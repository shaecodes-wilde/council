import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import contracts as c


class ContractsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
        return path

    def test_strict_json_duplicate_nonfinite_and_fenced_last(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e400}', '{'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.read_json(self.write('bad.json', text))
        path = self.write('answer.md', '```json\n{"old":1}\n```\n```json\n{"new":2}\n```')
        self.assertEqual(c.last_json(path), {'new': 2})
        for text in ('no JSON', '```json\n[]\n```', '```json\n{\n```'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                c.last_json(self.write('answer.md', text))

    def test_atomic_replace_interruption_preserves_previous_and_cleans_temp(self):
        path = self.root / 'state.json'
        c.atomic_json(path, {'old': True})
        before = path.read_bytes()
        with patch.object(c.os, 'replace', side_effect=OSError('simulated interrupted replace')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                c.atomic_json(path, {'new': True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [path])
        with self.assertRaises(ValueError):
            c.atomic_json(path, {'bad': float('nan')})
        self.assertEqual(path.read_bytes(), before)

    def test_paths_reject_traversal_absolute_and_all_symlinks(self):
        regular = self.write('inside/file', 'content')
        (self.root / 'link').symlink_to(self.root / 'inside', target_is_directory=True)
        for name in ('../outside', str(regular), 'link/file', 'inside/../inside/file', '.', 'x\\y'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.safe_path(self.root, name)
        self.assertEqual(c.safe_path(self.root, 'inside/file'), regular)
        with self.assertRaises(ValueError):
            c.atomic_json(self.root / 'link', {})

    def test_ballot_rejects_shape_identity_and_numeric_edges(self):
        good = {'ranking': ['B', 'C'], 'scores': {x: {'quality': 8.5} for x in 'ABC'}}
        self.assertIs(c.validate_ballot(good, 'A', list('ABC'), ['quality']), good)
        bads = [[], {}, {**good, 'ranking': None}, {**good, 'ranking': ['B', 'B']},
                {**good, 'ranking': ['A', 'B']}, {**good, 'voter': 'B'},
                {**good, 'scores': {'A': {'quality': 8}}}]
        for value in (True, 0, 11, 1.25, float('nan'), float('inf'), 10**400, '8', None):
            bad = copy.deepcopy(good)
            bad['scores']['C']['quality'] = value
            bads.append(bad)
        for bad in bads:
            with self.subTest(bad=str(bad)[:120]), self.assertRaises(ValueError):
                c.validate_ballot(bad, 'A', list('ABC'), ['quality'])
        for value in (1, 1.5, 10):
            good['scores']['A']['quality'] = value
            c.validate_ballot(good, 'A', list('ABC'), ['quality'])

    def test_model_aliases_unknown_and_small_rosters(self):
        manifest = {'vote': {'roster': list('ABC')}, 'seats': [
            {'letter': x, 'active': True, 'model_identity': 'model-' + x} for x in 'ABC']}
        self.assertEqual(c.validate_roster(manifest), list('ABC'))
        for identity in ('model-A', 'unknown', 'default', ''):
            bad = copy.deepcopy(manifest)
            bad['seats'][1]['model_identity'] = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                c.validate_roster(bad)
        with self.assertRaises(ValueError):
            c.validate_roster(manifest, ['A', 'B'])

    def test_settled_requires_children_done_and_completed_result_available(self):
        for status in c.TERMINAL:
            with self.subTest(status=status):
                task = {'status': status, 'workState': 'result_available', 'hasPendingChildRuns': True}
                self.assertFalse(c.settled(task))
                task['hasPendingChildRuns'] = False
                self.assertTrue(c.settled(task))
        self.assertFalse(c.settled({'status': 'completed', 'workState': 'pending', 'hasPendingChildRuns': False}))

    def test_severity_uses_brief_enums_and_legacy_default_must(self):
        self.assertTrue(c.finding_blocks({}))
        self.assertFalse(c.finding_blocks({'severity': 'should'}))
        self.assertTrue(c.finding_blocks({'severity': 'should', 'hard_constraint': True}))
        self.assertFalse(c.finding_blocks({'hard_constraint': True, 'disposition': 'resolved'}))
        for severity in ('critical', 'must-fix', None, True, []):
            with self.subTest(severity=severity), self.assertRaises(ValueError):
                c.finding_blocks({'severity': severity})

    def test_verifier_objections_normalize_without_mutation_or_model_veto(self):
        verifications = {'B': {'verdict': 'block', 'remaining_blockers': [
            {'id': 'B-F2', 'issue': 'Unsupported objection'}]}, 'C': {'findings': {}}}
        before = copy.deepcopy(verifications)
        objections = c.evaluate_final_objections(verifications)
        self.assertEqual(objections[0], {
            'critic': 'B', 'id': 'B-F2', 'description': 'Unsupported objection',
            'severity': 'must', 'hard_constraint': False, 'disposition': 'unresolved',
            'evidence': [], 'rationale': '', 'blocking': True})
        assessment = {'critic': 'B', 'id': 'B-F2', 'disposition': 'rejected',
                      'evidence': [{'path': 'sources/api.md', 'sha256': 'digest'}],
                      'rationale': 'Frozen API contract contradicts the objection.'}
        resolved = c.evaluate_final_objections(verifications, [assessment])[0]
        self.assertFalse(resolved['blocking'])
        self.assertEqual(resolved['disposition'], 'rejected')
        self.assertEqual(verifications, before)
        for bad in ({**assessment, 'evidence': []}, {**assessment, 'rationale': ''},
                    {**assessment, 'id': 'unknown'}, {**assessment, 'disposition': 'wontfix'},
                    {**assessment, 'evidence': ['unsupported assertion']}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                c.evaluate_final_objections(verifications, [bad])
        with self.assertRaises(ValueError):
            c.evaluate_final_objections(verifications, [assessment, assessment])

    def test_verifier_verdict_and_severity_cannot_be_silently_ignored(self):
        self.assertEqual(c.evaluate_final_objections({'B': {'findings': {}}}), [])
        for verdict in ('block', 'ship-after-fixes'):
            objection = c.evaluate_final_objections({'B': {'verdict': verdict}})[0]
            self.assertEqual(objection['id'], 'verdict')
            self.assertTrue(objection['blocking'])
        data = {'verdict': 'block', 'remaining_blockers': [
            {'id': 'B-F2', 'issue': 'Improve naming', 'severity': 'should'}]}
        self.assertFalse(c.evaluate_final_objections({'B': data})[0]['blocking'])
        data['remaining_blockers'][0]['hard_constraint'] = True
        self.assertTrue(c.evaluate_final_objections({'B': data})[0]['blocking'])
        for bad in ({'verdict': 'ready'}, {'remaining_blockers': 'failure'},
                    {'remaining_blockers': [{}]}, {'remaining_blockers': ['failure']},
                    {'remaining_blockers': [{'id': 'F1', 'issue': 'Failure', 'severity': 'critical'}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                c.evaluate_final_objections({'B': bad})

    def manifest(self):
        task = {'task_id': 'task-A', 'stage': 'r1', 'letter': 'A', 'attempt': 1,
                'epoch': 1, 'status': 'completed', 'hasPendingChildRuns': False,
                'workState': 'result_available', 'output_path': '_moderator/raw/a.md',
                'target': {'providerInstanceId': 'p', 'model': 'a', 'options': {}}}
        raw = self.write(task['output_path'], 'original')
        published = self.write('r1/A.md', 'anonymous')
        task['raw_sha256'] = c.sha256(raw)
        return {'vote': {'epoch': 1}, 'seats': [{'letter': 'A', 'active': True, **task['target']}],
                'tasks': {'req': task}, 'accepted': {'r1': {'A': {
                    'path': 'r1/A.md', 'sha256': c.sha256(published), 'request_id': 'req', 'epoch': 1}}}}

    def test_acceptance_provenance_and_raw_published_integrity(self):
        original = self.manifest()
        self.assertEqual(c.accepted_paths(self.root, original, 'r1'), {'A': self.root / 'r1/A.md'})
        changes = [('status', 'failed'), ('status', 'running'), ('hasPendingChildRuns', True),
                   ('workState', 'pending'), ('superseded', True), ('task_id', None)]
        for key, value in changes:
            manifest = copy.deepcopy(original)
            manifest['tasks']['req'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                c.accepted_paths(self.root, manifest, 'r1')
        bad = copy.deepcopy(original)
        bad['accepted']['r1']['A']['epoch'] = 2
        with self.assertRaises(ValueError):
            c.accepted_paths(self.root, bad, 'r1')
        self.write('r1/A.md', 'tampered')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            c.accepted_paths(self.root, original, 'r1')
        original = self.manifest()
        self.write('_moderator/raw/a.md', 'tampered')
        with self.assertRaisesRegex(ValueError, 'raw output hash'):
            c.accepted_paths(self.root, original, 'r1')

    def test_epoch_target_and_dependency_changes_reject_stale(self):
        manifest = self.manifest()
        task = manifest['tasks']['req']
        task['stage'] = 'r2'
        manifest['accepted']['r2'] = manifest['accepted'].pop('r1')
        manifest['vote']['epoch'] = 2
        with self.assertRaisesRegex(ValueError, 'stale'):
            c.accepted_paths(self.root, manifest, 'r2')
        manifest['vote']['epoch'] = 1
        manifest['seats'][0]['model'] = 'replacement'
        with self.assertRaisesRegex(ValueError, 'changed'):
            c.accepted_paths(self.root, manifest, 'r2')
        manifest = self.manifest()
        manifest['sources'] = []
        manifest['tasks']['req']['dependencies'] = {'sources': [], 'accepted': {}}
        manifest['sources'] = [{'path': 'changed', 'sha256': 'x'}]
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            c.accepted_paths(self.root, manifest, 'r1')


if __name__ == '__main__':
    unittest.main()

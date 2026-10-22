"""Regression coverage for manifest-only, symmetric Council elections."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import tally
from contracts import atomic_json, read_json


class RunFixture:
    def make_run(self, roster='ABCD', dimensions=('Correctness',)):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.root = root
        self.roster = list(roster)
        self.dimensions = list(dimensions)
        self.manifest = {
            'schema_version': 1, 'run_id': 'mechanics-test', 'owner_thread_id': 'owner',
            'created_at': '2026-10-22T00:00:00Z', 'topic': 'Regression fixture',
            'stage': 'r3', 'status': 'running', 'readiness': 'pending',
            'rubric': [{'name': d, 'definition': 'Quality', 'low': 'Fails',
                        'mid': 'Adequate', 'high': 'Excellent', 'direction': 'higher',
                        'weight': 1} for d in dimensions],
            'seats': [{'letter': v, 'providerInstanceId': 'test', 'model': f'model-{v}',
                       'model_identity': f'model-{v}', 'options': {}, 'active': True}
                      for v in roster],
            'sources': [], 'vote': {'epoch': 1, 'roster': list(roster), 'status': 'pending'},
            'tasks': {}, 'accepted': {}, 'blockers': [],
        }
        for voter in roster:
            self.artifact('r2', voter, f'Proposal {voter}\n')
            self.ballot(voter, self.valid_ballot(voter))
        self.save()
        return root

    def save(self):
        atomic_json(self.root / '_moderator/run.json', self.manifest)

    def artifact(self, stage, letter, text, *, request=None, accepted=True):
        request = request or f'{stage}-{letter}'
        raw_relative = f'_moderator/raw/{request}.md'
        published_relative = f'{stage}/{request}.md'
        raw = self.root / raw_relative
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(text.encode('utf-8'))
        published = self.root / published_relative
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_bytes(raw.read_bytes())
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        seat = next(s for s in self.manifest['seats'] if s['letter'] == letter)
        self.manifest['tasks'][request] = {
            'task_id': f'task-{request}', 'stage': stage, 'letter': letter,
            'attempt': 1, 'epoch': 1, 'status': 'completed',
            'output_path': raw_relative, 'raw_sha256': digest,
            'target': {k: seat[k] for k in ('providerInstanceId', 'model', 'options')},
            'hasPendingChildRuns': False, 'workState': 'result_available',
        }
        if accepted:
            self.manifest['accepted'].setdefault(stage, {})[letter] = {
                'path': published_relative, 'sha256': digest, 'request_id': request, 'epoch': 1}
        return request

    def valid_ballot(self, voter):
        return {'scores': {p: {d: 8 for d in self.dimensions} for p in self.roster},
                'ranking': [p for p in self.roster if p != voter]}

    def ballot(self, voter, ballot):
        self.artifact('r3', voter, '```json\n' + json.dumps(ballot) + '\n```\n')
        self.save()

    def cli(self, script='tally.py', *args):
        return subprocess.run([sys.executable, '-I', str(SCRIPTS / script), str(self.root), *args],
                              capture_output=True, text=True, check=False)

    def run_cli(self, command, *args):
        return subprocess.run([sys.executable, '-I', str(SCRIPTS / 'run.py'), command,
                               str(self.root), '--owner-thread', 'owner', *args],
                              capture_output=True, text=True, check=False)


class TallyTests(RunFixture, unittest.TestCase):
    def test_known_borda_and_provenance(self):
        self.make_run()
        result = tally.tally(self.root)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['winner'], 'A')
        self.assertEqual({r['letter']: r['borda'] for r in result['proposals']},
                         {'A': 9, 'B': 7, 'C': 5, 'D': 3})
        self.assertEqual(result['borda_max'], 9)
        self.assertEqual(result['provenance'], self.manifest['accepted']['r3'])
        self.assertEqual(result['proposal_provenance'], self.manifest['accepted']['r2'])
        cli = self.cli()
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(read_json(self.root / 'r3/tally.json'), result)

    def test_peer_averages_ranges_exclude_self(self):
        self.make_run('ABC', ('Correctness', 'Clarity'))
        for voter, scores in zip('ABC', ((10, 10), (6, 4), (8, 5))):
            ballot = self.valid_ballot(voter)
            ballot['scores']['A'] = dict(zip(self.dimensions, scores))
            self.ballot(voter, ballot)
        a = next(r for r in tally.tally(self.root)['proposals'] if r['letter'] == 'A')
        self.assertEqual(a['self'], 20)
        self.assertEqual(a['peer_avg'], 11.5)
        self.assertEqual(a['peer_range'], [10, 13])
        self.assertEqual(a['peer_dim_avg'], {'Correctness': 7, 'Clarity': 4.5})
        self.assertEqual(a['peer_dim_range'], {'Correctness': [6, 8], 'Clarity': [4, 5]})
        self.assertEqual(a['peer_count'], 2)

    def test_valid_tie_has_success_exit_and_distinct_status(self):
        self.make_run('ABC')
        for voter, ranking in {'A': ['B', 'C'], 'B': ['C', 'A'], 'C': ['A', 'B']}.items():
            ballot = self.valid_ballot(voter)
            ballot['ranking'] = ranking
            self.ballot(voter, ballot)
        result = tally.tally(self.root)
        self.assertEqual(result['status'], 'tied')
        self.assertIsNone(result['winner'])
        self.assertEqual(result['tied'], ['A', 'B', 'C'])
        cli = self.cli()
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn('valid tie', cli.stdout)

    def assert_no_election(self, result, status):
        self.assertEqual(result['status'], status)
        self.assertIsNone(result['winner'])
        self.assertEqual(result['tied'], [])
        self.assertEqual(result['proposals'], [])

    def test_baseline_empty_excessive_and_boolean_scores(self):
        self.make_run()
        for value in ({}, 1000, True):
            with self.subTest(value=value):
                for voter in self.roster:
                    ballot = self.valid_ballot(voter)
                    ballot['scores'] = {} if value == {} else {
                        p: {'Correctness': value} for p in self.roster}
                    self.ballot(voter, ballot)
                result = tally.tally(self.root)
                self.assert_no_election(result, 'invalid')
                self.assertEqual(set(result['errors']), set(self.roster))
                cli = self.cli()
                self.assertEqual(cli.returncode, 1, cli.stderr)
                self.assertNotIn('Traceback', cli.stderr)

    def test_baseline_null_ranking_and_all_invalid(self):
        self.make_run()
        for ranking in (None, list('ABCD')):
            for voter in self.roster:
                ballot = self.valid_ballot(voter)
                ballot['ranking'] = ranking
                self.ballot(voter, ballot)
            result = tally.tally(self.root)
            self.assert_no_election(result, 'invalid')
            self.assertEqual(set(result['errors']), set(self.roster))
            self.assertEqual(self.cli().returncode, 1)

    def test_single_voter_no_voters_and_dropout_cannot_win(self):
        self.make_run()
        for voter, ranking in {'A': ['B', 'D', 'C'], 'B': ['C', 'D', 'A'],
                               'C': ['A', 'D', 'B']}.items():
            ballot = self.valid_ballot(voter)
            ballot['ranking'] = ranking
            self.ballot(voter, ballot)
        for kept in ('ABC', 'A', ''):
            with self.subTest(kept=kept):
                self.manifest['accepted']['r3'] = {
                    v: a for v, a in self.manifest['accepted']['r3'].items() if v in kept}
                self.save()
                result = tally.tally(self.root)
                self.assert_no_election(result, 'incomplete')
                self.assertEqual({m['letter'] for m in result['missing']}, set('ABCD') - set(kept))
                self.assertEqual(self.cli().returncode, 1)

    def test_malformed_ballots_report_errors_without_batch_crashes(self):
        self.make_run()
        invalid = [None, [], 1, 'ballot', {'ranking': 3, 'scores': {}},
                   {'ranking': {}, 'scores': {}}, {'ranking': ['B', 'C', {}], 'scores': {}},
                   {'ranking': ['B', 'B', 'D'], 'scores': {}},
                   {'ranking': ['B', 'C', 'Z'], 'scores': {}}]
        for ballot in invalid:
            with self.subTest(ballot=ballot):
                self.ballot('A', ballot)
                result = tally.tally(self.root)
                self.assert_no_election(result, 'invalid')
                self.assertIn('A', result['errors'])

    def test_strict_score_shape_and_half_steps(self):
        self.make_run()
        for value in (False, '8', None, -1, 0, 10.5, 8.25, float('inf'), float('nan'), 10**400):
            with self.subTest(value=value):
                ballot = self.valid_ballot('A')
                ballot['scores']['A']['Correctness'] = value
                self.ballot('A', ballot)
                result = tally.tally(self.root)
                self.assert_no_election(result, 'invalid')
                self.assertIn('A', result['errors'])
        for mutation in ('missing_self', 'extra_candidate', 'missing_dimension', 'extra_dimension', 'not_object'):
            with self.subTest(mutation=mutation):
                ballot = self.valid_ballot('A')
                if mutation == 'missing_self':
                    del ballot['scores']['A']
                elif mutation == 'extra_candidate':
                    ballot['scores']['Z'] = {'Correctness': 8}
                elif mutation == 'missing_dimension':
                    ballot['scores']['B'] = {}
                elif mutation == 'extra_dimension':
                    ballot['scores']['B']['Novelty'] = 8
                else:
                    ballot['scores'] = []
                self.ballot('A', ballot)
                self.assert_no_election(tally.tally(self.root), 'invalid')
        ballot = self.valid_ballot('A')
        ballot['scores']['A']['Correctness'] = 8.5
        self.ballot('A', ballot)
        self.assertEqual(tally.tally(self.root)['status'], 'complete')

    def test_duplicate_json_keys_malformed_json_and_wrong_identity(self):
        self.make_run()
        for text in ('{"scores":{},"scores":{},"ranking":[]}', '{broken', '[]',
                     json.dumps({**self.valid_ballot('A'), 'voter': 'Z'})):
            with self.subTest(text=text):
                self.artifact('r3', 'A', '```json\n' + text + '\n```\n')
                self.save()
                result = tally.tally(self.root)
                self.assert_no_election(result, 'invalid')
                self.assertIn('A', result['errors'])

    def test_unknown_accepted_identity_missing_proposal_and_stale_files(self):
        self.make_run()
        (self.root / 'r3/Z.md').write_text('garbage')
        self.assertEqual(tally.tally(self.root)['winner'], 'A')
        del self.manifest['accepted']['r2']['D']
        self.save()
        self.assert_no_election(tally.tally(self.root), 'incomplete')
        self.manifest['vote']['roster'] = list('ABC')
        self.save()
        self.assert_no_election(tally.tally(self.root), 'invalid')

    def test_tamper_stale_epoch_failed_tasks_and_changed_models(self):
        self.make_run()
        original = copy.deepcopy(self.manifest)
        for mutation in ('hash', 'epoch', 'failed', 'pending_children', 'target', 'superseded'):
            with self.subTest(mutation=mutation):
                self.manifest = copy.deepcopy(original)
                task = self.manifest['tasks']['r3-A']
                if mutation == 'hash':
                    self.manifest['accepted']['r3']['A']['sha256'] = '0' * 64
                elif mutation == 'epoch':
                    self.manifest['vote']['epoch'] = 2
                elif mutation == 'failed':
                    task['status'] = 'failed'
                elif mutation == 'pending_children':
                    task['hasPendingChildRuns'] = True
                elif mutation == 'target':
                    task['target']['model'] = 'old-model'
                else:
                    task['superseded'] = True
                self.save()
                self.assert_no_election(tally.tally(self.root), 'invalid')

    def test_minimum_distinct_verified_models(self):
        self.make_run()
        for mutation in ('two_seats', 'duplicate', 'unknown'):
            original = copy.deepcopy(self.manifest)
            if mutation == 'two_seats':
                self.manifest['vote']['roster'] = list('AB')
            elif mutation == 'duplicate':
                self.manifest['seats'][1]['model_identity'] = 'MODEL-A'
            else:
                del self.manifest['seats'][1]['model_identity']
            self.save()
            result = tally.tally(self.root)
            self.assert_no_election(result, 'invalid')
            self.assertIn('roster', result['errors'])
            self.manifest = original

    def test_cli_rejects_missing_manifest_and_bad_usage(self):
        self.make_run()
        (self.root / '_moderator/run.json').unlink()
        cli = self.cli()
        self.assertEqual(cli.returncode, 2)
        self.assertNotIn('Traceback', cli.stderr)
        cli = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'tally.py')],
                             text=True, capture_output=True)
        self.assertEqual(cli.returncode, 2)

    def test_tally_cannot_overwrite_raw_or_follow_output_symlink(self):
        self.make_run()
        raw = self.root / self.manifest['tasks']['r3-A']['output_path']
        before = raw.read_bytes()
        (self.root / 'r3/tally.json').symlink_to(raw)
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(raw.read_bytes(), before)

    def test_registration_recomputes_outcome_and_protects_accepted_tally(self):
        self.make_run()
        self.assertEqual(self.cli().returncode, 0)
        registered = self.run_cli('record-tally', '--artifact', 'r3/tally.json')
        self.assertEqual(registered.returncode, 0, registered.stderr)
        manifest = read_json(self.root / '_moderator/run.json')
        self.assertEqual(manifest['vote']['status'], 'complete')
        self.assertEqual(manifest['vote']['winner'], 'A')
        before = (self.root / 'r3/tally.json').read_bytes()
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual((self.root / 'r3/tally.json').read_bytes(), before)
        forged = read_json(self.root / 'r3/tally.json')
        forged['winner'] = 'D'
        atomic_json(self.root / 'r3/tally.json', forged)
        rejected = self.run_cli('record-tally', '--artifact', 'r3/tally.json')
        self.assertEqual(rejected.returncode, 2, rejected.stdout)
        self.assertIn('independently computed', rejected.stderr)
        self.assertEqual(read_json(self.root / '_moderator/run.json')['vote']['winner'], 'A')
        self.assertEqual(self.cli().returncode, 2)

    def test_output_cannot_collide_with_manifest_raw_path(self):
        self.make_run()
        result = tally.tally(self.root)
        atomic_json(self.root / 'r3/tally.json', result)
        request = self.artifact('r1', 'A', 'raw original', accepted=False)
        self.manifest['tasks'][request]['output_path'] = 'r3/tally.json'
        self.save()
        before = (self.root / 'r3/tally.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'immutable raw'):
            tally.save_tally(self.root, result)
        self.assertEqual((self.root / 'r3/tally.json').read_bytes(), before)

    def test_tally_cannot_overwrite_frozen_evidence(self):
        self.make_run()
        output = self.root / 'r3/tally.json'
        output.write_text('Original frozen evidence, not a tally', encoding='utf-8')
        self.manifest['sources'] = [{'path': 'r3/tally.json',
                                     'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}]
        self.save()
        before = output.read_bytes()
        result = self.cli()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(output.read_bytes(), before)

    def test_atomic_tally_output_preserves_hardlinked_evidence_bytes(self):
        self.make_run()
        evidence = self.root / 'materials/evidence.json'
        evidence.parent.mkdir()
        evidence.write_text('Original frozen evidence', encoding='utf-8')
        self.manifest['sources'] = [{'path': 'materials/evidence.json',
                                     'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]
        self.save()
        (self.root / 'r3/tally.json').hardlink_to(evidence)
        before = evidence.read_bytes()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence.read_bytes(), before)
        self.assertFalse((self.root / 'r3/tally.json').samefile(evidence))

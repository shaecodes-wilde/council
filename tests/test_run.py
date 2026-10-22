from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import contracts as c
import run


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.seats = [{'letter': x, 'providerInstanceId': 'provider-' + x,
                       'model': 'model-' + x, 'options': {}, 'active': True,
                       'model_identity': 'verified-model-' + x} for x in 'ABC']
        self.rubric = [{'name': 'quality', 'definition': 'works', 'low': 'fails',
                        'mid': 'some gaps', 'high': 'complete', 'direction': 'higher', 'weight': 1}]
        c.atomic_json(self.parent / 'seats.json', self.seats)
        c.atomic_json(self.parent / 'rubric.json', self.rubric)
        result = self.cli('init', str(self.parent), '--topic', 'Same topic', '--seats',
                          str(self.parent / 'seats.json'), '--rubric', str(self.parent / 'rubric.json'))
        self.root = Path(result['run'])

    def cli(self, command, *args, ok=True, owner='thread-1'):
        argv = [sys.executable, '-I', str(SCRIPTS / 'run.py'), command, *map(str, args)]
        if command != 'status':
            argv.extend(['--owner-thread', owner])
        result = subprocess.run(argv, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        return result.stderr

    def manifest(self):
        return c.load_manifest(self.root)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def write_json(self, relative, value):
        c.atomic_json(self.root / relative, value)
        return relative

    def advance(self):
        return self.cli('advance', self.root)

    def output(self, stage, letter, text='A complete anonymous proposal.', attempt=1):
        intent = self.cli('intent', self.root, '--stage', stage, '--letter', letter, '--attempt', attempt)
        key = intent['request_id']
        self.write(intent['output_path'], text)
        self.cli('record-task', self.root, key, '--task-id', key + '-task', '--status', 'completed',
                 '--work-state', 'result_available')
        relative = f'{stage}/{letter}-{attempt}.md'
        self.write(relative, text)
        self.cli('accept', self.root, key, '--published', relative)
        return key

    def r3(self, tied=False):
        self.advance()
        for stage in ('r1', 'r2'):
            for letter in 'ABC':
                self.output(stage, letter)
            self.advance()
        rankings = {'A': ['B', 'C'], 'B': ['A', 'C'], 'C': ['A', 'B']}
        if tied:
            rankings = {'A': ['B', 'C'], 'B': ['C', 'A'], 'C': ['A', 'B']}
        for letter in 'ABC':
            ballot = {'ranking': rankings[letter], 'scores': {x: {'quality': 8} for x in 'ABC'}}
            self.output('r3', letter, '```json\n' + json.dumps(ballot) + '\n```')
        import tally
        document = tally.tally(self.root)
        self.write_json('r3/tally.json', document)
        self.cli('record-tally', self.root, '--artifact', 'r3/tally.json')
        return document

    def verify_stage(self, tied=False, finding=False, finding_metadata=None, verifier_extra=None, repair_actor=None, skip_verifier=None):
        self.r3(tied=tied)
        if tied:
            self.cli('draw-author', self.root, '--seed', 'repeatable')
        self.advance()
        author = self.manifest()['vote']['author']
        critics = [x for x in 'ABC' if x != author]
        self.output('synthesis', author, 'The final complete plan with all required sections.')
        self.advance()
        for letter in critics:
            self.output('redteam', letter, 'F1: documented hard requirement fails.' if finding else 'No findings.')
        evidence = [{k: v for k, v in self.manifest()['accepted']['redteam'][critics[0]].items() if k in ('path', 'sha256')}]
        findings = [{'id': 'F1', 'description': 'Documented requirement fails', 'hard_constraint': True,
                     'evidence': evidence, **(finding_metadata or {})}] if finding else []
        self.write_json('findings.json', findings)
        self.cli('record-findings', self.root, '--artifact', 'findings.json')
        self.advance()
        if repair_actor:
            self.write_json('repair-actor.json', repair_actor)
            self.cli('replace-actor', self.root, '--stage', 'repair', '--letter', author,
                     '--seat', self.root / 'repair-actor.json', '--reason', 'repair quota')
        if finding:
            self.output('repair', author, 'The revised plan still has a documented blocker.')
        else:
            self.cli('no-repair', self.root)
        self.advance()
        manifest = self.manifest()
        pointer = manifest['final_plan']
        artifact = manifest['accepted'][pointer['stage']][pointer['letter']]
        digest = artifact['sha256']
        for letter in critics:
            if letter == skip_verifier:
                continue
            data = {'final_sha256': digest, 'findings': {'F1': {'disposition': 'unresolved', 'verified': False}} if finding else {}}
            data.update((verifier_extra or {}).get(letter, {}))
            self.output('verify', letter, '```json\n' + json.dumps(data) + '\n```')
        final = {'final_sha256': digest, 'sections': {key: f'Plan section {key}' for key in run.FINAL_SECTIONS},
                 'user_decisions': ['Choose tied direction'] if tied else [],
                 'findings': [{'id': 'F1', 'disposition': 'unresolved', 'evidence': evidence}] if finding else []}
        if tied:
            final['tied_alternatives'] = manifest['vote']['tied']
        self.write_json('final-check.json', final)
        return final

    def test_unique_runs_owner_resume_and_lost_launch_response(self):
        second = self.cli('init', self.parent, '--topic', 'Same topic', '--seats', self.parent / 'seats.json', '--rubric', self.parent / 'rubric.json')
        self.assertNotEqual(second['run'], str(self.root))
        self.advance()
        first = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A')
        second = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A')
        self.assertEqual(first, second)
        status = self.cli('resume', self.root)
        self.assertEqual(status['reconcile_tasks'][0]['action'], 'retry-same-request-id')
        self.cli('resume', self.root, ok=False, owner='wrong-thread')
        self.assertEqual(self.cli('status', self.root)['owner_thread_id'], 'thread-1')

    def test_failed_nonterminal_pending_child_and_task_id_mismatch(self):
        self.advance()
        task = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A')
        key = task['request_id']
        self.write(task['output_path'], 'raw')
        self.write('r1/A.md', 'published')
        self.cli('record-task', self.root, key, '--task-id', 'id-a', '--status', 'running', '--work-state', 'pending')
        self.cli('accept', self.root, key, '--published', 'r1/A.md', ok=False)
        self.cli('record-task', self.root, key, '--task-id', 'different', '--status', 'failed', '--work-state', 'pending', ok=False)
        self.cli('record-task', self.root, key, '--task-id', 'id-a', '--status', 'completed', '--work-state', 'result_available', '--pending-children')
        self.cli('accept', self.root, key, '--published', 'r1/A.md', ok=False)
        self.cli('record-task', self.root, key, '--task-id', 'id-a', '--status', 'completed', '--work-state', 'result_available')
        self.cli('accept', self.root, key, '--published', 'r1/A.md')
        self.cli('record-task', self.root, key, '--task-id', 'id-a', '--status', 'failed', '--work-state', 'pending', ok=False)
        task = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'B')
        key = task['request_id']
        self.cli('record-task', self.root, key, '--task-id', 'id-b', '--status', 'failed', '--work-state', 'pending')
        self.cli('accept', self.root, key, '--published', 'r1/A.md', ok=False)
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'B', '--attempt', 2)
        self.cli('record-task', self.root, key, '--task-id', 'id-b', '--status', 'completed', '--work-state', 'result_available', ok=False)

    def test_duplicate_delivery_immutable_bytes_and_bad_paths(self):
        self.advance()
        key = self.output('r1', 'A')
        before = self.manifest()
        self.cli('record-task', self.root, key, '--task-id', key + '-task', '--status', 'completed', '--work-state', 'result_available')
        self.cli('accept', self.root, key, '--published', 'r1/A-1.md')
        self.assertEqual(before, self.manifest())
        self.write('r1/A-1.md', 'changed publication')
        self.cli('accept', self.root, key, '--published', 'r1/A-1.md', ok=False)
        self.cli('accept', self.root, key, '--published', '../outside', ok=False)
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'B', '--output', '/outside', ok=False)
        (self.root / 'linked').symlink_to(self.root / 'r1', target_is_directory=True)
        self.cli('accept', self.root, key, '--published', 'linked/A-1.md', ok=False)
        self.write(before['tasks'][key]['output_path'], 'changed raw')
        self.assertIn('hash', self.cli('accept', self.root, key, '--published', 'r1/A-1.md', ok=False))

    def test_barriers_freeze_hashes_and_minimum_panel(self):
        self.write('sources/code.txt', 'frozen')
        self.write_json('sources.json', [{'path': 'sources/code.txt', 'sha256': c.sha256(self.root / 'sources/code.txt')}])
        self.cli('freeze-sources', self.root, '--artifact', 'sources.json')
        self.advance()
        self.cli('advance', self.root, ok=False)
        self.write('sources/code.txt', 'changed')
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A', ok=False)
        self.cli('set-roster', self.root, '--roster', 'A', 'B', '--reason', 'exhausted')
        self.assertEqual(self.manifest()['status'], 'incomplete')
        self.cli('advance', self.root, ok=False)

    def test_replacement_keeps_other_r1_and_invalidates_callbacks(self):
        self.advance()
        keys = {x: self.output('r1', x) for x in 'ABC'}
        self.advance()
        late = self.cli('intent', self.root, '--stage', 'r2', '--letter', 'B')
        self.cli('record-task', self.root, late['request_id'], '--task-id', 'late',
                 '--status', 'cancelled', '--work-state', 'pending')
        new = {**self.seats[0], 'model': 'replacement', 'model_identity': 'verified-replacement'}
        self.write_json('replacement.json', new)
        self.cli('replace-seat', self.root, '--letter', 'A', '--seat', self.root / 'replacement.json', '--reason', 'quota exhausted')
        manifest = self.manifest()
        self.assertEqual(set(c.accepted_paths(self.root, manifest, 'r1')), {'B', 'C'})
        self.assertEqual(manifest['stage'], 'r1')
        self.assertEqual(manifest['vote']['epoch'], 2)
        self.cli('record-task', self.root, late['request_id'], '--task-id', 'late', '--status', 'completed', '--work-state', 'result_available', ok=False)
        self.cli('accept', self.root, keys['A'], '--published', 'r1/A-1.md', ok=False)
        self.output('r1', 'A', 'Replacement blind proposal')
        self.advance()
        self.assertEqual(self.manifest()['stage'], 'r2')

    def test_duplicate_completion_after_stage_advance_is_idempotent(self):
        self.advance()
        keys = {letter: self.output('r1', letter) for letter in 'ABC'}
        self.advance()
        before = self.manifest()
        self.cli('accept', self.root, keys['A'], '--published', 'r1/A-1.md')
        self.assertEqual(self.manifest(), before)

    def test_two_concurrent_writers_preserve_both_launch_intents(self):
        self.advance()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda letter: self.cli('intent', self.root, '--stage', 'r1', '--letter', letter), 'AB'))
        self.assertEqual(len(self.manifest()['tasks']), 2)
        self.assertEqual({x['letter'] for x in results}, {'A', 'B'})

    def test_reopened_ballot_stage_invalidates_dependent_epoch_and_artifacts(self):
        self.r3()
        self.cli('reopen', self.root, '--stage', 'r3', '--reason', 'material ballot input correction')
        manifest = self.manifest()
        self.assertEqual(manifest['stage'], 'r2')
        self.assertEqual(manifest['vote']['epoch'], 2)
        self.assertEqual(set(manifest['accepted']), {'r1'})
        self.assertEqual(set(c.accepted_paths(self.root, manifest, 'r1')), set('ABC'))
        for letter in 'ABC':
            self.output('r2', letter, 'Corrected complete proposal')
        self.advance()
        self.assertEqual(self.manifest()['stage'], 'r3')

    def test_replacement_rejects_duplicate_underlying_model(self):
        self.advance()
        replacement = {**self.seats[0], 'model': 'alias-for-B', 'model_identity': self.seats[1]['model_identity']}
        self.write_json('replacement.json', replacement)
        self.cli('replace-seat', self.root, '--letter', 'A', '--seat', self.root / 'replacement.json', '--reason', 'quota', ok=False)
        self.assertEqual(self.manifest()['vote']['epoch'], 1)

    def test_tally_redelivery_cannot_erase_or_redraw_tied_author(self):
        self.r3(tied=True)
        draw = self.cli('draw-author', self.root, '--seed', 'fixed-seed')
        self.cli('record-tally', self.root, '--artifact', 'r3/tally.json')
        self.assertEqual(self.manifest()['vote']['author_draw'], draw)
        self.cli('draw-author', self.root, '--seed', 'different-seed', ok=False)

    def test_post_vote_actor_substitution_preserves_voter_provenance(self):
        self.r3()
        elected = copy.deepcopy(self.manifest()['vote'])
        self.advance()
        self.output('synthesis', elected['author'])
        self.advance()
        original = copy.deepcopy(self.manifest()['seats'])
        actor = {**self.seats[1], 'model': 'critic-replacement', 'model_identity': 'verified-critic-replacement'}
        self.write_json('actor.json', actor)
        self.cli('replace-actor', self.root, '--stage', 'redteam', '--letter', 'B', '--seat', self.root / 'actor.json', '--reason', 'post-vote quota')
        self.assertEqual(self.manifest()['vote'], elected)
        self.assertEqual(self.manifest()['seats'], original)
        self.assertEqual(set(c.accepted_paths(self.root, self.manifest(), 'r3')), set('ABC'))
        key = self.output('redteam', 'B')
        manifest = self.manifest()
        self.assertEqual(manifest['tasks'][key]['target']['model'], 'critic-replacement')
        self.assertEqual(c.actor_seat(manifest, 'verify', 'B')['model'], 'critic-replacement')
        c.accepted_paths(self.root, manifest, 'redteam')

    def test_tally_forgery_and_registered_provenance(self):
        document = self.r3()
        self.assertEqual(self.manifest()['vote']['winner'], 'A')
        forged = copy.deepcopy(document)
        forged['winner'] = 'C'
        self.write_json('r3/forged.json', forged)
        self.cli('record-tally', self.root, '--artifact', 'r3/forged.json', ok=False)
        self.write('r3/A-1.md', 'changed ballot')
        self.cli('advance', self.root, ok=False)

    def test_no_finalization_without_complete_plan_and_verification(self):
        self.cli('finalize', self.root, ok=False)
        final = self.verify_stage()
        self.cli('finalize', self.root, ok=False)
        bad = {**final, 'final_sha256': 'wrong'}
        self.write_json('bad-final.json', bad)
        self.cli('record-final', self.root, '--artifact', 'bad-final.json', ok=False)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'ready')
        self.assertEqual(self.manifest()['status'], 'complete')
        self.assertGreaterEqual(self.manifest()['completed_at'], self.manifest()['created_at'])
        self.cli('advance', self.root, ok=False)
        self.cli('reopen', self.root, '--stage', 'verify', '--reason', 'correct final input')
        self.assertNotIn('completed_at', self.manifest())

    def test_pending_completed_task_stays_reconcilable_until_children_finish(self):
        self.advance()
        task = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A')
        key = task['request_id']
        self.write(task['output_path'], 'Result awaiting child work')
        self.cli('record-task', self.root, key, '--task-id', 'pending-A', '--status', 'completed',
                 '--work-state', 'result_available', '--pending-children')
        pending = self.cli('resume', self.root)['reconcile_tasks']
        self.assertEqual([item['request_id'] for item in pending], [key])
        self.assertTrue(pending[0]['hasPendingChildRuns'])
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A', '--attempt', 2, ok=False)
        self.cli('reopen', self.root, '--stage', 'r1', '--reason', 'pending child', ok=False)
        self.write_json('replacement.json', {**self.seats[0], 'model_identity': 'replacement'})
        self.cli('replace-seat', self.root, '--letter', 'A', '--seat', self.root / 'replacement.json',
                 '--reason', 'pending child', ok=False)
        self.assertFalse(self.manifest()['tasks'][key].get('superseded', False))
        self.cli('record-task', self.root, key, '--task-id', 'pending-A', '--status', 'completed',
                 '--work-state', 'result_available')
        self.assertEqual(self.cli('resume', self.root)['reconcile_tasks'][0]['request_id'], key)
        self.write('r1/A.md', 'Published result')
        self.cli('accept', self.root, key, '--published', 'r1/A.md')
        self.assertEqual(self.cli('resume', self.root)['reconcile_tasks'], [])

    def test_malformed_verification_can_be_repaired_without_reopening_vote(self):
        final = self.verify_stage(skip_verifier='B')
        before = copy.deepcopy(self.manifest()['vote'])
        invalid = ({'verdict': 'blocked'}, {'verdict': 'block', 'remaining_blockers': ['Unstructured blocker']})
        for attempt, extra in enumerate(invalid, 1):
            data = {'final_sha256': final['final_sha256'], 'findings': {}, **extra}
            text = '```json\n' + json.dumps(data) + '\n```'
            task = self.cli('intent', self.root, '--stage', 'verify', '--letter', 'B', '--attempt', attempt)
            self.write(task['output_path'], text)
            self.cli('record-task', self.root, task['request_id'], '--task-id', task['request_id'] + '-task',
                     '--status', 'completed', '--work-state', 'result_available')
            path = f'verify/B-invalid-{attempt}.md'
            self.write(path, text)
            self.cli('accept', self.root, task['request_id'], '--published', path, ok=False)
            self.assertNotIn('B', self.manifest()['accepted']['verify'])
            self.assertEqual(self.manifest()['vote'], before)
        good = {'final_sha256': final['final_sha256'], 'findings': {}, 'verdict': 'ship', 'remaining_blockers': []}
        self.output('verify', 'B', '```json\n' + json.dumps(good) + '\n```', attempt=3)
        self.assertEqual(self.manifest()['vote'], before)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        self.assertEqual(self.cli('finalize', self.root)['readiness'], 'ready')

    def test_incomplete_completed_and_interrupted_child_work_can_be_cancelled(self):
        self.advance()
        for status, letter in (('completed', 'A'), ('interrupted', 'B'), ('failed', 'C')):
            with self.subTest(status=status):
                task = self.cli('intent', self.root, '--stage', 'r1', '--letter', letter)
                key = task['request_id']
                self.cli('record-task', self.root, key, '--task-id', letter, '--status', status,
                         '--work-state', 'pending', '--pending-children')
                self.assertIn(key, [x['request_id'] for x in self.cli('resume', self.root)['reconcile_tasks']])
                self.cli('intent', self.root, '--stage', 'r1', '--letter', letter, '--attempt', 2, ok=False)
                self.cli('record-task', self.root, key, '--task-id', letter, '--status', 'cancelled',
                         '--work-state', 'pending')
                self.cli('intent', self.root, '--stage', 'r1', '--letter', letter, '--attempt', 2)

    def test_legacy_superseded_pending_work_remains_reconcilable(self):
        self.advance()
        task = self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A')
        key = task['request_id']
        self.cli('record-task', self.root, key, '--task-id', 'old-A', '--status', 'completed',
                 '--work-state', 'pending', '--pending-children')
        legacy = self.manifest()
        legacy['tasks'][key]['superseded'] = True
        c.atomic_json(self.root / '_moderator/run.json', legacy)
        self.assertEqual(self.cli('resume', self.root)['reconcile_tasks'][0]['request_id'], key)
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A', '--attempt', 2, ok=False)
        self.cli('record-task', self.root, key, '--task-id', 'old-A', '--status', 'cancelled',
                 '--work-state', 'pending')
        self.assertEqual(self.cli('resume', self.root)['reconcile_tasks'], [])
        self.cli('intent', self.root, '--stage', 'r1', '--letter', 'A', '--attempt', 2)

    def test_actual_substitute_author_cannot_review_own_plan(self):
        self.r3()
        elected = copy.deepcopy(self.manifest()['vote'])
        self.advance()
        actor = {**self.seats[0], 'model': 'fallback-X', 'model_identity': 'verified-X'}
        self.write_json('author.json', actor)
        self.cli('replace-actor', self.root, '--stage', 'synthesis', '--letter', 'A',
                 '--seat', self.root / 'author.json', '--reason', 'quota')
        self.output('synthesis', 'A')
        self.advance()
        self.write_json('critic.json', {**actor, 'letter': 'B'})
        self.cli('replace-actor', self.root, '--stage', 'redteam', '--letter', 'B',
                 '--seat', self.root / 'critic.json', '--reason', 'quota', ok=False)
        self.assertEqual(self.manifest()['vote'], elected)
        self.assertNotIn('redteam', self.manifest()['stage_actors'])

    def test_actor_replacement_checks_peers_across_redteam_and_verify(self):
        self.verify_stage()
        actor = {**self.seats[1], 'model': 'fallback-X', 'model_identity': 'verified-X'}
        self.write_json('critic.json', actor)
        self.cli('replace-actor', self.root, '--stage', 'verify', '--letter', 'B',
                 '--seat', self.root / 'critic.json', '--reason', 'quota')
        # C may not take B's historical redteam identity even though B now verifies as X.
        self.write_json('critic.json', {**self.seats[1], 'letter': 'C'})
        self.cli('replace-actor', self.root, '--stage', 'verify', '--letter', 'C',
                 '--seat', self.root / 'critic.json', '--reason', 'quota', ok=False)
        self.write_json('critic.json', {**self.seats[0], 'letter': 'C'})
        self.cli('replace-actor', self.root, '--stage', 'verify', '--letter', 'C',
                 '--seat', self.root / 'critic.json', '--reason', 'self review', ok=False)

    def test_actual_repair_author_cannot_become_a_verifier(self):
        actor = {**self.seats[0], 'model': 'repair-X', 'model_identity': 'verified-X'}
        self.verify_stage(finding=True, repair_actor=actor)
        elected = copy.deepcopy(self.manifest()['vote'])
        self.write_json('critic.json', {**actor, 'letter': 'B'})
        self.cli('replace-actor', self.root, '--stage', 'verify', '--letter', 'B',
                 '--seat', self.root / 'critic.json', '--reason', 'quota', ok=False)
        self.assertEqual(self.manifest()['vote'], elected)
        self.assertEqual(self.manifest()['final_plan']['stage'], 'repair')

    def test_new_verifier_blocker_cannot_finalize_ready(self):
        self.verify_stage(verifier_extra={'B': {'verdict': 'block', 'remaining_blockers': [
            {'id': 'B-F2', 'issue': 'Reproduced data loss violates R1',
             'evidence': 'Restart loses data', 'requirements': ['R1']}]}})
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'blocked')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['blockers'][0]['finding_id'], 'B-F2')
        self.assertEqual(self.manifest()['stage'], 'complete')

    def test_moderator_can_reject_unsupported_verifier_objection_with_counterevidence(self):
        final = self.verify_stage(verifier_extra={'B': {'verdict': 'block', 'remaining_blockers': [
            {'id': 'B-F2', 'issue': 'Unsupported restart objection'}]}})
        final['verifier_objections'] = [{'critic': 'B', 'id': 'B-F2', 'disposition': 'rejected',
                                        'rationale': 'The accepted plan explicitly persists the job before ack.',
                                        'evidence': []}]
        self.write_json('final-check.json', final)
        self.cli('record-final', self.root, '--artifact', 'final-check.json', ok=False)
        ref = self.manifest()['accepted']['synthesis']['A']
        unregistered = self.write('unregistered.txt', 'Unsupported evidence')
        final['verifier_objections'][0]['evidence'] = [{'path': 'unregistered.txt', 'sha256': c.sha256(unregistered)}]
        self.write_json('final-check.json', final)
        self.cli('record-final', self.root, '--artifact', 'final-check.json', ok=False)
        final['verifier_objections'][0]['evidence'] = [{k: ref[k] for k in ('path', 'sha256')}]
        self.write_json('final-check.json', final)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        self.assertEqual(self.cli('finalize', self.root)['readiness'], 'ready')

    def test_unresolved_should_finding_is_visible_but_does_not_block(self):
        self.verify_stage(finding=True, finding_metadata={'severity': 'should', 'hard_constraint': False})
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        self.assertEqual(self.cli('finalize', self.root)['readiness'], 'ready')
        self.assertEqual(self.manifest()['findings'][0]['severity'], 'should')

    def test_hard_should_finding_cannot_be_downgraded_by_disposition(self):
        final = self.verify_stage(finding=True, finding_metadata={'severity': 'should'})
        final['findings'][0].update(hard_constraint=False, severity='should')
        self.write_json('final-check.json', final)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'blocked')
        self.assertTrue(result['blockers'][0]['hard_constraint'])

    def test_must_finding_cannot_be_downgraded_by_disposition(self):
        final = self.verify_stage(finding=True, finding_metadata={'hard_constraint': False})
        final['findings'][0]['severity'] = 'should'
        self.write_json('final-check.json', final)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'blocked')
        self.assertEqual(result['blockers'][0]['severity'], 'must')

    def test_tied_final_preserves_election_and_requires_user_choice(self):
        final = self.verify_stage(tied=True)
        self.assertEqual(self.manifest()['vote']['status'], 'tied')
        bad = {**final, 'user_decisions': []}
        self.write_json('bad-final.json', bad)
        self.cli('record-final', self.root, '--artifact', 'bad-final.json', ok=False)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'needs-user-decision')
        self.assertEqual(self.manifest()['vote']['status'], 'tied')
        self.assertNotIn('winner', self.manifest()['vote'])

    def test_evidence_backed_minority_finding_blocks_unique_winner(self):
        final = self.verify_stage(finding=True)
        self.cli('record-final', self.root, '--artifact', 'final-check.json')
        result = self.cli('finalize', self.root)
        self.assertEqual(result['readiness'], 'blocked')
        self.assertTrue(result['blockers'][0]['hard_constraint'])
        self.assertEqual(self.manifest()['vote']['status'], 'complete')
        self.assertEqual(self.manifest()['vote']['winner'], 'A')

    def test_unverified_resolution_cannot_be_ready_or_omit_findings(self):
        final = self.verify_stage(finding=True)
        final['findings'][0]['disposition'] = 'resolved'
        self.write_json('bad-final.json', final)
        self.cli('record-final', self.root, '--artifact', 'bad-final.json', ok=False)
        final['findings'] = []
        self.write_json('bad-final.json', final)
        self.cli('record-final', self.root, '--artifact', 'bad-final.json', ok=False)
        self.cli('finalize', self.root, ok=False)


if __name__ == '__main__':
    unittest.main()

"""Anonymization preserves evidence, data semantics, and immutable attempts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import scrub
from test_tally import RunFixture


class ScrubTextTests(unittest.TestCase):
    def test_baseline_attribution_is_preserved_lowercase_identity_is_flagged(self):
        original = ('As OpenAI recommends, use exponential backoff.\n'
                    'The tool was developed by Google.\n'
                    'Model identification: deepseek\n')
        text, findings = scrub.scrub_text(original)
        self.assertEqual(text, original)
        self.assertEqual(findings['redactions'], [])
        self.assertTrue(findings['review_required'])
        self.assertEqual([m['text'] for m in findings['mentions']], ['OpenAI', 'Google', 'deepseek'])
        self.assertEqual([m['line'] for m in findings['mentions']], [1, 2, 3])

    def test_narrow_first_person_disclosures_are_scrubbed(self):
        original = ("I am Claude, and I recommend retries.\n"
                    "I'm GPT-6.1-sol.\n"
                    'I’m DeepSeek.\n'
                    'As Claude, I recommend retries.\n'
                    'Devin here, let us begin.\n'
                    'I was trained by Anthropic.\n')
        text, findings = scrub.scrub_text(original)
        self.assertEqual(len(findings['redactions']), 6)
        self.assertEqual(findings['mentions'], [])
        self.assertFalse(findings['review_required'])
        self.assertIn('recommend retries', text)
        self.assertIn('I was trained by [provider withheld].', text)
        self.assertNotIn('GPT', text)
        self.assertEqual(scrub.scrub_text(text)[0], text)

    def test_ambiguous_attribution_hypotheticals_and_ordinary_speech_are_flagged(self):
        original = ('As Claude explains, use retries.\n'
                    'As Claude, consider retries.\n'
                    'Devin here explains how retries work.\n'
                    'I am Google developer number four.\n'
                    'If I am Claude, I would retry.\n'
                    'The transcript says I am Claude.\n'
                    'This tool was made by OpenAI.\n'
                    'My model (deepseek) suggests a retry.\n')
        text, findings = scrub.scrub_text(original)
        self.assertEqual(text, original)
        self.assertTrue(findings['review_required'])
        self.assertEqual(findings['redactions'], [])

    def test_quotations_fenced_indented_and_inline_code_are_preserved(self):
        evidence = ('> I am Claude, and this is cited evidence.\n'
                    '"I am Claude."\n'
                    '“As Claude, I recommend retries.”\n'
                    "'I am DeepSeek.'\n"
                    '‘Devin here.’\n'
                    "'The source said:\nI'm Claude.\nI am DeepSeek.\n'\n"
                    '‘The source said:\nI’m Claude.\nI am DeepSeek.\n’\n'
                    '`I am GPT-6.1-sol.`\n'
                    '``I am Claude, `a literal backtick`.``\n'
                    '    I am Claude.\n'
                    '\tI am Claude.\n'
                    '```text\nI am Claude.\n```\n'
                    '~~~python\nI am Claude.\n~~~\n'
                    '[I am Claude.](https://example.test/evidence)\n'
                    '<pre>\nI am Claude.\n</pre>\n'
                    '<blockquote>\nI am Claude.\n</blockquote>\n')
        text, findings = scrub.scrub_text(evidence)
        self.assertEqual(text, evidence)
        self.assertTrue(findings['review_required'])
        self.assertTrue(all(m['protected'] for m in findings['mentions']))
        self.assertEqual(findings['redactions'], [])

    def test_lazy_blockquote_continuations_remain_quoted(self):
        original = '> The source said:\nI am Claude.\n\nI am DeepSeek.\n'
        text, findings = scrub.scrub_text(original)
        self.assertEqual(text, '> The source said:\nI am Claude.\n\nI am [panelist].\n')
        self.assertEqual(len(findings['redactions']), 1)
        self.assertTrue(findings['mentions'][0]['protected'])

    def test_unclosed_fence_preserves_rest_as_evidence(self):
        original = '```text\nI am Claude.\n'
        self.assertEqual(scrub.scrub_text(original)[0], original)

    def test_json_proposals_are_byte_preserved_while_prose_can_change(self):
        payload = '{"proposal":"I am Claude, use this sentence verbatim.","OpenAI":8}'
        fenced = '```json\n' + payload + '\n```\n'
        original = 'I am Claude.\n' + fenced
        text, findings = scrub.scrub_text(original)
        self.assertIn(fenced, text)
        self.assertEqual(len(findings['redactions']), 1)
        self.assertTrue(findings['review_required'])
        self.assertEqual(json.loads(text.split('```json\n')[1].split('\n```')[0]), json.loads(payload))
        self.assertEqual(scrub.scrub_text(payload)[0], payload)
        malformed = '{"proposal": I am Claude.}'
        self.assertEqual(scrub.scrub_text(malformed)[0], malformed)

    def test_case_insensitive_residual_names(self):
        original = 'Sources: openai, deepseek, gpt-6.1, anthropic, DEVIN, opencode.\n'
        text, findings = scrub.scrub_text(original)
        self.assertEqual(text, original)
        self.assertEqual(len(findings['mentions']), 6)
        self.assertTrue(findings['review_required'])


class ScrubArtifactTests(RunFixture, unittest.TestCase):
    def task(self, original='I am Claude.\r\nEvidence from openai.\r\n', *, accepted=False):
        self.make_run()
        request = self.artifact('r1', 'A', original, request='r1-A', accepted=accepted)
        self.save()
        return request

    def test_cli_creates_separate_copy_and_report_without_mutating_raw_or_manifest(self):
        request = self.task()
        raw = self.root / self.manifest['tasks'][request]['output_path']
        before = raw.read_bytes()
        manifest_before = (self.root / '_moderator/run.json').read_bytes()
        cli = self.cli('scrub.py', '1', '--request-id', request)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        report = json.loads(cli.stdout)['artifacts'][0]
        self.assertEqual(raw.read_bytes(), before)
        self.assertEqual((self.root / '_moderator/run.json').read_bytes(), manifest_before)
        published = self.root / report['path']
        self.assertNotEqual(published, raw)
        self.assertEqual(published.read_bytes(), b'I am [panelist].\r\nEvidence from openai.\r\n')
        self.assertEqual(report['sha256'], hashlib.sha256(published.read_bytes()).hexdigest())
        self.assertEqual(report['raw_sha256'], hashlib.sha256(before).hexdigest())
        self.assertTrue(report['review_required'])
        saved_report = json.loads((self.root / report['report_path']).read_text())
        self.assertEqual(saved_report['path'], report['path'])
        again = self.cli('scrub.py', 'r1', '--request-id', request)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, cli.stdout)

    def test_no_implicit_glob_selection(self):
        request = self.task()
        stale = self.root / 'r1/stale.md'
        stale.parent.mkdir(exist_ok=True)
        stale.write_text('I am Claude.\n')
        cli = self.cli('scrub.py', 'r1')
        self.assertEqual(cli.returncode, 2)
        self.assertIn('--request-id', cli.stderr)
        self.assertEqual(stale.read_text(), 'I am Claude.\n')
        self.manifest['accepted']['r1'] = {'A': {
            'path': 'r1/r1-A.md', 'sha256': self.manifest['tasks'][request]['raw_sha256'],
            'request_id': request, 'epoch': 1}}
        self.save()
        reports = scrub.scrub(self.root, 'r1')
        self.assertEqual([r['request_id'] for r in reports], [request])

    def test_unsuccessful_nonterminal_stale_and_changed_tasks_are_rejected(self):
        request = self.task()
        original = copy.deepcopy(self.manifest)
        for mutation in ('failed', 'running', 'no_id', 'children', 'work_state',
                         'superseded', 'model', 'inactive', 'raw_hash', 'dependencies'):
            with self.subTest(mutation=mutation):
                self.manifest = copy.deepcopy(original)
                task = self.manifest['tasks'][request]
                if mutation in ('failed', 'running'):
                    task['status'] = mutation
                elif mutation == 'no_id':
                    task['task_id'] = None
                elif mutation == 'children':
                    task['hasPendingChildRuns'] = True
                elif mutation == 'work_state':
                    task['workState'] = 'waiting_for_children'
                elif mutation == 'superseded':
                    task['superseded'] = True
                elif mutation == 'model':
                    task['target']['model'] = 'old-model'
                elif mutation == 'inactive':
                    self.manifest['seats'][0]['active'] = False
                elif mutation == 'raw_hash':
                    task['raw_sha256'] = '0' * 64
                else:
                    task['dependencies'] = {'sources': ['changed'], 'accepted': {}}
                self.save()
                cli = self.cli('scrub.py', 'r1', '--request-id', request)
                self.assertEqual(cli.returncode, 2, cli.stdout)
                self.assertNotIn('Traceback', cli.stderr)

    def test_current_epoch_required_for_r2_but_r1_can_survive_epoch(self):
        request = self.task()
        self.manifest['vote']['epoch'] = 2
        self.save()
        self.assertEqual(len(scrub.scrub(self.root, 'r1', [request])), 1)
        with self.assertRaisesRegex(ValueError, 'stale voting epoch'):
            scrub.scrub(self.root, 'r2', ['r2-A'])

    def test_raw_tampering_is_detected_and_bytes_are_left_alone(self):
        request = self.task()
        raw = self.root / self.manifest['tasks'][request]['output_path']
        raw.write_bytes(b'new raw bytes')
        with self.assertRaisesRegex(ValueError, 'hash'):
            scrub.scrub(self.root, 'r1', [request])
        self.assertEqual(raw.read_bytes(), b'new raw bytes')

    def test_request_stage_unknown_duplicate_and_multiple_attempts(self):
        request = self.task()
        for stage, ids in (('r1', ['unknown']), ('r2', [request]),
                           ('r1', [request, request]), ('bogus', [request])):
            with self.subTest(stage=stage, ids=ids), self.assertRaises(ValueError):
                scrub.scrub(self.root, stage, ids)
        second = self.artifact('r1', 'A', 'I am DeepSeek.\n', request='r1-A-a2', accepted=False)
        self.save()
        with self.assertRaisesRegex(ValueError, 'one successful attempt per seat'):
            scrub.scrub(self.root, 'r1', [request, second])

    def test_raw_and_published_path_symlinks_cannot_escape(self):
        request = self.task()
        raw = self.root / self.manifest['tasks'][request]['output_path']
        other = self.root / 'other.md'
        other.write_bytes(raw.read_bytes())
        raw.unlink()
        raw.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            scrub.scrub(self.root, 'r1', [request])
        raw.unlink()
        raw.write_bytes(other.read_bytes())
        (self.root / 'r1/published').symlink_to(self.root / '_moderator', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            scrub.scrub(self.root, 'r1', [request])
        self.assertEqual(other.read_bytes(), raw.read_bytes())

    def test_immutable_outputs_refuse_replacement(self):
        request = self.task()
        report = scrub.scrub(self.root, 'r1', [request])[0]
        path = self.root / report['path']
        path.write_bytes(b'foreign existing artifact')
        with self.assertRaisesRegex(ValueError, 'immutable scrub output'):
            scrub.scrub(self.root, 'r1', [request])
        self.assertEqual(path.read_bytes(), b'foreign existing artifact')

    def test_raw_json_published_identically_with_review_flag(self):
        original = '{"proposal":"I am Claude.","vendor":"deepseek"}\r\n'
        request = self.task(original)
        report = scrub.scrub(self.root, 'r1', [request])[0]
        self.assertEqual((self.root / report['path']).read_bytes(), original.encode())
        self.assertTrue(report['review_required'])
        self.assertEqual(report['redactions'], [])

    def test_publish_then_accept_through_run_cli(self):
        request = self.task('I am Claude.\n')
        self.manifest['stage'] = 'r1'
        self.save()
        raw = self.root / self.manifest['tasks'][request]['output_path']
        before = raw.read_bytes()
        report = scrub.scrub(self.root, 'r1', [request])[0]
        accepted = self.run_cli('accept', request, '--published', report['path'])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        manifest = json.loads((self.root / '_moderator/run.json').read_text())
        self.assertEqual(manifest['accepted']['r1']['A'], {
            'path': report['path'], 'sha256': report['sha256'],
            'request_id': request, 'epoch': report['epoch']})
        self.assertEqual(raw.read_bytes(), before)
        self.assertEqual(scrub.scrub(self.root, 'r1')[0]['path'], report['path'])

    def test_configured_model_identities_are_scrubbed_and_flagged(self):
        request = self.task('I am Future-LLM-7.\nSource: vendor/future-llm-7.\n')
        self.manifest['seats'][0]['model'] = 'vendor/Future-LLM-7'
        self.manifest['seats'][0]['model_identity'] = 'Future-LLM-7'
        self.manifest['tasks'][request]['target']['model'] = 'vendor/Future-LLM-7'
        self.save()
        report = scrub.scrub(self.root, 'r1', [request])[0]
        self.assertEqual((self.root / report['path']).read_text(),
                         'I am [panelist].\nSource: vendor/future-llm-7.\n')
        self.assertEqual([m['text'] for m in report['mentions']], ['vendor/future-llm-7'])
        self.assertTrue(report['review_required'])

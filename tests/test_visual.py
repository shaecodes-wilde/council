"""Renderer boundary and observable isolated CLI regressions; no provider calls."""
import copy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_visual as visual


class Markup(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.attrs = []
        self.scripts = []
        self.in_script = False
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)
        self.in_script = tag == 'script'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts.append(data)


def fixture():
    return json.loads((ROOT / 'tests/fixtures/visual-report.json').read_text())


def tally(status='complete'):
    roster = ['A', 'B', 'C']
    rows = []
    for letter, points, first in [('A', 4, 2), ('B', 3, 1), ('C', 2, 0)]:
        rows.append({'letter': letter, 'by': {v: 8 for v in roster}, 'self': 8,
                     'peer_avg': 8, 'peer_dim_avg': {'Correctness': 8}, 'borda': points,
                     'first_place': first, 'peer_count': 2, 'peer_range': [8, 8],
                     'peer_dim_range': {'Correctness': [8, 8]}})
    return {'status': status, 'dimensions': ['Correctness'], 'max_total': 10,
            'min_total': 1, 'roster': roster, 'epoch': 1, 'proposals': rows,
            'top_picks': {'A': 'B', 'B': 'A', 'C': 'A'}, 'winner': 'A', 'tied': [],
            'invalid': {}, 'errors': {}, 'borda_max': 4}


class VisualTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / '.t3/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='council-visual-test-', dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, source, historical=False, script=None):
        output = self.root / 'report.html'
        cmd = [sys.executable, '-I', str(script or ROOT / 'scripts/build_visual.py')]
        if historical:
            cmd.append('--historical')
        result = subprocess.run(cmd + [str(source), str(output)], cwd=self.root,
                                text=True, capture_output=True)
        return result, output

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def live(self):
        manifest = {'schema_version': 1, 'run_id': 'visual-fixture', 'owner_thread_id': 'owner',
                    'created_at': '2026-10-22', 'topic': 'A recoverable importer',
                    'stage': 'complete', 'status': 'complete', 'readiness': 'ready',
                    'rubric': [{'name': 'Correctness', 'definition': 'Works', 'low': 'Fails',
                                'mid': 'Partial', 'high': 'Passes', 'direction': 'higher', 'weight': 1}],
                    'seats': [{'letter': letter, 'providerInstanceId': 'fixture',
                               'model': 'model-' + letter, 'model_identity': 'model-' + letter,
                               'active': True, 'options': {}} for letter in 'ABC'],
                    'sources': [], 'vote': {'epoch': 1, 'roster': list('ABC'), 'status': 'complete'},
                    'tasks': {}, 'accepted': {}, 'blockers': [],
                    'final_plan': {'stage': 'synthesis', 'letter': 'A'}}
        plan = fixture()['final_plan']
        for stage, letters in [('r2', 'ABC'), ('r3', 'ABC'), ('synthesis', 'A'), ('redteam', 'BC'), ('verify', 'BC')]:
            for letter in letters:
                request = stage + '-' + letter
                raw_rel = '_moderator/raw/' + request + '.md'
                pub_rel = stage + '/' + letter + '.md'
                content = plan if stage == 'synthesis' else 'Accepted ' + request
                if stage == 'r3':
                    rankings = {'A': ['B', 'C'], 'B': ['A', 'C'], 'C': ['A', 'B']}
                    content += '\n```json\n' + json.dumps({'scores': {v: {'Correctness': 8} for v in 'ABC'}, 'ranking': rankings[letter]}) + '\n```'
                if stage == 'verify':
                    content += '\n```json\n' + json.dumps({'final_sha256': hashlib.sha256(plan.encode()).hexdigest(), 'findings': {}}) + '\n```'
                for rel in (raw_rel, pub_rel):
                    path = self.root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding='utf-8')
                sha = hashlib.sha256(content.encode()).hexdigest()
                seat = next(s for s in manifest['seats'] if s['letter'] == letter)
                manifest['tasks'][request] = {
                    'task_id': 'task-' + request, 'stage': stage, 'letter': letter,
                    'attempt': 1, 'epoch': 1, 'status': 'completed', 'output_path': raw_rel,
                    'target': {k: seat[k] for k in ('providerInstanceId', 'model', 'options')},
                    'hasPendingChildRuns': False, 'workState': 'result_available', 'raw_sha256': sha}
                manifest['accepted'].setdefault(stage, {})[letter] = {
                    'path': pub_rel, 'sha256': sha, 'request_id': request, 'epoch': 1}
        self.write(self.root / 'findings.json', [])
        manifest['findings'] = []
        manifest['findings_provenance'] = {'artifact': {'path': 'findings.json', 'sha256': hashlib.sha256((self.root / 'findings.json').read_bytes()).hexdigest()}, 'redteam': copy.deepcopy(manifest['accepted']['redteam'])}
        self.write(self.root / 'final-check.json', {'final_sha256': hashlib.sha256(plan.encode()).hexdigest(), 'sections': {key: 'See final plan section: ' + key for key in ('direction', 'evidence', 'contributions', 'strongest_objection', 'conditions', 'actions', 'acceptance_checks', 'user_decisions')}, 'user_decisions': [], 'findings': []})
        manifest['final_verification'] = {'path': 'final-check.json', 'sha256': hashlib.sha256((self.root / 'final-check.json').read_bytes()).hexdigest()}
        self.save_manifest(manifest)
        from tally import tally as recompute_tally
        t = recompute_tally(self.root)
        t['provenance'] = copy.deepcopy(manifest['accepted']['r3'])
        t['proposal_provenance'] = copy.deepcopy(manifest['accepted']['r2'])
        self.write(self.root / 'r3/registered.json', t)
        manifest['vote']['tally'] = {'path': 'r3/registered.json', 'epoch': 1,
                                    'sha256': hashlib.sha256((self.root / 'r3/registered.json').read_bytes()).hexdigest()}
        self.write(self.root / '_moderator/run.json', manifest)
        return manifest

    def save_manifest(self, manifest):
        self.write(self.root / '_moderator/run.json', manifest)

    def change_accepted(self, manifest, stage, letter, content):
        artifact = manifest['accepted'][stage][letter]
        task = manifest['tasks'][artifact['request_id']]
        for rel in (artifact['path'], task['output_path']):
            (self.root / rel).write_text(content, encoding='utf-8')
        digest = hashlib.sha256(content.encode()).hexdigest()
        artifact['sha256'] = task['raw_sha256'] = digest

    def change_final_check(self, manifest, document):
        self.write(self.root / 'final-check.json', document)
        manifest['final_verification']['sha256'] = hashlib.sha256((self.root / 'final-check.json').read_bytes()).hexdigest()

    def register_tally(self, manifest):
        self.save_manifest(manifest)
        from tally import tally as recompute_tally
        t = recompute_tally(self.root)
        self.write(self.root / 'r3/registered.json', t)
        manifest['vote']['tally']['sha256'] = hashlib.sha256((self.root / 'r3/registered.json').read_bytes()).hexdigest()
        manifest['vote']['status'] = t['status']
        manifest['vote']['tied'] = t['tied']
        manifest['vote']['winner'] = t['winner']
        self.save_manifest(manifest)
        return t

    def test_historical_cli_is_explicit_and_does_not_discover_tally(self):
        source = self.root / 'data.json'
        self.write(source, fixture())
        self.write(self.root / 'r3/tally.json', {'winner': 'ATTACK', 'status': 'complete'})
        result, output = self.cli(source)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(output.exists())
        result, output = self.cli(source, True)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn('Historical report', page)
        self.assertNotIn('ATTACK', page)
        self.assertNotIn('Winner:', page)

    def test_full_final_plan_is_first_and_visible_with_accessible_tables(self):
        d = fixture()
        page = visual.render_report(d, plan=d['final_plan'])
        self.assertIn(visual.e(d['final_plan']), page)
        self.assertLess(page.index('Final decision plan'), page.index('Readiness and run status'))
        self.assertLess(page.index('Readiness and run status'), page.index('Proposal history'))
        self.assertIn('<h1>Council: ship a recoverable importer</h1>', page)
        self.assertIn('<caption>', page)
        self.assertIn('scope="col"', page)
        self.assertIn('scope="row"', page)
        self.assertIn('name="viewport"', page)
        self.assertIn('textContent=', page)
        self.assertNotIn('innerHTML', page)
        self.assertNotIn(':root{--foreground:', page)

    def test_hostile_labels_remain_text_and_are_never_in_javascript(self):
        d = fixture()
        attack = '</script><script>alert(1)</script><img src=x onerror=alert(2)>'
        d['title'] = attack
        d['seats'][0]['name'] = attack
        d['score_chart']['series'][0]['label'] = attack
        d['score_chart']['dimensions'][0] = attack
        d['disputes'][0]['positions']['B']['reason'] = attack
        page = visual.render_report(d, plan=attack)
        parsed = Markup(page)
        self.assertNotIn('img', parsed.tags)
        self.assertEqual(parsed.tags.count('script'), 1)
        self.assertNotIn(attack, ''.join(parsed.scripts))
        self.assertNotIn('onerror', [name for name, _ in parsed.attrs])
        self.assertIn('&lt;/script&gt;', page)
        self.assertIn('Changed position', page)
        self.assertIn(visual.e(attack), page)

    def test_hostile_and_out_of_range_colors_are_rejected(self):
        for color in ['1);color:red', '1" onmouseover="alert(1)', True, 0, 7, 1.5]:
            with self.subTest(color=color):
                d = fixture()
                d['seats'][0]['color'] = color
                with self.assertRaisesRegex(ValueError, 'seat.color'):
                    visual.render_report(d)
        for color in ['red;position:fixed', '#fff" onmouseover="x', 'url(https://bad)', 'var(--evil)', '#fff']:
            with self.subTest(color=color):
                d = fixture()
                d['score_chart']['series'][0]['color'] = color
                with self.assertRaisesRegex(ValueError, 'colors'):
                    visual.render_report(d)
        d = fixture()
        d['score_chart']['series'][0]['color'] = '#12AbEf'
        visual.render_report(d)

    def test_scores_types_ranges_and_nonfinite_values_are_rejected(self):
        for values in [[True, 8], [float('nan'), 8], [float('inf'), 8], [0, 8], [11, 8], [8], '8,8', [10**400, 8]]:
            with self.subTest(values=values):
                d = fixture()
                d['score_chart']['series'][0]['values'] = values
                with self.assertRaises(ValueError):
                    visual.render_report(d)
        for low, high in [(8, 8), (10, 1), (-1, 10), (1, float('inf'))]:
            with self.subTest(bounds=(low, high)):
                d = fixture()
                d['score_chart'].update(min=low, max=high)
                with self.assertRaises(ValueError):
                    visual.render_report(d)

    def test_tally_numeric_and_range_fields_cannot_inject_markup(self):
        for key in ('borda', 'peer_avg', 'first_place', 'self'):
            d = fixture()
            d['tally'] = tally()
            d['tally']['proposals'][0][key] = '<img src=x onerror=alert(1)>'
            with self.subTest(key=key), self.assertRaises(ValueError):
                visual.render_report(d)
        for value in [[9, 2], [2], [0, 5], [3, float('nan')], '3,5']:
            d = fixture()
            d['tally'] = tally()
            d['tally']['proposals'][0]['peer_dim_range']['Correctness'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                visual.render_report(d)
        for field, value in [('max_total', '<script>x</script>'), ('min_total', True), ('borda_max', 999)]:
            d = fixture()
            d['tally'] = tally()
            d['tally'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                visual.render_report(d)

    def test_malicious_tally_error_text_is_escaped(self):
        d = fixture()
        d['tally'] = tally('invalid')
        d['tally']['errors'] = {'A': '<img src=x onerror=alert(1)>'}
        page = visual.render_report(d)
        self.assertNotIn('img', Markup(page).tags)
        self.assertIn('&lt;img', page)
        self.assertNotIn('Winner:', page)

    def test_incomplete_invalid_pending_and_mismatched_vote_have_no_winner(self):
        for status in ('incomplete', 'invalid', 'pending'):
            d = fixture()
            d['tally'] = tally(status)
            page = visual.render_report(d, {'vote': {'status': status}, 'tasks': {}, 'run_id': 'r', 'stage': 'r3'}, plan='Proposed plan')
            with self.subTest(status=status):
                self.assertNotIn('Winner:', page)
                self.assertIn('Election</strong><br>' + status, page)
        d['tally'] = tally()
        page = visual.render_report(d, {'vote': {'status': 'incomplete'}, 'tasks': {}, 'run_id': 'r', 'stage': 'r3'}, plan='Proposed plan')
        self.assertNotIn('Winner:', page)

    def test_agreement_denominator_uses_relevant_roster_and_dropped_history(self):
        page = visual.render_report(fixture())
        self.assertIn('3/3 remaining; 1 dropped', page)
        self.assertIn('Original Delta', page)
        self.assertIn('Quota exhausted', page)
        self.assertNotIn('4/4 remaining', page)

    def test_stale_finding_never_counts_as_current_or_ready(self):
        d = fixture()
        d.pop('user_calls')
        d['readiness'] = 'ready'
        page = visual.render_report(d, plan='New plan', plan_hash='new-version')
        self.assertIn('Unverified against current plan', page)
        self.assertIn('Readiness</strong><br>blocked', page)
        d['redteam']['issues'][0].update(plan_sha256='new-version', verified=True)
        page = visual.render_report(d, plan='New plan', plan_hash='new-version')
        self.assertIn('Verified against current plan', page)
        self.assertNotIn('Readiness</strong><br>ready', page)  # historical is never live-ready

    def test_isolated_live_cli_reads_accepted_full_plan_and_registered_tally(self):
        self.live()
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn(visual.e(fixture()['final_plan']), page)
        self.assertIn('Winner: model-A', page)
        self.assertIn('Peer scores by dimension', page)
        self.assertIn('Accepted plan SHA-256', page)
        self.assertNotIn('Historical report', page)
        self.assertIn('Accepted transcripts', page)

    def test_live_ignores_supplied_fake_plan_tally_and_stale_tally_file(self):
        m = self.live()
        m['vote'].pop('tally')
        m['vote']['status'] = 'pending'
        self.save_manifest(m)
        source = self.root / 'data.json'
        self.write(source, {'final_plan': 'FAKE PLAN', 'tally': {'winner': 'ATTACK'}})
        self.write(self.root / 'r3/tally.json', {'winner': 'ATTACK'})
        result, output = self.cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertNotIn('FAKE PLAN', page)
        self.assertNotIn('ATTACK', page)
        self.assertNotIn('Winner:', page)

    def test_complete_without_plan_and_tampered_artifacts_fail(self):
        m = self.live()
        (self.root / 'synthesis/A.md').write_text('tampered')
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('hash mismatch', result.stderr)
        self.assertFalse(output.exists())
        m['final_plan'] = None
        self.save_manifest(m)
        result, _ = self.cli(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('final_plan', result.stderr)

    def test_tally_reference_must_match_hash_epoch_and_accepted_provenance(self):
        for mutation, expected in [('hash', 'hash mismatch'), ('epoch', 'stale epoch'), ('provenance', 'provenance')]:
            with self.subTest(mutation=mutation):
                m = self.live()
                if mutation == 'hash':
                    (self.root / 'r3/registered.json').write_text('{}')
                elif mutation == 'epoch':
                    m['vote']['tally']['epoch'] = 2
                else:
                    p = self.root / 'r3/registered.json'
                    t = json.loads(p.read_text())
                    t['provenance']['A']['sha256'] = '0' * 64
                    self.write(p, t)
                    m['vote']['tally']['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
                self.save_manifest(m)
                result, _ = self.cli(self.root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)

    def test_duplicate_json_keys_and_nonfinite_json_fail_cleanly(self):
        source = self.root / 'data.json'
        for payload in ['{"title":"one","title":"two"}', '{"stats":NaN}', '{"stats":1e999}', '[' * 1500 + '0' + ']' * 1500]:
            with self.subTest(payload=payload):
                source.write_text(payload)
                result, output = self.cli(source, True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(output.exists())
                self.assertNotIn('Traceback', result.stderr)

    def test_live_tie_keeps_alternatives_and_user_choice_separate_from_lifecycle(self):
        m = self.live()
        self.change_accepted(m, 'r3', 'B', '```json\n' + json.dumps({'scores': {v: {'Correctness': 8} for v in 'ABC'}, 'ranking': ['C', 'A']}) + '\n```')
        t = self.register_tally(m)
        self.assertEqual(t['status'], 'tied')
        final = json.loads((self.root / 'final-check.json').read_text())
        final.update(user_decisions=['Choose A, B or C'], tied_alternatives=t['tied'])
        self.change_final_check(m, final)
        self.save_manifest(m)
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn('Lifecycle</strong><br>complete', page)
        self.assertIn('Election</strong><br>tied', page)
        self.assertIn('Readiness</strong><br>needs-user-decision', page)
        self.assertIn('Choose A, B or C', page)
        self.assertNotIn('Winner:', page)

    def test_live_incomplete_run_renders_without_final_plan_or_winner(self):
        m = self.live()
        m.update(stage='r3', status='incomplete', readiness='incomplete')
        m.pop('final_plan')
        m.pop('final_verification')
        m['accepted']['r3'].pop('B')
        self.register_tally(m)
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn('No accepted final plan is available yet', page)
        self.assertIn('Election</strong><br>incomplete', page)
        self.assertNotIn('Winner:', page)

    def test_final_dispositions_require_current_hash_and_every_critic(self):
        m = self.live()
        digest = m['accepted']['synthesis']['A']['sha256']
        finding = {'id': 'F1', 'description': 'An interrupted batch is lost', 'hard_constraint': True,
                   'evidence': [{'path': 'r2/A.md', 'sha256': m['accepted']['r2']['A']['sha256']}]}
        m['findings'] = [finding]
        self.write(self.root / 'findings.json', [finding])
        m['findings_provenance']['artifact']['sha256'] = hashlib.sha256((self.root / 'findings.json').read_bytes()).hexdigest()
        final = json.loads((self.root / 'final-check.json').read_text())
        final['findings'] = [{'id': 'F1', 'disposition': 'resolved', 'evidence': finding['evidence']}]
        self.change_final_check(m, final)
        for letter in 'BC':
            content = '```json\n' + json.dumps({'final_sha256': digest, 'findings': {'F1': {'disposition': 'resolved', 'verified': True}}}) + '\n```'
            self.change_accepted(m, 'verify', letter, content)
        self.save_manifest(m)
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn('Verified against current plan', page)
        self.assertIn('Readiness</strong><br>ready', page)
        self.assertIn('An interrupted batch is lost', page)
        for mutation, expected in [('hash', 'different final plan hash'), ('confirmation', 'critic confirmation')]:
            with self.subTest(mutation=mutation):
                check = {'final_sha256': '0' * 64 if mutation == 'hash' else digest,
                         'findings': {'F1': {'disposition': 'resolved', 'verified': mutation != 'confirmation'}}}
                self.change_accepted(m, 'verify', 'B', '```json\n' + json.dumps(check) + '\n```')
                self.save_manifest(m)
                result, _ = self.cli(self.root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(expected, result.stderr)

    def test_tally_is_recomputed_and_path_traversal_is_rejected(self):
        m = self.live()
        t = json.loads((self.root / 'r3/registered.json').read_text())
        t['proposals'][0]['borda'] = 3
        self.write(self.root / 'r3/registered.json', t)
        m['vote']['tally']['sha256'] = hashlib.sha256((self.root / 'r3/registered.json').read_bytes()).hexdigest()
        self.save_manifest(m)
        result, _ = self.cli(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('differs from validated accepted ballots', result.stderr)
        m['vote']['tally']['path'] = '../outside.json'
        self.save_manifest(m)
        result, _ = self.cli(self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsafe artifact path', result.stderr)

    def test_new_exact_hash_verifier_objections_cannot_display_ready_unassessed(self):
        for remaining in ([], [{'id': 'NEW1', 'issue': 'Missing restart recovery', 'severity': 'must', 'hard_constraint': True}]):
            with self.subTest(remaining=remaining):
                m = self.live()
                check = {'final_sha256': m['accepted']['synthesis']['A']['sha256'],
                         'findings': {}, 'verdict': 'block', 'remaining_blockers': remaining}
                self.change_accepted(m, 'verify', 'B', '```json\n' + json.dumps(check) + '\n```')
                self.save_manifest(m)
                result, output = self.cli(self.root)
                self.assertEqual(result.returncode, 0, result.stderr)
                page = output.read_text()
                self.assertIn('Readiness</strong><br>blocked', page)
                self.assertNotIn('Readiness</strong><br>ready', page)
                self.assertIn('Missing restart recovery' if remaining else 'Critic verdict: block', page)

    def test_verifier_objection_rejection_requires_registered_counterevidence(self):
        m = self.live()
        check = {'final_sha256': m['accepted']['synthesis']['A']['sha256'], 'findings': {},
                 'verdict': 'block', 'remaining_blockers': [{'id': 'NEW1', 'issue': 'Unsupported recovery objection'}]}
        self.change_accepted(m, 'verify', 'B', '```json\n' + json.dumps(check) + '\n```')
        final = json.loads((self.root / 'final-check.json').read_text())
        evidence = [{k: m['accepted']['synthesis']['A'][k] for k in ('path', 'sha256')}]
        assessment = {'critic': 'B', 'id': 'NEW1', 'disposition': 'rejected',
                      'evidence': evidence, 'rationale': 'The final plan includes restart recovery and its acceptance check.'}
        final['verifier_objections'] = [assessment]
        self.change_final_check(m, final)
        self.save_manifest(m)
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Readiness</strong><br>ready', output.read_text())
        self.assertIn(assessment['rationale'], output.read_text())
        for mutation in ('missing', 'unregistered', 'hash'):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(final)
                if mutation == 'missing':
                    bad['verifier_objections'][0]['evidence'] = []
                else:
                    self.write(self.root / 'unregistered.json', {'claim': 'counterevidence'})
                    ref = {'path': 'unregistered.json', 'sha256': hashlib.sha256((self.root / 'unregistered.json').read_bytes()).hexdigest()}
                    if mutation == 'hash':
                        ref = {'path': evidence[0]['path'], 'sha256': '0' * 64}
                    bad['verifier_objections'][0]['evidence'] = [ref]
                self.change_final_check(m, bad)
                self.save_manifest(m)
                result, _ = self.cli(self.root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('evidence', result.stderr)

    def test_unresolved_should_fix_is_visible_but_hard_or_must_blocks(self):
        for severity, hard, readiness in [('should', False, 'ready'), ('must', False, 'blocked'), ('should', True, 'blocked')]:
            for new_objection in (False, True):
                with self.subTest(severity=severity, hard=hard, new_objection=new_objection):
                    m = self.live()
                    finding = {'id': 'F1', 'description': 'Measure recovery overhead', 'hard_constraint': hard,
                               'severity': severity, 'evidence': [{k: m['accepted']['r2']['A'][k] for k in ('path', 'sha256')}]}
                    if new_objection:
                        check = {'final_sha256': m['accepted']['synthesis']['A']['sha256'], 'findings': {},
                                 'verdict': 'ship-after-fixes', 'remaining_blockers': [finding]}
                        self.change_accepted(m, 'verify', 'B', '```json\n' + json.dumps(check) + '\n```')
                    else:
                        m['findings'] = [finding]
                        self.write(self.root / 'findings.json', [finding])
                        m['findings_provenance']['artifact']['sha256'] = hashlib.sha256((self.root / 'findings.json').read_bytes()).hexdigest()
                        final = json.loads((self.root / 'final-check.json').read_text())
                        final['findings'] = [{'id': 'F1', 'disposition': 'unresolved', 'evidence': finding['evidence']}]
                        self.change_final_check(m, final)
                        for letter in 'BC':
                            check = {'final_sha256': m['accepted']['synthesis']['A']['sha256'],
                                     'findings': {'F1': {'disposition': 'unresolved', 'verified': False}}}
                            self.change_accepted(m, 'verify', letter, '```json\n' + json.dumps(check) + '\n```')
                    self.save_manifest(m)
                    result, output = self.cli(self.root)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('Readiness</strong><br>' + readiness, output.read_text())
                    self.assertIn('Measure recovery overhead', output.read_text())

    def test_earlier_stage_without_roster_and_telemetry_do_not_invent_totals(self):
        d = fixture()
        d.pop('stage_rosters')
        page = visual.render_report(d)
        self.assertIn('Stage roster unavailable', page)
        self.assertIn('Unavailable; not zero', page)
        d['telemetry'] = {'coverage': 'partial', 'source': 'Provider usage export', 'tokens': 1200, 'cost': 0.02, 'currency': 'USD'}
        page = visual.render_report(d)
        self.assertIn('Provider usage export', page)
        self.assertIn('partial', page)
        for key, value in [('tokens', True), ('cost', -1), ('calls', '<img>'), ('coverage', 'unknown')]:
            bad = copy.deepcopy(d)
            bad['telemetry'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                visual.render_report(bad)

    def test_report_output_cannot_overwrite_raw_or_accepted_artifacts(self):
        self.live()
        for relative in ('_moderator/raw/synthesis-A.md', 'synthesis/A.md', '_moderator/run.json', 'r3/registered.json'):
            target = self.root / relative
            before = target.read_bytes()
            result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/build_visual.py'), str(self.root), str(target)], text=True, capture_output=True)
            with self.subTest(output=relative):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('overwrite', result.stderr)
                self.assertEqual(target.read_bytes(), before)

    def test_hardlinked_output_cannot_overwrite_any_immutable_input(self):
        m = self.live()
        self.write(self.root / 'source.json', {'frozen': True})
        source = {'path': 'source.json', 'sha256': hashlib.sha256((self.root / 'source.json').read_bytes()).hexdigest()}
        m['sources'] = [source]
        self.write(self.root / 'evidence.json', {'evidence': True})
        m['evidence'] = [{'path': 'evidence.json', 'sha256': hashlib.sha256((self.root / 'evidence.json').read_bytes()).hexdigest()}]
        # The frozen source is a dependency of every accepted task.
        from contracts import dependency_snapshot
        for task in m['tasks'].values():
            task['dependencies'] = dependency_snapshot(m, task['stage'])
        self.save_manifest(m)
        for relative in ('source.json', 'evidence.json', '_moderator/raw/synthesis-A.md',
                         'synthesis/A.md', '_moderator/run.json', 'r3/registered.json',
                         'final-check.json', 'findings.json'):
            target = self.root / relative
            output = self.root / 'report.html'
            before = target.read_bytes()
            if output.exists():
                output.unlink()
            os.link(target, output)
            result, _ = self.cli(self.root)
            with self.subTest(input=relative):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('overwrite', result.stderr)
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(output.read_bytes(), before)

    def test_historical_hardlink_and_atomic_replacement_preserve_input(self):
        source = self.root / 'data.json'
        self.write(source, fixture())
        before = source.read_bytes()
        output = self.root / 'report.html'
        os.link(source, output)
        result, _ = self.cli(source, True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('overwrite', result.stderr)
        self.assertEqual(source.read_bytes(), before)
        # Atomic replacement preserves a newly introduced alias even after validation.
        visual.write_report(output, 'new report')
        self.assertEqual(output.read_text(), 'new report')
        self.assertEqual(source.read_bytes(), before)

    def test_wall_clock_elapsed_uses_complete_or_running_timestamps(self):
        m = {'status': 'complete', 'created_at': '2026-10-22T01:00:00Z',
             'completed_at': '2026-10-21T22:01:30.500-03:00'}
        self.assertEqual(visual.elapsed_seconds(m), 90.5)
        m['status'] = 'running'
        self.assertEqual(visual.elapsed_seconds(m, datetime(2026, 10, 22, 1, 2, tzinfo=timezone.utc)), 120)
        m['status'] = 'complete'
        for field, value in [('created_at', None), ('created_at', 'bad'),
                             ('created_at', '2026-10-22'), ('completed_at', None),
                             ('completed_at', '2026-10-22T00:00:00Z'),
                             ('completed_at', '2026-10-22T01:01:00')]:
            with self.subTest(field=field, value=value):
                bad = dict(m, **{field: value})
                self.assertIsNone(visual.elapsed_seconds(bad))
        m.pop('completed_at')
        self.assertIsNone(visual.elapsed_seconds(m))

    def test_live_measurements_and_per_seat_counts_are_recorded_not_estimated(self):
        m = self.live()
        m.update(created_at='2026-10-22T01:00:00Z', completed_at='2026-10-22T01:02:03Z')
        previous = dict(m['tasks']['synthesis-A'], task_id='retry-task', attempt=2,
                        status='failed', output_path='_moderator/raw/retry-A.md')
        m['tasks']['retry-A'] = previous
        self.save_manifest(m)
        result, output = self.cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text()
        self.assertIn('Wall-clock elapsed seconds</th><td>123.0', page)
        self.assertIn('Tokens</th><td>Unavailable; not zero', page)
        self.assertIn('Cost</th><td>Unavailable; not zero', page)
        self.assertIn('<th scope="row">A</th><td>4</td><td>4</td><td>1</td>', page)
        self.assertIn('<th scope="row">B</th><td>4</td><td>4</td><td>0</td>', page)

    def test_inline_alignment_keeps_standalone_padding_defaults(self):
        d = fixture()
        self.assertNotIn('main{max-width:none;padding:0}', visual.render_report(d))
        self.assertIn('main{max-width:none;padding:0}', visual.render_report(d, inline=True))
        source = self.root / 'data.json'
        self.write(source, d)
        output = self.root / 'inline.html'
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/build_visual.py'),
                                 '--historical', '--inline', str(source), str(output)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('main{max-width:none;padding:0}', output.read_text())

    def test_nondefault_installation_bootstraps_from_real_script_location(self):
        install = self.root / 'nondefault skill/scripts'
        install.mkdir(parents=True)
        for name in ('contracts.py', 'build_visual.py'):
            shutil.copy(ROOT / 'scripts' / name, install / name)
        source = self.root / 'data.json'
        self.write(source, fixture())
        result, output = self.cli(source, True, install / 'build_visual.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())


if __name__ == '__main__':
    unittest.main()

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import panel


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.config = panel.read_json(panel.CONFIG)
        targets = self.config['preferred'] + self.config['fallbacks']
        providers = {}
        for target in targets:
            p = providers.setdefault(target['providerInstanceId'], {'providerInstanceId': target['providerInstanceId'], 'canRunChildTask': True, 'models': []})
            if not any(m['id'] == target['model'] for m in p['models']):
                p['models'].append({'id': target['model'], 'options': [{'id': k, 'type': 'select', 'options': [{'id': v}]} for k, v in target['options'].items()]})
            else:
                model = next(m for m in p['models'] if m['id'] == target['model'])
                for key, value in target['options'].items():
                    spec = next(s for s in model['options'] if s['id'] == key)
                    if not any(o['id'] == value for o in spec['options']):
                        spec['options'].append({'id': value})
        self.catalog = {'providers': list(providers.values())}
        self.active = [dict(t, active=True) for t in self.config['preferred']]
        self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.checks = {t['model']: {'free': True, 'smoke_passed': True, 'data_policy_compatible': True,
                                   'model_identity': t['model_identity'], 'options': t['options'],
                                   'free_source': t['free_source'], 'checked_at': self.now.isoformat()}
                       for t in self.config['fallbacks']}

    def test_preferred_four_and_anonymous_letters(self):
        seats, warnings = panel.resolve_panel(self.catalog, self.config)
        self.assertEqual(len(seats), 4)
        self.assertEqual({s['letter'] for s in seats}, set('ABCD'))
        self.assertFalse(warnings)

    def test_live_generic_devin_default_is_not_mislabeled(self):
        p = next(p for p in self.catalog['providers'] if p['providerInstanceId'] == 'acpRegistry_devin')
        p['models'] = [{'id': 'default', 'options': []}]
        seats, warnings = panel.resolve_panel(self.catalog, self.config)
        self.assertEqual(len(seats), 3)
        self.assertTrue(any('swe-2-high' in warning for warning in warnings))
        self.assertFalse(any(s['alias'] == 'devin' for s in seats))

    def test_fewer_than_three_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'fewer than three'):
            panel.resolve_panel(self.catalog, self.config, without=('opus', 'sol'))

    def test_alias_duplicate_does_not_add_diversity(self):
        seats, warnings = panel.resolve_panel(self.catalog, self.config, added=('sol',))
        self.assertEqual(len(seats), 4)
        self.assertTrue(any('duplicate' in warning for warning in warnings))

    def test_options_use_live_catalog(self):
        target = copy.deepcopy(self.config['preferred'][0])
        target['options']['effort'] = 'imaginary'
        with self.assertRaisesRegex(ValueError, 'unsupported effort'):
            panel.validate_target(self.catalog, target)

    def test_disabled_provider_not_selected(self):
        self.catalog['providers'][0]['canRunChildTask'] = False
        seats, warnings = panel.resolve_panel(self.catalog, self.config)
        self.assertEqual(len(seats), 3)
        self.assertTrue(warnings)

    def test_distinct_qualified_fallback(self):
        result = panel.choose_fallback(self.catalog, self.config, 'code', self.active, self.checks, now=self.now)
        self.assertEqual(result['alias'], 'glm')
        self.active.append(dict(result, active=True))
        result2 = panel.choose_fallback(self.catalog, self.config, 'code', self.active, self.checks, now=self.now)
        self.assertEqual(result2['alias'], 'minimax')
        self.active.append(dict(result2, active=True))
        with self.assertRaisesRegex(ValueError, 'no distinct'):
            panel.choose_fallback(self.catalog, self.config, 'code', self.active, self.checks, now=self.now)

    def test_reservation_skips_simultaneous_candidate(self):
        reserved = ['opencode/glm-5.3-flash-free']
        result = panel.choose_fallback(self.catalog, self.config, 'code', self.active, self.checks, used=reserved, now=self.now)
        self.assertEqual(result['alias'], 'minimax')

    def test_three_distinct_free_models_when_preferred_panel_is_exhausted(self):
        active, used = [], []
        for _ in range(3):
            chosen = panel.choose_fallback(self.catalog, self.config, 'code', active, self.checks, used=used, now=self.now)
            active.append(dict(chosen, active=True))
            used.append(chosen['model'])
        self.assertEqual(len({s['model_identity'] for s in active}), 3)
        with self.assertRaises(ValueError):
            panel.choose_fallback(self.catalog, self.config, 'code', active, self.checks, used=used, now=self.now)

    def test_raw_model_id_cannot_hide_a_known_duplicate(self):
        seats, warnings = panel.resolve_panel(self.catalog, self.config,
                                             added=('opencode:opencode/deepseek-v4.1-flash-free@variant=high,agent=build',))
        self.assertEqual(len(seats), 4)
        self.assertTrue(any('duplicate' in warning for warning in warnings))

    def test_receipts_required_and_match_exact_configuration(self):
        for field, wrong in [('free', False), ('smoke_passed', False), ('data_policy_compatible', False),
                             ('model_identity', 'other'), ('options', {}), ('free_source', 'https://example.com/')]:
            with self.subTest(field=field):
                checks = copy.deepcopy(self.checks)
                for check in checks.values():
                    check[field] = wrong
                with self.assertRaises(ValueError):
                    panel.choose_fallback(self.catalog, self.config, 'code', self.active, checks, now=self.now)

    def test_stale_future_and_naive_receipts_rejected(self):
        for stamp in [(self.now-timedelta(days=2)).isoformat(), (self.now+timedelta(seconds=1)).isoformat(), '2026-10-22T00:00:00']:
            checks = copy.deepcopy(self.checks)
            for check in checks.values():
                check['checked_at'] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                panel.choose_fallback(self.catalog, self.config, 'code', self.active, checks, now=self.now)

    def test_unknown_identity_and_default_cannot_qualify(self):
        with self.assertRaises(ValueError):
            panel.added_target('opencode:default', self.config)
        with self.assertRaises(ValueError):
            panel.identity({'model_identity': 'unknown'})

    def test_limit_decisions_are_bounded(self):
        self.assertEqual(panel.limit_action('usage_exhausted')['action'], 'fallback')
        self.assertEqual(panel.limit_action('rate_limit', 30)['action'], 'retry-once')
        for delay in (None, True, 61, 10**400, float('nan'), float('inf'), -1):
            self.assertEqual(panel.limit_action('rate_limit', delay)['action'], 'fallback')
        self.assertEqual(panel.limit_action('rate_limit', 30, True)['action'], 'fallback')
        self.assertEqual(panel.limit_action('authentication')['action'], 'investigate')

    def test_cli_works_in_isolated_python(self):
        result = subprocess.run([sys.executable, '-I', str(Path(panel.__file__)), 'limit', 'rate_limit', '--retry-after', '10'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['after_seconds'], 10)


if __name__ == '__main__':
    unittest.main()

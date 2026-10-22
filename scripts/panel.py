#!/usr/bin/env python3
"""Resolve live V2 targets and select evidence-qualified, runtime-checked fallbacks.

No network or model calls. Moderator supplies the live catalog and fresh checks.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import read_json

CONFIG = Path(__file__).resolve().parent.parent / 'config' / 'panel.json'


def identity(target):
    value = target.get('model_identity')
    if type(value) is not str or not value.strip() or value.casefold() in {'default', 'unknown'}:
        raise ValueError('verified underlying model_identity is required')
    return value.strip().casefold()


def validate_target(catalog, target):
    if type(catalog) is not dict or type(catalog.get('providers')) is not list:
        raise ValueError('expected the V2 capability catalog object with providers')
    provider = next((p for p in catalog['providers'] if p.get('providerInstanceId') == target.get('providerInstanceId')), None)
    if provider is None or provider.get('canRunChildTask') is not True:
        raise ValueError(f"provider unavailable for child tasks: {target.get('providerInstanceId')}")
    model = next((m for m in provider.get('models', []) if m.get('id') == target.get('model')), None)
    if model is None:
        raise ValueError(f"model absent from live catalog: {target.get('model')}")
    options = target.get('options', {})
    if type(options) is not dict:
        raise ValueError('target options must be an object')
    specs = {s['id']: s for s in (model.get('options') or [])}
    for key, value in options.items():
        spec = specs.get(key)
        if spec is None:
            raise ValueError(f'unsupported option: {key}')
        if spec.get('type') == 'boolean':
            valid = type(value) is bool
        else:
            valid = type(value) is str and value in {o['id'] for o in spec.get('options', [])}
        if not valid:
            raise ValueError(f'unsupported {key} value: {value!r}')
    identity(target)
    return target


def added_target(value, config):
    aliases = {s['alias']: s for s in config['preferred']}
    aliases.update(config.get('aliases', {}))
    if value in aliases:
        return dict(deepcopy(aliases[value]), alias=value)
    provider, separator, rest = value.partition(':')
    if not separator or not provider or not rest:
        raise ValueError(f'unknown alias or invalid provider:model: {value}')
    model, _, option_text = rest.partition('@')
    options = {}
    for assignment in option_text.split(',') if option_text else []:
        key, sep, val = assignment.partition('=')
        if not sep or not key or not val or key in options:
            raise ValueError(f'invalid/duplicate option: {assignment}')
        options[key] = {'true': True, 'false': False}.get(val, val)
    # A generic default/custom alias needs an explicit verified identity in config.
    if model.casefold() in {'default', 'unknown'}:
        raise ValueError('generic defaults need a configured verified model_identity')
    known = config['preferred'] + config['fallbacks'] + list(config.get('aliases', {}).values())
    canonical = next((t['model_identity'] for t in known if t['model'] == model),
                     model.removeprefix('opencode/'))
    return dict(alias=value, name=model, providerInstanceId=provider, model=model,
                model_identity=canonical, options=options)


def resolve_panel(catalog, config, without=(), added=()):
    if config.get('schema_version') != 1:
        raise ValueError('unsupported panel config schema')
    known_aliases = {s['alias'] for s in config['preferred']}
    if set(without) - known_aliases:
        raise ValueError(f'unknown --without seats: {sorted(set(without) - known_aliases)}')
    requested = [deepcopy(s) for s in config['preferred'] if s['alias'] not in without]
    requested += [added_target(x, config) for x in added]
    seats, warnings, used = [], [], set()
    for target in requested:
        try:
            validate_target(catalog, target)
            model = identity(target)
            if model in used:
                raise ValueError(f'duplicate underlying model: {model}')
            used.add(model)
            seats.append(dict(target, active=True))
        except ValueError as exc:
            warnings.append(f"{target.get('alias', target.get('model'))}: {exc}")
    if len(seats) > 26:
        raise ValueError('at most 26 anonymous seats are supported')
    secrets.SystemRandom().shuffle(seats)
    for index, seat in enumerate(seats):
        seat['letter'] = chr(ord('A') + index)
    if len(seats) < 3:
        raise ValueError('fewer than three distinct usable models; ' + '; '.join(warnings))
    return seats, warnings


def fallback_candidates(catalog, config, mode, active, used=()):
    blocked = {identity(seat) for seat in active if seat.get('active', True)}
    models = {item['alias']: item for item in config['fallbacks']}
    candidates = []
    for alias in config['fallback_order'][mode]:
        target = models[alias]
        if target['model'] in used or identity(target) in blocked:
            continue
        benchmark = target.get('benchmark', {})
        if (target.get('providerInstanceId') != 'opencode' or not target.get('free_source', '').startswith('https://opencode.ai/')
                or not benchmark.get('source', '').startswith('https://') or not benchmark.get('metrics')):
            continue
        try:
            validate_target(catalog, target)
        except ValueError:
            continue
        candidates.append(deepcopy(target))
    return candidates


def choose_fallback(catalog, config, mode, active, checks, used=(), now=None):
    """Require fresh moderator receipts; a catalog name alone cannot prove free use."""
    now = now or datetime.now(timezone.utc)
    if type(checks) is not dict:
        raise ValueError('runtime checks must be an object keyed by exact model ID')
    for candidate in fallback_candidates(catalog, config, mode, active, used):
        check = checks.get(candidate['model'], {})
        if type(check) is not dict:
            continue
        if (check.get('free') is not True or check.get('smoke_passed') is not True
                or check.get('data_policy_compatible') is not True
                or check.get('model_identity') != candidate['model_identity']
                or check.get('options') != candidate['options']
                or not str(check.get('free_source', '')).startswith('https://opencode.ai/')):
            continue
        try:
            checked = datetime.fromisoformat(check['checked_at'].replace('Z', '+00:00'))
            age = (now - checked).total_seconds()
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= age <= 86400:
            continue
        candidate['runtime_check'] = deepcopy(check)
        return candidate
    raise ValueError('no distinct qualified fallback with current free/policy/smoke receipts; retain incomplete state if fewer than three usable models remain')


def limit_action(kind, retry_after=None, retried=False):
    if kind == 'usage_exhausted':
        return {'action': 'fallback', 'reason': 'usage exhausted'}
    if kind == 'rate_limit':
        if (not retried and type(retry_after) in (int, float)
                and 0 < retry_after <= 60 and math.isfinite(retry_after)):
            return {'action': 'retry-once', 'after_seconds': retry_after}
        return {'action': 'fallback', 'reason': 'no remaining short explicit retry'}
    return {'action': 'investigate', 'reason': 'not a confirmed provider quota/rate limit'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve', help='print shuffled seats JSON; unavailable seats warn on stderr')
    resolve.add_argument('catalog', type=Path)
    resolve.add_argument('--without', default='')
    resolve.add_argument('--add', action='append', default=[])
    for name in ('candidates', 'fallback'):
        p = commands.add_parser(name)
        p.add_argument('catalog', type=Path)
        p.add_argument('--active', type=Path, required=True, help='current seats JSON array')
        p.add_argument('--mode', choices=('code', 'strategy', 'redesign', 'artifact'), default='code')
        p.add_argument('--used', action='append', default=[], help='already tried or reserved exact model ID')
        if name == 'fallback':
            p.add_argument('--checks', type=Path, required=True)
    limit = commands.add_parser('limit')
    limit.add_argument('kind', choices=('usage_exhausted', 'rate_limit', 'unknown', 'authentication'))
    limit.add_argument('--retry-after', type=float)
    limit.add_argument('--retried', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'limit':
            result = limit_action(args.kind, args.retry_after, args.retried)
        else:
            config, catalog = read_json(args.config), read_json(args.catalog)
            if args.command == 'resolve':
                result, warnings = resolve_panel(catalog, config, [x for x in args.without.split(',') if x], args.add)
                for warning in warnings:
                    print(warning, file=sys.stderr)
            else:
                active = read_json(args.active)
                result = (fallback_candidates(catalog, config, args.mode, active, args.used)
                          if args.command == 'candidates' else
                          choose_fallback(catalog, config, args.mode, active, read_json(args.checks), args.used))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'panel: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

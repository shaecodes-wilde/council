#!/usr/bin/env python3
"""Render a self-contained Council report.

python3 -I scripts/build_visual.py <run-dir|data.json> <out.html>
python3 -I scripts/build_visual.py --historical <old-data.json> <out.html>

Live inputs come from the manifest, never filename discovery. Historical reports
are explicitly unverified and cannot establish acceptance or resumable state.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import math
import os
import pathlib
import re
import sys
import tempfile

# -I removes the script directory from sys.path. Resolve this installation,
# rather than assuming a particular home directory or current working directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from contracts import (accepted_paths, evaluate_final_objections, finding_blocks,
                       last_json, load_manifest, read_json, safe_path)

CSS = """
:root{color-scheme:light dark;--cv-text:var(--foreground,light-dark(#202124,#e5e7eb));--cv-muted:var(--muted-foreground,light-dark(#565d68,#bbc2cd));--cv-border:var(--border,light-dark(#d4d9e0,#454b55));--cv-bg:var(--background,light-dark(#fff,#171a20));--cv-card:var(--card,light-dark(#f4f6f8,#242933));--cv-accent:var(--accent-surface,light-dark(#e8f0fa,#243b56))}
*{box-sizing:border-box}body{margin:0;background:var(--cv-bg);color:var(--cv-text);font:14px/1.55 var(--font-sans,system-ui,sans-serif);overflow-wrap:anywhere}dl{margin:8px 0}dt{font-weight:600}dd{margin:0 0 8px 12px}main{max-width:1100px;margin:auto;padding:20px;min-width:0}h1{font-size:24px;line-height:1.25;margin:0 0 12px}h2{font-size:18px;margin:0 0 10px}h3{font-size:15px}section{margin:24px 0;min-width:0}p{margin:8px 0}.muted{color:var(--cv-muted)}.status{display:flex;gap:12px;flex-wrap:wrap}.status p{padding:10px;background:var(--cv-card);border-radius:6px}.plan{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;border-left:3px solid var(--cv-border);padding:0 14px}.scroll{overflow-x:auto;max-width:100%;border:1px solid var(--cv-border);border-radius:5px}table{border-collapse:collapse;width:100%;font-size:13px}caption{text-align:left;font-weight:600;padding:10px}th,td{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid var(--cv-border);overflow-wrap:anywhere}th{min-width:90px;font-weight:600;background:var(--cv-card)}tbody th{background:transparent}code,pre{font-family:var(--font-mono,ui-monospace,monospace)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}details{border-top:1px solid var(--cv-border);padding:10px 0}summary{cursor:pointer;font-weight:600;min-height:30px}button{font:inherit;color:inherit;background:var(--cv-card);border:1px solid var(--cv-border);border-radius:4px;min-width:44px;min-height:44px;padding:6px;cursor:pointer}button:focus-visible,summary:focus-visible,.scroll:focus-visible{outline:3px solid var(--cv-text);outline-offset:3px}.tip{position:fixed;bottom:12px;left:12px;right:12px;max-width:650px;white-space:pre-wrap;background:var(--cv-card);border:2px solid var(--cv-border);border-radius:6px;padding:12px;pointer-events:none;z-index:3}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}.s1{background:var(--chart-1,#2874c4)}.s2{background:var(--chart-2,#b85624)}.s3{background:var(--chart-3,#23804f)}.s4{background:var(--chart-4,#a77700)}.s5{background:var(--chart-5,#af4e88)}.s6{background:var(--chart-6,#735bbd)}.notice{background:var(--cv-accent);padding:12px;border-radius:6px}a{color:inherit}ul,ol{padding-left:24px}li{margin:6px 0}@media(max-width:480px){main{padding:12px}h1{font-size:21px}th,td{padding:7px}.status{gap:4px}.status p{flex:1 1 100%;margin:2px 0}}@media print{details>div{display:block}.tip,button{display:none}}
"""
TOOLTIPS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(button=>{
  const show=()=>{tip.textContent=button.dataset.tip;tip.hidden=false;};
  const hide=()=>{tip.hidden=true;};
  button.addEventListener('pointerenter',show);
  button.addEventListener('pointerleave',hide);
  button.addEventListener('focus',show);
  button.addEventListener('blur',hide);
  button.addEventListener('click',show);
});
document.addEventListener('keydown',event=>{if(event.key==='Escape')tip.hidden=true;});
"""
STAGES = ('r1', 'r2', 'r3', 'synthesis', 'redteam', 'repair', 'verify')
ELECTIONS = ('pending', 'complete', 'tied', 'incomplete', 'invalid')
READINESS = ('pending', 'ready', 'blocked', 'needs-user-decision', 'incomplete')
COLOR = re.compile(r'#[0-9a-fA-F]{6}\Z')


def e(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def obj(value, where):
    if type(value) is not dict:
        raise ValueError(f'{where} must be an object')
    return value


def arr(value, where):
    if type(value) is not list:
        raise ValueError(f'{where} must be an array')
    return value


def text(value, where):
    if type(value) is not str:
        raise ValueError(f'{where} must be text')
    return value


def strings(value, where, unique=False):
    arr(value, where)
    for item in value:
        text(item, where)
    if unique and len(value) != len(set(value)):
        raise ValueError(f'{where} contains duplicates')
    return value


def number(value, where, low, high, nullable=False, integer=False):
    if nullable and value is None:
        return
    if (type(value) not in (int, float) or not low <= value <= high
            or not math.isfinite(value) or (integer and type(value) is not int)):
        raise ValueError(f'{where} must be a finite {"integer" if integer else "number"} in {low}..{high}')


def json_tree(value, where='data'):
    if type(value) is dict:
        for key, item in value.items():
            text(key, where + ' key')
            json_tree(item, where + '.' + key)
    elif type(value) is list:
        for item in value:
            json_tree(item, where + '[]')
    elif type(value) is float and not math.isfinite(value):
        raise ValueError(where + ' must be finite')
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError(where + ' is not JSON data')


def optional_fields(record, fields, where):
    for key, kind in fields.items():
        if key in record and type(record[key]) is not kind:
            raise ValueError(f'{where}.{key} must be {kind.__name__}')


def validate_tally(tally, known):
    obj(tally, 'tally')
    optional_fields(tally, {'status': str, 'epoch': int}, 'tally')
    if tally.get('winner') is not None:
        text(tally['winner'], 'tally.winner')
    status = tally.get('status', 'incomplete')
    if status not in ELECTIONS:
        raise ValueError('unknown tally status')
    dims = strings(tally.get('dimensions', []), 'tally.dimensions', True)
    roster = strings(tally.get('roster', list(known)), 'tally.roster', True)
    if set(roster) - set(known):
        raise ValueError('tally roster contains unknown seats')
    number(tally.get('max_total', 10 * len(dims)), 'tally.max_total', 0, 10 * len(dims))
    if tally.get('winner') is not None and tally['winner'] not in roster:
        raise ValueError('tally winner is outside roster')
    if 'min_total' in tally:
        number(tally['min_total'], 'tally.min_total', len(dims), len(dims))
    if 'borda_max' in tally:
        number(tally['borda_max'], 'tally.borda_max', max(0, (len(roster) - 1) ** 2), max(0, (len(roster) - 1) ** 2), integer=True)
    tied = strings(tally.get('tied', []), 'tally.tied', True)
    if set(tied) - set(roster):
        raise ValueError('tally tie contains unknown seats')
    for key in ('invalid', 'errors'):
        for seat, error in obj(tally.get(key, {}), 'tally.' + key).items():
            text(error, 'tally.' + key + '.' + seat)
    for voter, candidate in obj(tally.get('top_picks', {}), 'tally.top_picks').items():
        if voter not in roster or candidate not in roster or voter == candidate:
            raise ValueError('tally top pick must identify another roster member')
    rows = arr(tally.get('proposals', []), 'tally.proposals')
    letters = []
    n = len(roster)
    for row in rows:
        obj(row, 'tally proposal')
        letter = text(row.get('letter'), 'tally proposal letter')
        if letter not in roster or letter in letters:
            raise ValueError('tally proposals must be unique roster members')
        letters.append(letter)
        for voter, score in obj(row.get('by', {}), 'tally.by').items():
            if voter not in roster:
                raise ValueError('tally scorer outside roster')
            number(score, 'tally.by score', len(dims), 10 * len(dims))
        for key in ('self', 'peer_avg'):
            number(row.get(key), 'tally.' + key, len(dims), 10 * len(dims), nullable=True)
        for dim, score in obj(row.get('peer_dim_avg', {}), 'tally.peer_dim_avg').items():
            if dim not in dims:
                raise ValueError('unknown score dimension')
            number(score, 'tally dimension average', 1, 10)
        number(row.get('borda', 0), 'tally.borda', 0, max(0, (n - 1) ** 2), integer=True)
        number(row.get('first_place', 0), 'tally.first_place', 0, max(0, n - 1), integer=True)
        if 'peer_count' in row:
            number(row['peer_count'], 'tally.peer_count', 0, max(0, n - 1), integer=True)
        for key in ('peer_range', 'peer_dim_range'):
            if key not in row:
                continue
            ranges = obj(row[key], key).values() if key == 'peer_dim_range' else [row[key]]
            if key == 'peer_dim_range' and set(row[key]) - set(dims):
                raise ValueError('unknown range dimension')
            for bounds in ranges:
                if bounds is None:
                    continue
                arr(bounds, key)
                if len(bounds) != 2:
                    raise ValueError(key + ' needs two bounds')
                low, high = (1, 10) if key == 'peer_dim_range' else (len(dims), 10 * len(dims))
                for bound in bounds:
                    number(bound, key, low, high)
                if bounds[0] > bounds[1]:
                    raise ValueError(key + ' bounds are reversed')
    if status in ('complete', 'tied'):
        if n < 3 or set(letters) != set(roster) or tally.get('invalid') or tally.get('errors'):
            raise ValueError('complete tally needs at least three complete proposals and no errors')
        if not dims or set(tally.get('top_picks', {})) != set(roster):
            raise ValueError('complete tally needs every voter and rubric dimension')
        for row in rows:
            if set(row.get('by', {})) != set(roster) or set(row.get('peer_dim_avg', {})) != set(dims):
                raise ValueError('complete tally needs all scores and dimensions')
        if status == 'complete' and (tally.get('winner') is None or tied):
            raise ValueError('complete tally needs one winner and no tie')
        if status == 'tied' and (tally.get('winner') is not None or len(tied) < 2):
            raise ValueError('tied tally needs at least two leaders and no winner')
    return tally


def validate_data(data):
    obj(data, 'data')
    json_tree(data)
    optional_fields(data, {key: str for key in ('title', 'folder', 'stats', 'proposals_title', 'decision', 'lifecycle', 'readiness')}, 'data')
    seats = arr(data.get('seats', []), 'seats')
    known = []
    for seat in seats:
        obj(seat, 'seat')
        letter = text(seat.get('letter'), 'seat.letter')
        if not letter or letter in known:
            raise ValueError('seat letters must be nonempty and unique')
        known.append(letter)
        optional_fields(seat, {'name': str, 'short': str, 'active': bool, 'model': str, 'providerInstanceId': str, 'model_identity': str, 'options': dict}, 'seat')
        if 'color' in seat:
            number(seat['color'], 'seat.color', 1, 6, integer=True)
        if 'dropped' in seat:
            strings(seat['dropped'], 'seat.dropped')
    for key in ('rounds', 'proposals', 'consensus', 'disputes', 'factcheck', 'user_calls', 'unknowns', 'actions', 'conditions', 'blockers', 'evidence', 'substitutions', 'findings'):
        if key in data:
            for record in arr(data[key], key):
                if type(record) not in (dict, str) or (key in ('rounds', 'proposals', 'consensus', 'disputes', 'factcheck', 'user_calls', 'unknowns', 'substitutions', 'findings') and type(record) is not dict):
                    raise ValueError(key + ' entries must be objects or plain text')
    for record in data.get('rounds', []):
        optional_fields(record, {'name': str, 'note': str, 'highlight': bool}, 'round')
    for record in data.get('proposals', []):
        if record.get('letter') not in known:
            raise ValueError('unknown proposal seat')
        optional_fields(record, {key: str for key in ('headline', 'r1', 'final', 'badge')}, 'proposal')
    rosters = obj(data.get('stage_rosters', {}), 'stage_rosters')
    for stage, roster in rosters.items():
        strings(roster, 'stage roster', True)
        if set(roster) - set(known):
            raise ValueError('stage roster contains unknown seats')
    for record in data.get('consensus', []):
        text(record.get('text'), 'consensus.text')
        strings(record.get('agree', []), 'consensus.agree', True)
        if set(record.get('agree', [])) - set(known):
            raise ValueError('agreement contains unknown seats')
        if 'roster' in record:
            strings(record['roster'], 'consensus.roster', True)
            if set(record['roster']) - set(known):
                raise ValueError('consensus roster contains unknown seats')
        optional_fields(record, {'stage': str}, 'consensus')
    for dispute in data.get('disputes', []):
        optional_fields(dispute, {key: str for key in ('id', 'question', 'outcome', 'status')}, 'dispute')
        for letter, position in obj(dispute.get('positions', {}), 'positions').items():
            if letter not in known:
                raise ValueError('unknown position seat')
            obj(position, 'position')
            optional_fields(position, {'label': str, 'changed': bool, 'reason': str, 'before': str, 'after': str}, 'position')
    if 'tally' in data and data['tally'] is not None:
        validate_tally(data['tally'], known)
    if 'score_chart' in data:
        chart = obj(data['score_chart'], 'score_chart')
        dims = strings(chart.get('dimensions'), 'score_chart.dimensions', True)
        low, high = chart.get('min', 1), chart.get('max', 10)
        number(low, 'score_chart.min', 0, 10)
        number(high, 'score_chart.max', 0, 10)
        if high <= low:
            raise ValueError('score chart range must increase')
        optional_fields(chart, {'title': str, 'note': str}, 'score_chart')
        for series in arr(chart.get('series'), 'score_chart.series'):
            obj(series, 'score series')
            text(series.get('label'), 'score series label')
            optional_fields(series, {'hollow': bool}, 'score series')
            if series.get('letter') is not None and series['letter'] not in known:
                raise ValueError('unknown score series seat')
            if 'color' in series and not COLOR.fullmatch(text(series['color'], 'score series color')):
                raise ValueError('custom colors must be six-digit hex colors')
            values = arr(series.get('values'), 'score series values')
            if len(values) != len(dims):
                raise ValueError('score values must match dimensions')
            for score in values:
                number(score, 'score series value', max(1, low), high, nullable=True)
    if 'telemetry' in data:
        telemetry = obj(data['telemetry'], 'telemetry')
        if telemetry.get('coverage') not in ('partial', 'complete') or not text(telemetry.get('source'), 'telemetry.source').strip():
            raise ValueError('telemetry needs partial/complete coverage and a source')
        for key in ('elapsed_seconds', 'cost'):
            if key in telemetry:
                number(telemetry[key], 'telemetry.' + key, 0, 1e12)
        for key in ('calls', 'retries', 'replacements', 'tokens'):
            if key in telemetry:
                number(telemetry[key], 'telemetry.' + key, 0, 10**15, integer=True)
        if 'cost' in telemetry and not re.fullmatch(r'[A-Z]{3}', text(telemetry.get('currency'), 'telemetry.currency')):
            raise ValueError('cost needs a three-letter currency')
    if 'redteam' in data:
        rt = obj(data['redteam'], 'redteam')
        for key in ('verdicts', 'issues'):
            for record in arr(rt.get(key, []), 'redteam.' + key):
                obj(record, 'redteam record')
        for verdict in rt.get('verdicts', []):
            if verdict.get('letter') not in known:
                raise ValueError('unknown critic')
            optional_fields(verdict, {'verdict': str, 'summary': str, 'plan_sha256': str}, 'verdict')
        for issue in rt.get('issues', []):
            optional_fields(issue, {'plan_sha256': str, 'disposition': str, 'verified': bool, 'must_fix': bool}, 'issue')
    for finding in data.get('findings', []):
        optional_fields(finding, {'plan_sha256': str, 'disposition': str, 'verified': bool, 'must_fix': bool}, 'finding')
    return data


def secure_artifact(root, ref):
    obj(ref, 'artifact reference')
    path = safe_path(root, text(ref.get('path'), 'artifact path'))
    digest = text(ref.get('sha256'), 'artifact sha256')
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('artifact hash mismatch: ' + str(ref['path']))
    return path


def load_report(source, historical=False):
    source = pathlib.Path(source)
    root = source if source.is_dir() else source.parent
    data = {} if source.is_dir() else read_json(source)
    obj(data, 'data')
    if historical:
        if source.is_dir():
            raise ValueError('--historical requires an explicit data.json file')
        validate_data(data)
        plan = data.get('final_plan', '')
        if type(plan) is dict:
            plan = plan.get('content', '')
        text(plan, 'historical final_plan')
        return data, None, plan, None, []
    manifest = load_manifest(root)
    for source_ref in manifest['sources']:
        secure_artifact(root, source_ref)
    # Explanatory fields can supplement the report but cannot replace run state.
    data = dict(data)
    data['title'] = data.get('title', 'Council: ' + manifest['topic'])
    data['seats'] = [dict(seat, name=seat.get('name', seat['model']), color=(i % 6) + 1)
                     for i, seat in enumerate(manifest['seats'])]
    data['lifecycle'] = manifest['status']
    data['readiness'] = manifest.get('readiness', 'pending')
    data['blockers'] = manifest.get('blockers', [])
    data['_verification_current'] = False
    data.pop('redteam', None)
    data.pop('telemetry', None)
    if 'telemetry' in manifest:
        data['telemetry'] = manifest['telemetry']
    for key in ('findings', 'substitutions', 'conditions', 'actions', 'evidence', 'user_calls'):
        if key in manifest:
            data[key] = manifest[key]
    data['stage_rosters'] = dict(manifest.get('stage_rosters', {}), r3=manifest['vote']['roster'])
    transcripts = []
    paths_by_stage = {}
    for stage in STAGES:
        paths = accepted_paths(root, manifest, stage)
        paths_by_stage[stage] = paths
        for letter, path in paths.items():
            transcripts.append((stage + ' · ' + letter, path.read_text(encoding='utf-8')))
    plan, plan_hash = '', None
    final = manifest.get('final_plan')
    if final:
        obj(final, 'final_plan')
        stage, letter = final.get('stage'), final.get('letter')
        if stage not in ('synthesis', 'repair') or letter not in paths_by_stage[stage]:
            raise ValueError('final_plan must select an accepted synthesis or repair artifact')
        path = paths_by_stage[stage][letter]
        plan = path.read_text(encoding='utf-8')
        plan_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if re.search(r'```json[^\S\n]*\n', plan, re.I):
            structured = last_json(path)
            json_tree(structured, 'accepted plan JSON')
            for source_key, display_key in (('next_actions', 'actions'), ('conditions', 'conditions'), ('supporting_evidence', 'evidence')):
                if source_key in structured:
                    data[display_key] = arr(structured[source_key], 'plan.' + source_key)
    if manifest['status'] == 'complete' and not plan.strip():
        raise ValueError('complete run has no accepted final plan')
    if manifest.get('final_verification'):
        load_final_check(root, manifest, data, plan_hash, paths_by_stage)
    elif manifest['status'] == 'complete':
        raise ValueError('complete run has no registered final verification')
    data.pop('tally', None)
    ref = manifest['vote'].get('tally')
    if ref:
        if ref.get('epoch') != manifest['vote']['epoch']:
            raise ValueError('tally reference belongs to a stale epoch')
        tally = read_json(secure_artifact(root, ref))
        obj(tally, 'tally')
        if tally.get('epoch') != manifest['vote']['epoch'] or tally.get('roster') != manifest['vote']['roster']:
            raise ValueError('tally epoch/roster differs from manifest')
        for key, stage in (('provenance', 'r3'), ('proposal_provenance', 'r2')):
            expected = manifest['accepted'].get(stage, {})
            if tally.get(key) != expected:
                raise ValueError('tally ' + key + ' differs from accepted artifacts')
        from tally import tally as recompute_tally
        if tally != recompute_tally(root):
            raise ValueError('registered tally differs from validated accepted ballots')
        data['tally'] = tally
    validate_data(data)
    return data, manifest, plan, plan_hash, transcripts


def load_final_check(root, manifest, data, plan_hash, accepted):
    """Bind the published finding dispositions to this plan and its critics."""
    document = obj(read_json(secure_artifact(root, manifest['final_verification'])), 'final verification')
    json_tree(document, 'final verification')
    if not plan_hash or document.get('final_sha256') != plan_hash:
        raise ValueError('final verification identifies a different plan hash')
    required = ('direction', 'evidence', 'contributions', 'strongest_objection',
                'conditions', 'actions', 'acceptance_checks', 'user_decisions')
    checks = obj(document.get('sections'), 'final section checks')
    if any(type(checks.get(key)) is not str or not checks[key].strip() for key in required):
        raise ValueError('final verification requires concrete section checks')
    decisions = arr(document.get('user_decisions'), 'final user decisions')
    if manifest['vote']['status'] == 'tied' and (not decisions or document.get('tied_alternatives') != manifest['vote'].get('tied')):
        raise ValueError('tied final plan must preserve alternatives and a user choice')
    data['user_calls'] = [value if type(value) is dict else {'question': text(value, 'user decision')} for value in decisions]
    provenance = obj(manifest.get('findings_provenance'), 'findings provenance')
    findings = arr(manifest.get('findings'), 'findings')
    if read_json(secure_artifact(root, provenance.get('artifact'))) != findings:
        raise ValueError('finding ledger differs from registered artifact')
    if provenance.get('redteam') != manifest['accepted'].get('redteam', {}):
        raise ValueError('finding ledger has stale red-team provenance')
    dispositions = arr(document.get('findings'), 'final dispositions')
    known = {text(obj(item, 'finding').get('id'), 'finding ID'): item for item in findings}
    ids = [text(obj(item, 'disposition').get('id'), 'disposition ID') for item in dispositions]
    if len(known) != len(findings) or len(set(ids)) != len(ids) or set(ids) != set(known):
        raise ValueError('final dispositions must cover every finding exactly once')
    critics = set(manifest['vote']['roster']) - {manifest['final_plan']['letter']}
    verified = {}
    verifier_documents = {}
    for letter, path in accepted['verify'].items():
        check = last_json(path)
        if check.get('final_sha256') != plan_hash:
            raise ValueError('critic verified a different final plan hash')
        entries = obj(check.get('findings'), 'critic dispositions')
        if set(entries) != set(known):
            raise ValueError('critic must address every finding')
        verified[letter] = entries
        verifier_documents[letter] = check
    current = set(verified) == critics
    merged = []
    for disposition in dispositions:
        identity = disposition['id']
        status = disposition.get('disposition')
        if status not in ('resolved', 'rejected', 'unresolved'):
            raise ValueError('unknown finding disposition')
        confirmations = []
        for entries in verified.values():
            item = obj(entries[identity], 'critic finding')
            optional_fields(item, {'verified': bool, 'disposition': str}, 'critic finding')
            confirmations.append(item.get('verified') is True and item.get('disposition') == status)
        confirmed = current and all(confirmations)
        if status != 'unresolved' and not confirmed:
            raise ValueError('resolved/rejected finding lacks exact-plan critic confirmation')
        merged.append({**known[identity], **disposition,
                       'severity': known[identity].get('severity', 'must'),
                       'hard_constraint': known[identity].get('hard_constraint', False),
                       'plan_sha256': plan_hash,
                       'verified': confirmed, 'current_plan': current})
    objections = evaluate_final_objections(verifier_documents, document.get('verifier_objections'))
    evidence_refs = {(ref['path'], ref['sha256']) for ref in manifest['sources']}
    evidence_refs.update((ref['path'], ref['sha256']) for group in manifest['accepted'].values() for ref in group.values())
    for objection in objections:
        for ref in objection['evidence']:
            if (ref.get('path'), ref.get('sha256')) not in evidence_refs:
                raise ValueError('verifier objection evidence must reference a frozen/accepted artifact')
            secure_artifact(root, ref)
        merged.append({**objection, 'plan_sha256': plan_hash, 'current_plan': current,
                       'verified': current and objection['disposition'] in ('resolved', 'rejected')})
    data['findings'] = merged
    data['_verification_current'] = current
    data['_section_checks'] = checks
    if manifest['status'] == 'complete' and not current:
        raise ValueError('complete run needs verification from every other seat')


def block(value):
    """Escape structured evidence without turning supplied strings into markup."""
    if type(value) is dict:
        return '<dl>' + ''.join(f'<dt>{e(key.replace("_", " "))}</dt><dd>{block(item)}</dd>' for key, item in value.items()) + '</dl>'
    if type(value) is list:
        return '<ul>' + ''.join('<li>' + block(item) + '</li>' for item in value) + '</ul>'
    return e(value)


def section(title, body):
    return f'<section><h2>{e(title)}</h2>{body}</section>'


def table(caption, headers, rows):
    heads = ''.join(f'<th scope="col">{e(header)}</th>' for header in headers)
    body = ''.join('<tr>' + ''.join(f'<{"th scope=\"row\"" if i == 0 else "td"}>{cell}</{"th" if i == 0 else "td"}>' for i, cell in enumerate(row)) + '</tr>' for row in rows)
    return f'<div class="scroll" tabindex="0" role="region" aria-label="{e(caption)}"><table><caption>{e(caption)}</caption><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></div>'


def elapsed_seconds(manifest, now=None):
    """Wall-clock duration needs timezone-aware timestamps, not provider estimates."""
    try:
        start = datetime.fromisoformat(manifest['created_at'].replace('Z', '+00:00'))
        if manifest['status'] == 'complete':
            end = datetime.fromisoformat(manifest['completed_at'].replace('Z', '+00:00'))
        elif manifest['status'] == 'running':
            end = now or datetime.now(timezone.utc)
        else:
            return None
        if start.utcoffset() is None or end.utcoffset() is None:
            return None
        seconds = (end - start).total_seconds()
        return seconds if seconds >= 0 else None
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def render_report(data, manifest=None, plan='', plan_hash=None, transcripts=(), *, inline=False):
    validate_data(data)
    historical = manifest is None
    seats = {seat['letter']: seat for seat in data.get('seats', [])}
    def name(letter):
        return seats.get(letter, {}).get('short') or seats.get(letter, {}).get('name', letter)
    def tag(letter):
        color = seats.get(letter, {}).get('color', 1)
        return f'<span class="dot s{color}" aria-hidden="true"></span>{e(name(letter))}'
    title = data.get('title', 'Council decision report')
    out = [f'<h1>{e(title)}</h1>']
    if historical:
        out.append('<p class="notice">Historical report · supplied data is unverified. This report does not establish live acceptance or resumable state.</p>')
    out.append(section('Final decision plan', '<div class="plan">' + e(plan) + '</div>' if plan else '<p>No accepted final plan is available yet.</p>'))
    if plan_hash:
        out.append('<p class="muted">Accepted plan SHA-256: <code>' + e(plan_hash) + '</code></p>')
    for key, title_text in (('actions', 'Next actions'), ('conditions', 'Conditions for execution')):
        if data.get(key):
            out.append(section(title_text, block(data[key])))
    tally = data.get('tally') or {}
    election = tally.get('status', 'incomplete' if tally else 'pending')
    if manifest and election != manifest['vote']['status']:
        election = 'incomplete'
    issues = data.get('findings', []) + data.get('redteam', {}).get('issues', [])
    readiness = data.get('readiness', 'incomplete' if historical else 'pending')
    if readiness not in READINESS:
        raise ValueError('unknown readiness')
    lifecycle = data.get('lifecycle', 'unverified' if historical else 'incomplete')
    if lifecycle not in ('running', 'incomplete', 'complete', 'unverified'):
        raise ValueError('unknown lifecycle')
    if data.get('blockers'):
        readiness = 'blocked'
    elif any(finding_blocks({**i, 'disposition': 'unresolved'}) and not (i.get('verified') is True and i.get('plan_sha256') == plan_hash and plan_hash and i.get('disposition') in ('resolved', 'rejected')) for i in issues):
        readiness = 'blocked'
    elif data.get('user_calls') and readiness == 'ready':
        readiness = 'needs-user-decision'
    elif readiness == 'ready' and (not plan or election not in ('complete', 'tied') or historical or data.get('_verification_current') is not True):
        readiness = 'incomplete'
    outcome = election
    # A stale or incomplete election can never acquire an official winner badge.
    if election == 'complete' and tally.get('winner') and not historical:
        outcome += ' · Winner: ' + name(tally['winner'])
    elif election == 'tied':
        outcome += ' · Alternatives: ' + ', '.join(name(letter) for letter in tally.get('tied', []))
    out.append(section('Readiness and run status', '<div class="status">' + ''.join(f'<p><strong>{e(label)}</strong><br>{e(value)}</p>' for label, value in (('Readiness', readiness), ('Lifecycle', lifecycle), ('Election', outcome))) + '</div>'))
    for key, title_text in (('blockers', 'Blockers'), ('user_calls', 'Your choices'), ('unknowns', 'Open questions'), ('evidence', 'Supporting evidence'), ('factcheck', 'Fact-checks')):
        if data.get(key):
            out.append(section(title_text, block(data[key])))
    if issues or data.get('redteam'):
        rows = []
        for issue in issues:
            current = bool(plan_hash and issue.get('plan_sha256') == plan_hash and issue.get('verified') is True)
            rows.append([e(issue.get('id', issue.get('issue', 'Finding'))), block(issue), e('Verified against current plan' if current else 'Current plan; unresolved disposition' if issue.get('current_plan') else 'Unverified against current plan')])
        out.append(section('Red-team findings and dispositions', table('Dispositions refer to the accepted final version', ['Finding', 'Evidence and disposition', 'Version check'], rows) + block(data.get('redteam', {}).get('verdicts', []))))
    if data.get('consensus'):
        rows = []
        for item in data['consensus']:
            stage = item.get('stage', 'r3')
            roster = (data.get('stage_rosters', {}).get(stage, item.get('roster')) if manifest
                      else item.get('roster', data.get('stage_rosters', {}).get(stage)))
            if roster is None and 'stage' in item:
                rows.append([e(item['text']), 'Stage roster unavailable', 'Count not inferred'])
                continue
            if roster is None:
                roster = [letter for letter, seat in seats.items() if seat.get('active', True) and not seat.get('dropped')]
            agreement = [letter for letter in roster if letter in item.get('agree', [])]
            dropped = len(set(seats) - set(roster))
            count = f'{len(agreement)}/{len(roster)} remaining; {dropped} dropped'
            rows.append([e(item['text']), e(count), e(', '.join(name(letter) for letter in agreement))])
        out.append(section('Agreement', table('Agreement within the relevant stage roster', ['Position', 'Count', 'Agreeing seats'], rows)))
    if data.get('disputes'):
        rows = []
        for dispute in data['disputes']:
            positions = []
            for letter, position in dispute.get('positions', {}).items():
                detail = '<p>' + tag(letter) + ': ' + e(position.get('label', '')) + '</p>'
                if position.get('changed'):
                    detail += '<details><summary>Changed position · ' + e(name(letter)) + '</summary><div>' + block({key: position.get(key, 'Not supplied') for key in ('before', 'after', 'reason')}) + '</div></details>'
                positions.append(detail)
            rows.append([e(dispute.get('id', '')) + ' ' + e(dispute.get('question', '')), ''.join(positions), e(dispute.get('outcome', ''))])
        out.append(section('Changes of mind and disputes', table('Positions and reasons', ['Question', 'Positions', 'Outcome'], rows)))
    if data.get('proposals'):
        rows = [[tag(p['letter']), e(p.get('r1', p.get('headline', ''))), e(p.get('final', ''))] for p in data['proposals']]
        out.append(section(data.get('proposals_title', 'Proposal history'), table('Independent and revised proposals', ['Seat', 'Initial proposal', 'After debate'], rows)))
    charts = []
    if tally.get('dimensions'):
        charts.append( {'dimensions': tally['dimensions'], 'series': [{'label': name(r['letter']), 'values': [r.get('peer_dim_avg', {}).get(d) for d in tally['dimensions']]} for r in tally.get('proposals', [])]})
    if data.get('score_chart'):
        supplied = dict(data['score_chart'])
        supplied['title'] = 'Supplied comparison: ' + supplied.get('title', 'Scores')
        charts.append(supplied)
    for chart in charts:
        rows = []
        for series in chart['series']:
            cells = [e(series['label'])]
            for dimension, value in zip(chart['dimensions'], series['values']):
                label = 'Unavailable' if value is None else str(value)
                detail = series['label'] + ' · ' + dimension + ': ' + label
                cells.append(f'<button type="button" data-tip="{e(detail)}" aria-label="{e(detail)}">{e(label)}</button>')
            rows.append(cells)
        out.append(section(chart.get('title', 'Peer scores by dimension'), '<p>' + e(chart.get('note', 'Peer averages exclude self-scores. Scores range from 1 to 10.')) + '</p>' + table('All dimension scores, available without hover', ['Proposal'] + chart['dimensions'], rows)))
    if tally:
        roster = tally.get('roster', list(seats))
        rows = []
        for row in tally.get('proposals', []):
            bounds = row.get('peer_range')
            spread = '–'.join(str(value) for value in bounds) if bounds else 'Unavailable'
            rows.append([tag(row['letter'])] + [e(row.get('by', {}).get(v, 'Unavailable')) for v in roster] + [e(row.get('peer_avg', 'Unavailable')), e(spread), e(row.get('peer_count', 'Unavailable')), e(row.get('borda', 0)), e(row.get('first_place', 0))])
        out.append(section('Scores and ranked vote', '<p>Panel preference uses Borda. Self-scores are shown but excluded from peer averages. Scores total up to ' + e(tally.get('max_total', 'Unavailable')) + '.</p>' + table('Election: ' + election, ['Proposal'] + ['By ' + name(v) for v in roster] + ['Peer average', 'Peer range', 'Peer count', 'Borda', 'First choices'], rows) + block({'invalid': tally.get('invalid', {}), 'errors': tally.get('errors', {})})))
    if data.get('substitutions'):
        out.append(section('Substitution history', block(data['substitutions'])))
    telemetry = data.get('telemetry', {})
    elapsed = elapsed_seconds(manifest) if manifest else telemetry.get('elapsed_seconds')
    measurements = [('Wall-clock elapsed seconds', elapsed if elapsed is not None else 'Unavailable'),
                    ('Tokens', telemetry.get('tokens', 'Unavailable; not zero')),
                    ('Cost', str(telemetry['cost']) + ' ' + telemetry['currency'] if 'cost' in telemetry else 'Unavailable; not zero')]
    out.append(section('Run measurements', table('Recorded measurements; usage is unavailable unless supplied', ['Measurement', 'Value'], [[e(label), e(value)] for label, value in measurements])))
    if manifest:
        tasks = manifest['tasks']
        letters = list(dict.fromkeys(list(seats) + [task['letter'] for task in tasks.values()]))
        counts = []
        for letter in letters:
            attempts = [task for task in tasks.values() if task['letter'] == letter]
            counts.append([e(letter), e(len(attempts)), e(sum(bool(t.get('task_id')) for t in attempts)), e(sum(t.get('attempt', 1) > 1 for t in attempts))])
        out.append(section('Per-seat task counts', table('Stored task attempts, including previous actors and epochs', ['Seat', 'Stored task attempts', 'With delegated task IDs', 'Retry attempts'], counts)))
    provenance = []
    for letter, seat in seats.items():
        provenance.append([e(letter), e(seat.get('name', seat.get('model', 'Unknown'))), e(seat.get('providerInstanceId', 'Unavailable')), e(seat.get('model', 'Unavailable')), e('active' if seat.get('active', True) else 'dropped')])
    out.append(section('Panel and provenance', table('Actual participants; substitutions retain their history', ['Seat', 'Label', 'Provider', 'Model', 'State'], provenance)))
    if transcripts:
        out.append(section('Accepted transcripts', ''.join('<details><summary>' + e(label) + '</summary><div><pre>' + e(content) + '</pre></div></details>' for label, content in transcripts)))
    diagnostics = {'stats': data.get('stats', 'Unavailable'), 'telemetry': data.get('telemetry', 'Unavailable; not zero')}
    if manifest:
        tasks = manifest['tasks']
        diagnostics.update({'run_id': manifest['run_id'], 'stage': manifest['stage'], 'delegated_tasks_with_ids': sum(bool(t.get('task_id')) for t in tasks.values()), 'retry_attempts': sum(t.get('attempt', 1) > 1 for t in tasks.values()), 'replacements': len(data.get('substitutions', [])), 'tasks': tasks, 'section_checks': data.get('_section_checks', 'Not yet verified')})
    if data.get('rounds'):
        diagnostics['rounds'] = data['rounds']
    if data.get('folder'):
        diagnostics['folder'] = data['folder']
    out.append('<details><summary>Diagnostics and telemetry</summary><div>' + block(diagnostics) + '</div></details>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + e(title) + '</title><style>' + CSS + ('body{background:transparent}main{max-width:none;padding:0}' if inline else '') + '</style></head><body><main>' + ''.join(out)
            + '</main><div id="tip" class="tip" role="status" aria-live="polite" hidden></div><script>' + TOOLTIPS + '</script></body></html>')


def protect_output(source, output, manifest):
    if output.is_symlink():
        raise ValueError('report output cannot be a symlink')
    protected = [source.resolve()] if source.is_file() else []
    if manifest:
        root = source if source.is_dir() else source.parent
        refs = []
        def collect_refs(value):
            if type(value) is dict:
                if type(value.get('path')) is str and type(value.get('sha256')) is str:
                    refs.append(value)
                for item in value.values():
                    collect_refs(item)
            elif type(value) is list:
                for item in value:
                    collect_refs(item)
        collect_refs(manifest)
        protected.append((root / '_moderator/run.json').resolve())
        protected.extend((root / task['output_path']).resolve() for task in manifest['tasks'].values())
        protected.extend((root / ref['path']).resolve() for ref in refs)
    if output.resolve() in protected or (output.exists() and any(path.exists() and output.samefile(path) for path in protected)):
        raise ValueError('report output would overwrite an input or immutable run artifact')


def write_report(output, page):
    # Replacing the directory entry also preserves other hard links during races.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.' + output.name + '.', delete=False) as stream:
            temporary = stream.name
            stream.write(page)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--historical', action='store_true')
    parser.add_argument('--inline', action='store_true', help='align the report with T3 inline text, without outer padding')
    parser.add_argument('source', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        report = load_report(args.source, args.historical)
        page = render_report(*report, inline=args.inline)
        protect_output(args.source, args.output, report[1])
        write_report(args.output, page)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as error:
        print('Cannot render Council report: ' + str(error), file=sys.stderr)
        return 2
    print(f'wrote {args.output} ({len(page):,} chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

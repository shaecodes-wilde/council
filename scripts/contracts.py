#!/usr/bin/env python3
"""Strict shared Council manifest and artifact boundaries (stdlib only)."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

STAGES = ('prepare', 'r1', 'r2', 'r3', 'synthesis', 'redteam', 'repair', 'verify', 'complete')
TASK_STAGES = STAGES[1:-1]
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out', 'interrupted'}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f'nonfinite JSON constant: {value}')


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'nonfinite JSON number: {value}')
    return number


def _loads(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f'invalid JSON: {exc}') from exc


def read_json(path):
    return _loads(Path(path).read_text(encoding='utf-8'))


def last_json(path):
    blocks = re.findall(r'```json[^\S\n]*\n(.*?)\n```', Path(path).read_text(encoding='utf-8'), re.S | re.I)
    if not blocks:
        raise ValueError(f'{path}: no fenced JSON object')
    value = _loads(blocks[-1])
    if type(value) is not dict:
        raise ValueError(f'{path}: last fenced JSON must be an object')
    return value


def atomic_json(path, value):
    """A failed encode/write/replace leaves the previous destination intact."""
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f'symlink destination: {path}')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', delete=False) as stream:
            name = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        name = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if name is not None:
            os.unlink(name)


def safe_path(root, relative, *, exists=True):
    """Require a plain relative run-local path, with no symlink components."""
    root = Path(root).resolve(strict=True)
    if type(relative) is not str or not relative or '\\' in relative:
        raise ValueError('artifact path must be a nonempty relative POSIX path')
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts or relative == '.':
        raise ValueError(f'unsafe artifact path: {relative}')
    path = root
    for component in part.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError(f'symlink artifact path: {relative}')
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'artifact escapes run: {relative}')
    if exists and not path.is_file():
        raise ValueError(f'artifact is not a file: {relative}')
    return path


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def successful(task):
    return (task.get('status') == 'completed' and task.get('hasPendingChildRuns') is False
            and task.get('workState') == 'result_available')


def settled(task):
    """A terminal parent alone does not establish that its child work is done."""
    return (task.get('hasPendingChildRuns') is False and
            (successful(task) or task.get('status') in TERMINAL - {'completed'}))


def finding_blocks(finding):
    severity = finding.get('severity', 'must')
    if type(severity) is not str or severity not in {'must', 'should'}:
        raise ValueError('finding severity must be must or should')
    hard = finding.get('hard_constraint', False)
    if type(hard) is not bool:
        raise ValueError('finding hard_constraint must be boolean')
    return finding.get('disposition', 'unresolved') == 'unresolved' and (hard or severity == 'must')


def evaluate_final_objections(verifications, assessments=None):
    """Normalize critic objections; callers check exact hashes and evidence refs."""
    if type(verifications) is not dict or (assessments is not None and type(assessments) is not list):
        raise ValueError('verifications must be an object; verifier_objections must be an array')
    reviewed = {}
    for item in assessments or []:
        if (type(item) is not dict or any(type(item.get(k)) is not str or not item[k].strip()
                                         for k in ('critic', 'id'))):
            raise ValueError('verifier objection assessment needs critic and id')
        key = (item['critic'], item['id'])
        if key in reviewed:
            raise ValueError('duplicate verifier objection assessment')
        reviewed[key] = item
    results = []
    seen = set()
    for critic, data in verifications.items():
        if type(critic) is not str or type(data) is not dict:
            raise ValueError('invalid critic verification')
        verdict = data.get('verdict', 'ship')
        if type(verdict) is not str or verdict not in {'ship', 'ship-after-fixes', 'block'}:
            raise ValueError(f'{critic}: invalid verification verdict')
        objections = data.get('remaining_blockers', [])
        if type(objections) is not list:
            raise ValueError(f'{critic}: remaining_blockers must be an array')
        if not objections and verdict != 'ship':
            objections = [{'id': 'verdict', 'issue': f'Critic verdict: {verdict}'}]
        for objection in objections:
            if (type(objection) is not dict or type(objection.get('id')) is not str
                    or not objection['id'].strip()):
                raise ValueError(f'{critic}: remaining blocker needs an id')
            key = (critic, objection['id'])
            if key in seen:
                raise ValueError(f'{critic}: duplicate remaining blocker id')
            seen.add(key)
            description = objection.get('issue', objection.get('description'))
            if type(description) is not str or not description.strip():
                raise ValueError(f'{critic}/{key[1]}: blocker description required')
            assessment = reviewed.get(key, {})
            disposition = assessment.get('disposition', 'unresolved')
            if type(disposition) is not str or disposition not in {'resolved', 'rejected', 'unresolved'}:
                raise ValueError(f'{critic}/{key[1]}: invalid objection disposition')
            refs = assessment.get('evidence', [])
            rationale = assessment.get('rationale', '')
            if type(refs) is not list or type(rationale) is not str:
                raise ValueError('objection evidence must be an array and rationale a string')
            if disposition != 'unresolved':
                if not rationale.strip() or not refs:
                    raise ValueError('resolved/rejected verifier objections need evidence and rationale')
                for ref in refs:
                    if type(ref) is not dict or any(type(ref.get(k)) is not str or not ref[k] for k in ('path', 'sha256')):
                        raise ValueError('objection evidence requires path/sha256 references')
            result = {'critic': critic, 'id': key[1], 'description': description,
                      'severity': objection.get('severity', 'must'),
                      'hard_constraint': objection.get('hard_constraint', False),
                      'disposition': disposition, 'evidence': refs, 'rationale': rationale}
            result['blocking'] = finding_blocks(result)
            results.append(result)
    if set(reviewed) - seen:
        raise ValueError('assessment references an unknown verifier objection')
    return results


def model_identity(seat):
    identity = seat.get('model_identity')
    if type(identity) is not str or not identity.strip() or identity.lower() in {'unknown', 'default'}:
        raise ValueError(f"seat {seat.get('letter')}: verified underlying model_identity required")
    return identity.strip().casefold()


def validate_roster(manifest, roster=None):
    roster = manifest['vote']['roster'] if roster is None else roster
    if type(roster) is not list or len(roster) < 3 or any(type(x) is not str for x in roster) or len(set(roster)) != len(roster):
        raise ValueError('voting requires at least three unique seat letters')
    seats = {seat['letter']: seat for seat in manifest['seats']}
    if any(letter not in seats or not seats[letter]['active'] for letter in roster):
        raise ValueError('roster contains an unknown/inactive seat')
    identities = [model_identity(seats[letter]) for letter in roster]
    if len(set(identities)) != len(identities):
        raise ValueError('voting requires distinct underlying models; aliases/options do not count')
    return roster


def load_manifest(root):
    path = safe_path(root, '_moderator/run.json')
    manifest = read_json(path)
    if type(manifest) is not dict or type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1:
        raise ValueError('expected manifest schema_version 1')
    required = {'run_id', 'owner_thread_id', 'created_at', 'topic', 'stage', 'status', 'rubric',
                'seats', 'sources', 'vote', 'tasks', 'accepted', 'blockers'}
    if required - manifest.keys():
        raise ValueError(f'manifest missing fields: {sorted(required - manifest.keys())}')
    for key in ('run_id', 'owner_thread_id', 'created_at', 'topic'):
        if type(manifest[key]) is not str or not manifest[key].strip():
            raise ValueError(f'manifest {key} must be a nonempty string')
    if type(manifest['stage']) is not str or type(manifest['status']) is not str or manifest['stage'] not in STAGES or manifest['status'] not in {'running', 'incomplete', 'complete'}:
        raise ValueError('invalid manifest stage/status')
    if type(manifest.get('readiness', 'pending')) is not str or manifest.get('readiness', 'pending') not in {'pending', 'ready', 'blocked', 'needs-user-decision', 'incomplete'}:
        raise ValueError('invalid readiness')
    rubric = manifest['rubric']
    if type(rubric) is not list or not rubric:
        raise ValueError('rubric must be a nonempty array')
    names = []
    for item in rubric:
        if type(item) is not dict or any(type(item.get(k)) is not str or not item[k].strip() for k in ('name', 'definition', 'low', 'mid', 'high')):
            raise ValueError('rubric requires name, definition and low/mid/high anchors')
        if item.get('direction') != 'higher' or type(item.get('weight')) is not int or item['weight'] != 1:
            raise ValueError('rubric must use higher direction and equal integer weights of 1')
        names.append(item['name'])
    if len(set(names)) != len(names):
        raise ValueError('duplicate rubric dimension')
    seats = manifest['seats']
    if type(seats) is not list or not seats:
        raise ValueError('seats must be a nonempty array')
    letters = []
    for seat in seats:
        if type(seat) is not dict or type(seat.get('letter')) is not str or not re.fullmatch('[A-Z]', seat.get('letter', '')):
            raise ValueError('seat letter must be A..Z')
        for key in ('providerInstanceId', 'model'):
            if type(seat.get(key)) is not str or not seat[key]:
                raise ValueError(f'seat requires {key}')
        if type(seat.get('options')) is not dict or type(seat.get('active')) is not bool:
            raise ValueError('seat requires options object and active boolean')
        letters.append(seat['letter'])
    if len(set(letters)) != len(letters):
        raise ValueError('duplicate seat letter')
    vote = manifest['vote']
    if type(vote) is not dict or type(vote.get('epoch')) is not int or vote['epoch'] < 1:
        raise ValueError('vote epoch must be a positive integer')
    if type(vote.get('status')) is not str or vote.get('status') not in {'pending', 'complete', 'tied', 'incomplete', 'invalid'}:
        raise ValueError('invalid vote status')
    if type(vote.get('roster')) is not list or any(type(x) is not str for x in vote['roster']) or len(set(vote['roster'])) != len(vote['roster']) or set(vote['roster']) - set(letters):
        raise ValueError('invalid vote roster')
    for key in ('tasks', 'accepted'):
        if type(manifest[key]) is not dict:
            raise ValueError(f'{key} must be an object')
    for key in ('sources', 'blockers'):
        if type(manifest[key]) is not list:
            raise ValueError(f'{key} must be an array')
    for request, task in manifest['tasks'].items():
        if type(task) is not dict or task.get('stage') not in TASK_STAGES or task.get('letter') not in letters:
            raise ValueError(f'invalid task {request}')
        if type(task.get('attempt')) is not int or task['attempt'] < 1 or type(task.get('epoch')) is not int or task['epoch'] < 1:
            raise ValueError(f'invalid attempt/epoch for {request}')
        if task.get('task_id') is not None and (type(task['task_id']) is not str or not task['task_id']):
            raise ValueError(f'invalid task_id for {request}')
        if type(task.get('status')) is not str or task['status'] not in {'intent', 'queued', 'running'} | TERMINAL or type(task.get('workState')) is not str:
            raise ValueError(f'invalid status/workState for {request}')
        safe_path(root, task.get('output_path'), exists=False)
        if type(task.get('target')) is not dict or type(task.get('hasPendingChildRuns')) is not bool:
            raise ValueError(f'invalid target/child state for {request}')
    for stage, artifacts in manifest['accepted'].items():
        if stage not in TASK_STAGES or type(artifacts) is not dict or set(artifacts) - set(letters):
            raise ValueError(f'invalid accepted stage {stage}')
    if manifest['status'] == 'complete' and (manifest['stage'] != 'complete' or not manifest.get('final_plan')):
        raise ValueError('complete manifest requires final_plan and complete stage')
    return manifest


def dependency_snapshot(manifest, stage):
    index = STAGES.index(stage)
    return copy.deepcopy({'sources': manifest['sources'], 'accepted': {
        name: artifacts for name, artifacts in manifest['accepted'].items()
        if STAGES.index(name) < index and (name != 'r1' or stage != 'r1')
    }})


def actor_seat(manifest, stage, letter):
    override = manifest.get('stage_actors', {}).get(stage, {}).get(letter)
    return override if override is not None else next(s for s in manifest['seats'] if s['letter'] == letter)


def validate_actors(manifest):
    """Post-vote roles use actual authors and critics, never historical voters."""
    author = manifest['vote'].get('author')
    if not author:
        return
    identities = {}
    for letter in manifest['vote']['roster']:
        stages = ('synthesis', 'repair') if letter == author else ('redteam', 'verify')
        identities[letter] = {model_identity(actor_seat(manifest, stage, letter)) for stage in stages}
        for stage in stages:
            ref = manifest['accepted'].get(stage, {}).get(letter)
            if ref:
                task = manifest['tasks'][ref['request_id']]
                if 'model_identity' in task:
                    identities[letter].add(model_identity(task))
    for letter, models in identities.items():
        if any(models & peers for peer, peers in identities.items() if peer != letter):
            raise ValueError('actor model duplicates an actual author or effective peer actor')


def eligible_task(root, manifest, request_id):
    if type(request_id) is not str or not request_id:
        raise ValueError('artifact requires a nonempty request_id')
    task = manifest['tasks'].get(request_id)
    if not task or not task.get('task_id') or not successful(task):
        raise ValueError(f'{request_id}: task must be completed, result_available, with no pending child runs')
    if task.get('superseded'):
        raise ValueError(f'{request_id}: superseded task')
    if task['stage'] != 'r1' and task['epoch'] != manifest['vote']['epoch']:
        raise ValueError(f'{request_id}: stale voting epoch')
    seat = actor_seat(manifest, task['stage'], task['letter'])
    target = {k: seat[k] for k in ('providerInstanceId', 'model', 'options')}
    if (task['target'] != target or not seat['active'] or
            ('model_identity' in task and task['model_identity'] != seat.get('model_identity'))):
        raise ValueError(f'{request_id}: changed or inactive seat')
    if 'dependencies' in task and task['dependencies'] != dependency_snapshot(manifest, task['stage']):
        raise ValueError(f'{request_id}: stale dependencies')
    raw = safe_path(root, task['output_path'])
    if not task.get('raw_sha256') or sha256(raw) != task['raw_sha256']:
        raise ValueError(f'{request_id}: immutable raw output hash mismatch/missing')
    return task


def accepted_paths(root, manifest, stage):
    if stage not in TASK_STAGES:
        raise ValueError(f'unknown artifact stage: {stage}')
    paths = {}
    for letter, artifact in manifest['accepted'].get(stage, {}).items():
        if type(artifact) is not dict:
            raise ValueError(f'{stage}/{letter}: artifact must be an object')
        request = artifact.get('request_id')
        task = eligible_task(root, manifest, request)
        if task['stage'] != stage or task['letter'] != letter or artifact.get('epoch') != task['epoch']:
            raise ValueError(f'{stage}/{letter}: artifact task/stage/seat/epoch mismatch')
        if stage != 'r1' and artifact['epoch'] != manifest['vote']['epoch']:
            raise ValueError(f'{stage}/{letter}: stale artifact epoch')
        path = safe_path(root, artifact.get('path'))
        if path == safe_path(root, task['output_path']):
            raise ValueError(f'{stage}/{letter}: published artifact must differ from raw output')
        if sha256(path) != artifact.get('sha256'):
            raise ValueError(f'{stage}/{letter}: accepted artifact hash mismatch')
        paths[letter] = path
    return paths


def validate_ballot(ballot, voter, roster, dimensions):
    if type(roster) is not list or any(type(x) is not str for x in roster) or len(set(roster)) != len(roster) or voter not in roster:
        raise ValueError('voter/roster must identify unique known candidates')
    if type(dimensions) is not list or not dimensions or any(type(x) is not str for x in dimensions) or len(set(dimensions)) != len(dimensions):
        raise ValueError('dimensions must be a nonempty unique string list')
    if type(ballot) is not dict:
        raise ValueError('ballot must be a JSON object')
    for key in ('voter', 'letter'):
        if key in ballot and ballot[key] != voter:
            raise ValueError(f'ballot {key} does not match voter {voter}')
    ranking = ballot.get('ranking')
    if type(ranking) is not list or any(type(x) is not str for x in ranking):
        raise ValueError('ranking must be a list of candidate letters')
    expected = set(roster) - {voter}
    if len(ranking) != len(expected) or set(ranking) != expected:
        raise ValueError(f'ranking must contain every candidate except self exactly once: {sorted(expected)}')
    scores = ballot.get('scores')
    if type(scores) is not dict or set(scores) != set(roster):
        raise ValueError('scores must cover exactly all candidates, including self')
    for candidate, values in scores.items():
        if type(values) is not dict or set(values) != set(dimensions):
            raise ValueError(f'{candidate}: scores must cover exactly dimensions {dimensions}')
        for dimension, value in values.items():
            if type(value) not in (int, float) or not 1 <= value <= 10 or not math.isfinite(value) or value * 2 != int(value * 2):
                raise ValueError(f'{candidate}/{dimension}: score must be finite 1..10 in 0.5 steps (not boolean)')
    return ballot

#!/usr/bin/env python3
"""Council's serial manifest writer. Records T3 facts; never invokes T3/models."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import random
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts import (STAGES, TASK_STAGES, TERMINAL, accepted_paths, atomic_json,
                       actor_seat, dependency_snapshot, eligible_task, last_json, load_manifest,
                       read_json, safe_path, sha256, successful, settled, validate_ballot, validate_roster,
                       model_identity, validate_actors, finding_blocks, evaluate_final_objections)

FINAL_SECTIONS = ('direction', 'evidence', 'contributions', 'strongest_objection',
                  'conditions', 'actions', 'acceptance_checks', 'user_decisions')


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextmanager
def writer(root, owner):
    root = Path(root).resolve(strict=True)
    lock = safe_path(root, '_moderator/write.lock', exists=False)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        manifest = load_manifest(root)
        require(manifest['owner_thread_id'] == owner, 'resume/mutation requires the owning thread')
        yield manifest
        atomic_json(safe_path(root, '_moderator/run.json'), manifest)


def validate_sources(root, manifest):
    seen = set()
    for source in manifest['sources']:
        require(type(source) is dict, 'source must be an artifact record')
        path = safe_path(root, source.get('path'))
        require(source['path'] not in seen, 'duplicate frozen source path')
        seen.add(source['path'])
        require(sha256(path) == source.get('sha256'), f'frozen source changed: {source["path"]}')


def artifact_ref(root, relative):
    path = safe_path(root, relative)
    return {'path': relative, 'sha256': sha256(path)}


def check_ref(root, ref):
    require(type(ref) is dict, 'artifact reference must be an object')
    path = safe_path(root, ref.get('path'))
    require(sha256(path) == ref.get('sha256'), f'artifact hash mismatch: {ref.get("path")}')
    return path


def check_all(root, manifest):
    validate_sources(root, manifest)
    for stage in manifest['accepted']:
        accepted_paths(root, manifest, stage)


def participants(manifest, stage):
    roster = manifest['vote']['roster']
    if stage in ('synthesis', 'repair'):
        require(manifest['vote'].get('author') in roster, 'select a synthesis author first')
        return [manifest['vote']['author']]
    if stage in ('redteam', 'verify'):
        require(manifest['vote'].get('author') in roster, 'select a synthesis author first')
        return [letter for letter in roster if letter != manifest['vote']['author']]
    return roster


def barrier(root, manifest, stage):
    if stage in ('synthesis', 'redteam', 'repair', 'verify'):
        validate_actors(manifest)
    paths = accepted_paths(root, manifest, stage)
    expected = participants(manifest, stage)
    require(set(paths) == set(expected), f'{stage} requires accepted artifacts for exactly {expected}')


def init(args):
    parent = Path(args.parent).resolve(strict=True)
    seats, rubric = read_json(args.seats), read_json(args.rubric)
    slug = re.sub(r'[^a-z0-9]+', '-', args.topic.lower()).strip('-')[:48] or 'council'
    run_id = now()[:10] + '-' + slug + '-' + uuid.uuid4().hex[:12]
    root = parent / run_id
    root.mkdir()
    try:
        (root / '_moderator').mkdir(mode=0o700)
        sources = read_json(args.sources) if args.sources else []
        manifest = dict(schema_version=1, run_id=run_id, owner_thread_id=args.owner_thread,
                        created_at=now(), topic=args.topic, stage='prepare', status='running',
                        readiness='pending', rubric=rubric, seats=seats, sources=sources,
                        vote={'epoch': 1, 'roster': [s['letter'] for s in seats if s['active']], 'status': 'pending'},
                        tasks={}, accepted={}, blockers=[], substitutions=[], findings=[])
        # Snapshots must already be prepared within this new run; record them separately.
        require(not sources, 'init sources must be empty; prepare snapshots then use freeze-sources')
        atomic_json(root / '_moderator/run.json', manifest)
        load_manifest(root)
        validate_roster(manifest)
    except Exception:
        # Keep the failed run for diagnosis, but never leave a resumable invalid manifest.
        path = root / '_moderator/run.json'
        if path.exists():
            path.unlink()
        raise
    return {'run': str(root), 'run_id': run_id}


def invalidate(manifest, start, reason, letter=None):
    index = STAGES.index(start)
    affected = [task for task in manifest['tasks'].values()
                if STAGES.index(task['stage']) >= index and not
                (task['stage'] == 'r1' and letter and task['letter'] != letter)]
    require(all(settled(task) for task in affected), 'reconcile/cancel affected tasks before invalidation')
    removed = {}
    for stage in list(manifest['accepted']):
        if STAGES.index(stage) >= index:
            if stage == 'r1' and letter:
                value = manifest['accepted'][stage].pop(letter, None)
                if value:
                    removed[stage] = {letter: value}
            else:
                removed[stage] = manifest['accepted'].pop(stage)
    for task in manifest['tasks'].values():
        if STAGES.index(task['stage']) >= index and not (task['stage'] == 'r1' and letter and task['letter'] != letter):
            task['superseded'] = True
    manifest.setdefault('invalidations', []).append({'at': now(), 'from': start, 'reason': reason, 'accepted': removed})
    manifest['vote'] = {'epoch': manifest['vote']['epoch'] + 1,
                        'roster': manifest['vote']['roster'], 'status': 'pending'}
    for key in ('final_plan', 'final_verification', 'findings_provenance', 'repair_noop', 'stage_actors', 'completed_at'):
        manifest.pop(key, None)
    manifest['findings'] = []
    manifest['blockers'] = []
    manifest['readiness'] = 'pending'
    manifest['status'] = 'running'
    manifest['stage'] = start


def intent(root, manifest, args):
    require(manifest['status'] != 'complete', 'completed run is immutable')
    require(args.stage == manifest['stage'], 'launch stage must equal the current stage')
    require(args.letter in participants(manifest, args.stage), 'seat does not participate in this stage')
    validate_roster(manifest)
    if args.stage in ('synthesis', 'redteam', 'repair', 'verify'):
        validate_actors(manifest)
    check_all(root, manifest)
    require(args.attempt >= 1, 'attempt must be positive')
    epoch = manifest['vote']['epoch']
    key = f'council-{manifest["run_id"]}-{args.stage}-{epoch}-{args.letter}-a{args.attempt}'
    relative = args.output or f'_moderator/raw/{args.stage}-e{epoch}-{args.letter}-a{args.attempt}.md'
    safe_path(root, relative, exists=False)
    require(relative.startswith('_moderator/raw/'), 'raw staging must be under _moderator/raw/')
    if key in manifest['tasks']:
        task = manifest['tasks'][key]
        require(task['output_path'] == relative and not task.get('superseded'), 'launch intent conflicts with existing request')
        return {'request_id': key, **task}
    previous = [t for t in manifest['tasks'].values() if t['stage'] == args.stage and t['letter'] == args.letter and t['epoch'] == epoch]
    require(not any(t.get('superseded') and not settled(t) for t in manifest['tasks'].values()
                    if t['letter'] == args.letter), 'reconcile/cancel superseded work before relaunch')
    require(not previous or args.attempt > max(t['attempt'] for t in previous), 'attempt must increase')
    require(all(settled(t) for t in previous), 'reconcile/cancel prior active attempt before relaunch')
    require(args.letter not in manifest['accepted'].get(args.stage, {}), 'accepted attempt is immutable; explicitly reopen the stage first')
    require(not any(t['output_path'] == relative for t in manifest['tasks'].values()), 'raw output path is already reserved')
    for task in previous:
        task['superseded'] = True
    seat = actor_seat(manifest, args.stage, args.letter)
    task = {'task_id': None, 'stage': args.stage, 'letter': args.letter, 'attempt': args.attempt,
            'epoch': epoch, 'status': 'intent', 'output_path': relative,
            'target': {k: seat[k] for k in ('providerInstanceId', 'model', 'options')},
            'model_identity': seat['model_identity'], 'hasPendingChildRuns': False,
            'workState': 'pending', 'dependencies': dependency_snapshot(manifest, args.stage),
            'created_at': now(), 'history': []}
    manifest['tasks'][key] = task
    return {'request_id': key, **task}


def record_task(root, manifest, args):
    require(args.request_id in manifest['tasks'], 'unknown launch intent')
    task = manifest['tasks'][args.request_id]
    require(not task.get('superseded') or not settled(task), 'superseded task callback ignored; retain it outside authoritative state')
    require(not task['task_id'] or task['task_id'] == args.task_id, 'task ID cannot change for a stable launch intent')
    require(not any(t['task_id'] == args.task_id for key, t in manifest['tasks'].items() if key != args.request_id), 'task ID already belongs to another intent')
    snapshot = read_json(args.snapshot) if args.snapshot else {}
    require(type(snapshot) is dict, 'task snapshot must be an object')
    state = {'status': args.status, 'hasPendingChildRuns': args.pending_children,
             'workState': args.work_state}
    if settled(task):
        require(all(task[k] == v for k, v in state.items()), 'terminal task state cannot change')
    task['task_id'] = args.task_id
    task.update(state)
    if successful(task):
        raw = safe_path(root, task['output_path'])
        digest = sha256(raw)
        require(not task.get('raw_sha256') or task['raw_sha256'] == digest, 'immutable raw output changed')
        task['raw_sha256'] = digest
    event = {**state, 'snapshot': snapshot}
    if not task['history'] or {k: v for k, v in task['history'][-1].items() if k != 'at'} != event:
        task['history'].append({'at': now(), **event})
    return {'request_id': args.request_id, **task}


def accept(root, manifest, args):
    task = eligible_task(root, manifest, args.request_id)
    stage, letter = task['stage'], task['letter']
    published = safe_path(root, args.published)
    require(not args.published.startswith('_moderator/'), 'published artifacts must be outside private moderator staging')
    require(published != safe_path(root, task['output_path']), 'published output must be separate from raw')
    require(published.read_text(encoding='utf-8').strip(), 'published artifact must contain nonempty UTF-8 text')
    if stage == 'r3':
        validate_ballot(last_json(published), letter, manifest['vote']['roster'], [d['name'] for d in manifest['rubric']])
    if stage == 'verify':
        pointer = manifest.get('final_plan', {})
        plan = accepted_paths(root, manifest, pointer.get('stage')).get(pointer.get('letter'))
        data = last_json(published)
        require(plan is not None and data.get('final_sha256') == sha256(plan), 'critic must verify the exact final hash')
        findings = data.get('findings')
        known = {item['id'] for item in manifest['findings']}
        require(type(findings) is dict and set(findings) == known, 'verification must address every finding')
        for item in findings.values():
            require(type(item) is dict and type(item.get('verified')) is bool
                    and item.get('disposition') in ('resolved', 'rejected', 'unresolved'),
                    'verification finding needs a disposition and verified boolean')
        # Reject repairable format errors before accepting immutable output.
        evaluate_final_objections({letter: data})
    artifact = {**artifact_ref(root, args.published), 'request_id': args.request_id, 'epoch': task['epoch']}
    old = manifest['accepted'].setdefault(stage, {}).get(letter)
    require(old is None or old == artifact, 'accepted artifact is immutable; reopen before replacement')
    if old == artifact:
        return artifact
    require(stage == manifest['stage'], 'acceptance requires the current stage')
    require(letter in participants(manifest, stage), 'seat no longer participates')
    manifest['accepted'][stage][letter] = artifact
    return artifact


def tally_record(root, manifest, args):
    require(manifest['stage'] == 'r3', 'tally registration requires r3 stage')
    validate_roster(manifest)
    check_all(root, manifest)
    document = read_json(safe_path(root, args.artifact))
    # Recompute independently: the supplied tally cannot assert an invented winner.
    from tally import tally
    expected = tally(root)
    require(document == expected, 'tally differs from independently computed accepted ballots')
    require(document.get('epoch') == manifest['vote']['epoch'], 'stale tally epoch')
    require(document.get('roster') == manifest['vote']['roster'], 'tally roster mismatch')
    require(document.get('provenance') == manifest['accepted'].get('r3', {}), 'tally ballot provenance mismatch')
    require(document.get('proposal_provenance') == manifest['accepted'].get('r2', {}), 'tally proposal provenance mismatch')
    status = document.get('status')
    require(status in {'complete', 'tied', 'incomplete', 'invalid'}, 'invalid tally status')
    reference = {**artifact_ref(root, args.artifact), 'epoch': manifest['vote']['epoch']}
    if manifest['vote']['status'] in {'complete', 'tied'}:
        require(manifest['vote'].get('tally') == reference, 'registered valid tally is immutable; reopen to change it')
        return manifest['vote']
    manifest['vote']['status'] = status
    manifest['vote']['tally'] = reference
    for key in ('winner', 'tied', 'author', 'author_draw'):
        manifest['vote'].pop(key, None)
    if status == 'complete':
        require(document.get('winner') in manifest['vote']['roster'], 'complete election needs a known winner')
        manifest['vote']['winner'] = document['winner']
        manifest['vote']['author'] = document['winner']
    elif status == 'tied':
        tied = document.get('tied')
        require(type(tied) is list and len(tied) >= 2 and set(tied) <= set(manifest['vote']['roster']), 'tie requires tied candidates')
        manifest['vote']['tied'] = tied
    else:
        require(document.get('winner') is None, 'incomplete/invalid election cannot have a winner')
    return manifest['vote']


def verified_tally(root, manifest):
    vote = manifest['vote']
    require(vote['status'] in {'complete', 'tied'}, 'complete or tied valid election required')
    tally = read_json(check_ref(root, vote.get('tally')))
    require(vote['tally'].get('epoch') == vote['epoch'], 'stale registered tally')
    from tally import tally as recompute
    require(tally == recompute(root), 'registered tally no longer matches accepted ballots')
    return tally


def evidence(root, manifest, refs):
    require(type(refs) is list and refs, 'evidence requires at least one frozen/accepted artifact reference')
    known = {(x['path'], x['sha256']) for x in manifest['sources']}
    known.update((x['path'], x['sha256']) for group in manifest['accepted'].values() for x in group.values())
    for ref in refs:
        check_ref(root, ref)
        require((ref['path'], ref['sha256']) in known, 'evidence must reference a frozen source or accepted output')


def record_findings(root, manifest, args):
    require(manifest['stage'] == 'redteam', 'findings registration requires redteam stage')
    barrier(root, manifest, 'redteam')
    findings = read_json(safe_path(root, args.artifact))
    require(type(findings) is list, 'findings must be an array (empty records no findings)')
    ids = set()
    for finding in findings:
        require(type(finding) is dict and type(finding.get('id')) is str and finding['id'] and finding['id'] not in ids, 'finding IDs must be unique nonempty strings')
        ids.add(finding['id'])
        require(type(finding.get('description')) is str and finding['description'].strip(), 'finding description required')
        require(type(finding.get('hard_constraint')) is bool, 'finding needs hard_constraint boolean')
        finding_blocks(finding)
        evidence(root, manifest, finding.get('evidence'))
    require('findings_provenance' not in manifest or findings == manifest['findings'], 'registered findings are immutable; reopen redteam to amend')
    manifest['findings'] = findings
    manifest['findings_provenance'] = {'artifact': artifact_ref(root, args.artifact), 'redteam': manifest['accepted']['redteam']}
    return {'findings': len(findings)}


def record_final(root, manifest, args):
    require(manifest['stage'] == 'verify', 'final verification registration requires verify stage')
    check_all(root, manifest)
    barrier(root, manifest, 'verify')
    require('findings_provenance' in manifest, 'registered findings required')
    provenance = manifest['findings_provenance']
    require(read_json(check_ref(root, provenance['artifact'])) == manifest['findings'], 'finding ledger changed')
    require(provenance['redteam'] == manifest['accepted'].get('redteam'), 'finding redteam provenance changed')
    pointer = manifest.get('final_plan')
    require(type(pointer) is dict and pointer.get('stage') in ('synthesis', 'repair'), 'final plan pointer required')
    plan = accepted_paths(root, manifest, pointer['stage']).get(pointer.get('letter'))
    require(plan is not None, 'final plan is not accepted')
    document = read_json(safe_path(root, args.artifact))
    require(type(document) is dict and document.get('final_sha256') == sha256(plan), 'final verification must identify the exact final hash')
    sections = document.get('sections')
    require(type(sections) is dict and set(FINAL_SECTIONS) <= set(sections), f'final section checks required: {FINAL_SECTIONS}')
    require(all(type(sections[key]) is str and sections[key].strip() for key in FINAL_SECTIONS), 'section checks require concrete locations/evidence, not booleans')
    require(type(document.get('user_decisions')) is list, 'user_decisions must be a list')
    if manifest['vote']['status'] == 'tied':
        require(document['user_decisions'] and document.get('tied_alternatives') == manifest['vote']['tied'], 'tied final must preserve alternatives and a user decision')
    dispositions = document.get('findings')
    require(type(dispositions) is list, 'final finding dispositions must be an array')
    known = {item['id']: item for item in manifest['findings']}
    require(all(type(item) is dict and type(item.get('id')) is str for item in dispositions), 'invalid disposition record')
    require(len(dispositions) == len(known) and {d['id'] for d in dispositions} == set(known), 'every finding needs exactly one final disposition')
    verification = {}
    verifier_documents = {}
    for letter, path in accepted_paths(root, manifest, 'verify').items():
        data = last_json(path)
        require(data.get('final_sha256') == sha256(plan), f'{letter}: critic verified a different final version')
        require(type(data.get('findings')) is dict and set(data['findings']) == set(known), f'{letter}: verification must address every finding')
        verification[letter] = data['findings']
        verifier_documents[letter] = data
    for disposition in dispositions:
        identity = disposition['id']
        require(disposition.get('disposition') in {'resolved', 'rejected', 'unresolved'}, f'{identity}: invalid disposition')
        evidence(root, manifest, disposition.get('evidence'))
        for letter, findings in verification.items():
            item = findings[identity]
            require(type(item) is dict and type(item.get('verified')) is bool, f'{letter}/{identity}: verified boolean required')
            if disposition['disposition'] != 'unresolved':
                require(item.get('disposition') == disposition['disposition'], f'{letter}/{identity}: disposition disagreement remains unresolved')
                require(item['verified'], f'{letter}/{identity}: must-fix disposition is unverified')
    objections = evaluate_final_objections(verifier_documents, document.get('verifier_objections'))
    for objection in objections:
        if objection['evidence']:
            evidence(root, manifest, objection['evidence'])
    manifest['final_verification'] = artifact_ref(root, args.artifact)
    return document


def advance(root, manifest):
    stage = manifest['stage']
    require(stage != 'complete', 'run already complete')
    check_all(root, manifest)
    validate_roster(manifest)
    if stage in ('r1', 'r2', 'synthesis', 'redteam'):
        barrier(root, manifest, stage)
    if stage == 'r3':
        verified_tally(root, manifest)
        require(manifest['vote'].get('author'), 'tied election needs a recorded seeded author draw')
    if stage == 'redteam':
        require('findings_provenance' in manifest, 'register redteam findings, including an empty finding list')
        check_ref(root, manifest['findings_provenance']['artifact'])
    if stage == 'repair':
        if not manifest.get('repair_noop'):
            barrier(root, manifest, 'repair')
            manifest['final_plan'] = {'stage': 'repair', 'letter': manifest['vote']['author']}
        require(manifest.get('final_plan'), 'accepted final plan required')
    require(stage != 'verify', 'use finalize after verified finding dispositions')
    manifest['stage'] = STAGES[STAGES.index(stage) + 1]
    manifest['status'] = 'running'
    manifest['readiness'] = 'pending'
    return {'stage': manifest['stage']}


def finalize(root, manifest):
    require(manifest['stage'] == 'verify', 'finalize requires verify stage')
    verified_tally(root, manifest)
    require('final_verification' in manifest, 'register final artifact and verified finding dispositions first')
    args = argparse.Namespace(artifact=manifest['final_verification']['path'])
    check_ref(root, manifest['final_verification'])
    document = record_final(root, manifest, args)
    known = {item['id']: item for item in manifest['findings']}
    manifest['blockers'] = [
        {'finding_id': item['id'], 'hard_constraint': known[item['id']]['hard_constraint'],
         'severity': known[item['id']].get('severity', 'must'), 'reason': 'unresolved verified finding'}
        for item in document['findings']
        if finding_blocks({**known[item['id']], 'disposition': item['disposition']})]
    objections = evaluate_final_objections(
        {letter: last_json(path) for letter, path in accepted_paths(root, manifest, 'verify').items()},
        document.get('verifier_objections'))
    manifest['blockers'].extend({'finding_id': item['id'], 'critic': item['critic'],
                                 'hard_constraint': item['hard_constraint'], 'severity': item['severity'],
                                 'reason': item['description']}
                                for item in objections if item['blocking'])
    manifest['readiness'] = ('blocked' if manifest['blockers'] else 'needs-user-decision'
                             if document['user_decisions'] or manifest['vote']['status'] == 'tied' else 'ready')
    manifest['stage'] = 'complete'
    manifest['status'] = 'complete'
    manifest['completed_at'] = now()
    return {'status': 'complete', 'readiness': manifest['readiness'], 'blockers': manifest['blockers']}


def reconcile(root, manifest):
    errors = []
    try:
        validate_sources(root, manifest)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    for stage in manifest['accepted']:
        try:
            accepted_paths(root, manifest, stage)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    for ref in (manifest['vote'].get('tally'), manifest.get('final_verification'),
                manifest.get('findings_provenance', {}).get('artifact')):
        if ref is not None:
            try:
                check_ref(root, ref)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
    return {'run_id': manifest['run_id'], 'owner_thread_id': manifest['owner_thread_id'],
            'stage': manifest['stage'], 'status': manifest['status'],
            'readiness': manifest.get('readiness', 'pending'), 'integrity_errors': errors,
            'accepted': manifest['accepted'], 'vote': manifest['vote'],
            'reconcile_tasks': [{'request_id': key, 'task_id': t['task_id'], 'status': t['status'],
                                 'hasPendingChildRuns': t['hasPendingChildRuns'], 'workState': t['workState'],
                                 'action': 'reconcile-by-task-id' if t['task_id'] else 'retry-same-request-id',
                                 'target': t['target'], 'output_path': t['output_path']}
                                for key, t in manifest['tasks'].items()
                                if not settled(t) or (not t.get('superseded') and successful(t) and
                                    key not in {a['request_id'] for group in manifest['accepted'].values() for a in group.values()})]}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    command = commands.add_parser('init')
    command.add_argument('parent')
    command.add_argument('--owner-thread', required=True)
    command.add_argument('--topic', required=True)
    command.add_argument('--seats', required=True)
    command.add_argument('--rubric', required=True)
    command.add_argument('--sources')
    for name in ('intent', 'record-task', 'accept', 'record-tally', 'draw-author', 'freeze-sources',
                 'set-roster', 'replace-seat', 'replace-actor', 'reopen', 'record-findings', 'no-repair',
                 'record-final', 'advance', 'finalize', 'mark-incomplete', 'resume', 'status'):
        command = commands.add_parser(name)
        command.add_argument('root')
        command.add_argument('--owner-thread', required=name != 'status')
        if name in ('accept', 'record-task'):
            command.add_argument('request_id')
        if name in ('record-tally', 'record-findings', 'record-final', 'freeze-sources'):
            command.add_argument('--artifact', required=True)
        if name == 'intent':
            command.add_argument('--stage', required=True, choices=TASK_STAGES)
            command.add_argument('--letter', required=True)
            command.add_argument('--attempt', type=int, default=1)
            command.add_argument('--output')
        if name == 'record-task':
            command.add_argument('--task-id', required=True)
            command.add_argument('--status', required=True, choices=('queued', 'running', *sorted(TERMINAL)))
            command.add_argument('--work-state', required=True)
            command.add_argument('--pending-children', action='store_true')
            command.add_argument('--snapshot')
        if name == 'accept':
            command.add_argument('--published', required=True)
        if name == 'draw-author':
            command.add_argument('--seed', required=True)
        if name in ('reopen', 'set-roster', 'replace-seat', 'replace-actor', 'mark-incomplete'):
            command.add_argument('--reason', required=True)
        if name == 'reopen':
            command.add_argument('--stage', required=True, choices=TASK_STAGES)
        if name == 'set-roster':
            command.add_argument('--roster', nargs='+', required=True)
        if name == 'replace-actor':
            command.add_argument('--stage', required=True, choices=('synthesis', 'redteam', 'repair', 'verify'))
        if name in ('replace-seat', 'replace-actor'):
            command.add_argument('--letter', required=True)
            command.add_argument('--seat', required=True)
    return p


def dispatch(root, manifest, args):
    command = args.command
    if command == 'intent':
        return intent(root, manifest, args)
    if command == 'record-task':
        return record_task(root, manifest, args)
    if command == 'accept':
        return accept(root, manifest, args)
    if command == 'record-tally':
        return tally_record(root, manifest, args)
    if command == 'advance':
        return advance(root, manifest)
    if command == 'finalize':
        return finalize(root, manifest)
    if command == 'record-findings':
        return record_findings(root, manifest, args)
    if command == 'record-final':
        return record_final(root, manifest, args)
    if command == 'freeze-sources':
        require(manifest['stage'] == 'prepare' and not manifest['tasks'], 'sources freeze only before R1')
        sources = read_json(safe_path(root, args.artifact))
        require(type(sources) is list, 'sources must be an array of path/sha256 records')
        manifest['sources'] = sources
        validate_sources(root, manifest)
        return {'sources': sources}
    if command == 'draw-author':
        verified_tally(root, manifest)
        require(manifest['vote']['status'] == 'tied', 'author draw is only for tied elections')
        require(manifest['stage'] == 'r3', 'author selection is frozen after r3')
        draw = manifest['vote'].get('author_draw')
        require(draw is None or draw['seed'] == args.seed, 'author draw seed is immutable')
        author = random.Random(args.seed).choice(sorted(manifest['vote']['tied']))
        manifest['vote']['author'] = author
        manifest['vote']['author_draw'] = {'seed': args.seed, 'algorithm': 'python-random-choice-sorted-v1', 'author': author, 'purpose': 'authorship only; election remains tied'}
        return manifest['vote']['author_draw']
    if command == 'no-repair':
        require(manifest['stage'] == 'repair' and not manifest['findings'], 'no-op repair requires no findings')
        barrier(root, manifest, 'synthesis')
        manifest['repair_noop'] = {'reason': 'no findings', 'at': now()}
        manifest['final_plan'] = {'stage': 'synthesis', 'letter': manifest['vote']['author']}
        return manifest['repair_noop']
    if command == 'reopen':
        require(STAGES.index(args.stage) <= STAGES.index(manifest['stage']), 'cannot reopen a future stage')
        # Any reopened output changes later-round dependencies and therefore voting.
        start = 'r1' if args.stage == 'r1' else 'r2'
        invalidate(manifest, start, args.reason)
        return {'stage': manifest['stage'], 'epoch': manifest['vote']['epoch']}
    if command == 'replace-actor':
        require(manifest['stage'] == args.stage, 'replace an actor only in the current post-vote stage')
        verified_tally(root, manifest)
        require(args.letter in participants(manifest, args.stage), 'actor does not participate in this stage')
        seat = read_json(args.seat)
        require(type(seat) is dict and seat.get('letter') == args.letter and seat.get('active') is True, 'actor letter/active mismatch')
        require(type(seat.get('options')) is dict and all(type(seat.get(k)) is str and seat[k] for k in ('providerInstanceId', 'model')), 'actor requires an exact target')
        model_identity(seat)
        stages = [args.stage]
        if args.stage == 'redteam':
            stages.append('verify')
        elif args.stage == 'synthesis':
            stages.append('repair')
        old = actor_seat(manifest, args.stage, args.letter)
        require(all(settled(task) for task in manifest['tasks'].values()
                    if task['stage'] in stages and task['letter'] == args.letter),
                'reconcile/cancel actor tasks before replacement')
        removed = {}
        for stage in stages:
            for task in manifest['tasks'].values():
                if task['stage'] == stage and task['letter'] == args.letter:
                    task['superseded'] = True
            artifact = manifest['accepted'].get(stage, {}).pop(args.letter, None)
            if artifact:
                removed[stage] = artifact
            manifest.setdefault('stage_actors', {}).setdefault(stage, {})[args.letter] = seat
        validate_actors(manifest)
        manifest['substitutions'].append({'at': now(), 'role': 'post-vote actor', 'stages': stages,
                                          'letter': args.letter, 'from': old, 'to': seat,
                                          'reason': args.reason, 'accepted': removed})
        if args.stage == 'redteam':
            manifest.pop('findings_provenance', None)
            manifest['findings'] = []
        manifest.pop('final_verification', None)
        return {'stage': args.stage, 'letter': args.letter, 'epoch': manifest['vote']['epoch'], 'vote_unchanged': True}
    if command in ('set-roster', 'replace-seat'):
        if command == 'replace-seat':
            seat = read_json(args.seat)
            require(type(seat) is dict and seat.get('letter') == args.letter, 'replacement letter mismatch')
            old = next((s for s in manifest['seats'] if s['letter'] == args.letter), None)
            require(old is not None, 'unknown replacement seat')
            require(seat.get('active') is True, 'replacement must be active')
            identity = model_identity(seat)
            require(all(identity != model_identity(s) for s in manifest['seats'] if s['active'] and s['letter'] != args.letter), 'replacement model already occupies an active seat')
            manifest['seats'][manifest['seats'].index(old)] = seat
            manifest['substitutions'].append({'at': now(), 'letter': args.letter, 'from': old, 'to': seat, 'reason': args.reason})
            invalidate(manifest, 'r1', args.reason, args.letter)
            # Validate the full replacement shape before persisting it.
            require(all(k in seat for k in ('providerInstanceId', 'model', 'options', 'model_identity')), 'replacement lacks target/identity fields')
            require(type(seat['options']) is dict and all(type(seat[k]) is str and seat[k] for k in ('providerInstanceId', 'model')), 'invalid replacement target')
        else:
            old = manifest['vote']['roster']
            require(set(args.roster) <= {s['letter'] for s in manifest['seats']}, 'unknown roster member')
            require(len(set(args.roster)) == len(args.roster), 'duplicate roster member')
            manifest.setdefault('roster_history', []).append({'at': now(), 'from': old, 'to': args.roster, 'reason': args.reason, 'r1': manifest['accepted'].get('r1', {}).copy()})
            manifest['vote']['roster'] = args.roster
            for seat in manifest['seats']:
                seat['active'] = seat['letter'] in args.roster
            for letter in set(manifest['accepted'].get('r1', {})) - set(args.roster):
                manifest['accepted']['r1'].pop(letter)
            invalidate(manifest, 'r2', args.reason)
            manifest['stage'] = 'r1' if set(manifest['accepted'].get('r1', {})) != set(args.roster) else 'r2'
        try:
            validate_roster(manifest)
        except ValueError:
            manifest['status'] = 'incomplete'
            manifest['readiness'] = 'incomplete'
        return {'stage': manifest['stage'], 'status': manifest['status'], 'epoch': manifest['vote']['epoch']}
    if command == 'mark-incomplete':
        require(manifest['status'] != 'complete', 'completed run cannot become incomplete')
        manifest['status'] = 'incomplete'
        manifest['readiness'] = 'incomplete'
        manifest.setdefault('interruptions', []).append({'at': now(), 'reason': args.reason})
        return {'status': 'incomplete'}
    if command == 'resume':
        return reconcile(root, manifest)
    raise ValueError(f'unknown command {command}')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'init':
            result = init(args)
        elif args.command == 'status':
            result = reconcile(args.root, load_manifest(args.root))
        else:
            with writer(args.root, args.owner_thread) as manifest:
                result = dispatch(args.root, manifest, args)
        import json
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError, UnicodeError) as exc:
        print(f'council run: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

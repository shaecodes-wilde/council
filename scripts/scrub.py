#!/usr/bin/env python3
"""Create immutable publishable copies; never edit raw Council responses.

Usage: python3 -I scripts/scrub.py RUN STAGE [--request-id KEY ...]
Explicit request IDs select completed current tasks recorded in the manifest.
Without IDs, only tasks referenced by accepted stage artifacts are selected.
Review the JSON report's residual mentions before run.py accept; quoted/code/JSON
content and ordinary source attribution are preserved, even if they name a model.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from contracts import accepted_paths, eligible_task, load_manifest, safe_path, sha256

NAMES = (r"Claude(?:[ -](?:Opus|Sonnet|Haiku))?(?:[ -]\d+(?:\.\d+)*)?|"
         r"GPT(?:-[\w]+(?:[.-][\w]+)*)?|SWE-\d+(?:[.-][\w]+)*|"
         r"Anthropic|Opus|Sonnet|Fable|Haiku|OpenAI|ChatGPT|Codex|Sol|"
         r"DeepSeek|OpenCode|Devin|Cognition|Windsurf|Gemini|Google|Grok|xAI|"
         r"Kimi|Moonshot|Qwen|Llama|Mistral|GLM(?:[- ]\d+(?:\.\d+)*)?|Zhipu|ZAI")
# Require first-person grammar; never match generic 'developed by Google' or
# 'As OpenAI recommends'. Leave uncertain disclosures for moderator review.
SELF_RE = re.compile(
    rf"\b(?:I am|I'm|I’m)\s+(?:an?\s+)?(?P<identity>{NAMES})\b"
    rf"(?=\s*(?:[,.;:!?]|$)|\s+(?:model|assistant|and|but)\b)"
    rf"|\bAs\s+(?:an?\s+)?(?P<as_identity>{NAMES})\b"
    rf"(?:\s+(?:model|assistant))?(?=,\s+(?:I\b|my\b))"
    rf"|\bI\s+(?:was|am)\s+(?:an?\s+(?:AI\s+)?(?:model|assistant)\s+)?"
    rf"(?:built|made|trained|developed)\s+by\s+(?P<provider>{NAMES})\b"
    rf"(?=\s*(?:[,.;:!?]|$)|\s+(?:and|but)\b)", re.I)
GREETING_RE = re.compile(rf"^(?P<indent>[ \t]*)(?P<identity>{NAMES})[ \t]+here(?=[,.!:]|[ \t]*$)", re.I | re.M)
QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"|“[^”]*”|‘(?:[^’]|’(?=\w))*’|(?<!\w)\'(?:[^\'\\]|\\.|\'(?=\w))*\'(?!\w)', re.S)
HTML_EVIDENCE_RE = re.compile(r"<(blockquote|pre|code|script|style)\b[^>]*>.*?(?:</\1\s*>|$)", re.I | re.S)
INLINE_RE = re.compile(r"(`+).*?\1|\[[^\]\n]*\]\([^\n)]*\)", re.S)


def protected_spans(text):
    """Conservatively preserve Markdown evidence and machine-readable payloads."""
    spans = []
    offset, fence, start = 0, None, None
    in_quote = False
    for line in text.splitlines(keepends=True):
        mark = re.match(r" {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            if mark and mark[1][0] == fence[0] and len(mark[1]) >= len(fence):
                spans.append((start, offset + len(line)))
                fence, start = None, None
        elif mark:
            fence, start = mark[1], offset
        elif re.match(r" {0,3}>", line) or (in_quote and line.strip()):
            in_quote = True
            spans.append((offset, offset + len(line)))
        elif re.match(r"(?: {4}|\t)", line):
            spans.append((offset, offset + len(line)))
        if not line.strip():
            in_quote = False
        offset += len(line)
    if fence is not None:
        spans.append((start, len(text)))
    spans.extend(m.span() for pattern in (QUOTED_RE, INLINE_RE, HTML_EVIDENCE_RE) for m in pattern.finditer(text))
    # A raw JSON response is data, even if malformed; do not repair it by editing
    # strings or keys. The content validator, not anonymization, owns its meaning.
    if text.lstrip().startswith(('{', '[')):
        spans.append((0, len(text)))
    return spans


def scrub_text(text, identity_names=()):
    """Return rewritten text plus review findings; uncertain mentions stay intact."""
    literals = sorted({name for name in identity_names if name and name.casefold() not in {'default', 'unknown'}},
                      key=len, reverse=True)
    names = '|'.join([*(re.escape(name) for name in literals), NAMES])
    self_re = re.compile(SELF_RE.pattern.replace(NAMES, names), SELF_RE.flags)
    greeting_re = re.compile(GREETING_RE.pattern.replace(NAMES, names), GREETING_RE.flags)
    mention_re = re.compile(rf"\b(?:{names})\b", re.I)
    protected = protected_spans(text)
    replacements = []
    for pattern in (self_re, greeting_re):
        for match in pattern.finditer(text):
            prefix = text[:match.start()].rsplit("\n", 1)[-1]
            if pattern is self_re and prefix.strip() and not re.search(r"[.!?]\s+$", prefix):
                continue
            if any(match.start() < end and match.end() > start for start, end in protected):
                continue
            group = next(name for name in ('identity', 'as_identity', 'provider')
                         if match.groupdict().get(name))
            start, end = match.span(group)
            if any(start < old_end and end > old_start for old_start, old_end, _ in replacements):
                continue
            replacements.append((start, end, '[provider withheld]' if group == 'provider' else '[panelist]'))
    redactions = [{"line": text.count('\n', 0, start) + 1,
                   "original": text[start:end], "replacement": replacement}
                  for start, end, replacement in sorted(replacements)]
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    protected = protected_spans(text)
    mentions = [{"line": text.count('\n', 0, match.start()) + 1,
                 "text": match.group(),
                 "protected": any(match.start() < end and match.end() > start
                                  for start, end in protected)}
                for match in mention_re.finditer(text)]
    return text, {"redactions": redactions, "mentions": mentions,
                  "review_required": bool(mentions)}


def _write_immutable(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            name = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise ValueError(f'refusing to replace immutable scrub output: {path}')
    finally:
        if name is not None:
            os.unlink(name)


def scrub(root, stage, request_ids=None):
    root = pathlib.Path(root).resolve()
    manifest = load_manifest(root)
    stage = f'r{stage}' if stage in ('1', '2', '3') else stage
    if stage not in ('r1', 'r2', 'r3', 'synthesis', 'redteam', 'repair', 'verify'):
        raise ValueError('stage must be r1/r2/r3/synthesis/redteam/repair/verify')
    if not request_ids:
        accepted_paths(root, manifest, stage)
        request_ids = [a['request_id'] for a in manifest['accepted'].get(stage, {}).values()]
    if not request_ids or len(set(request_ids)) != len(request_ids):
        raise ValueError('select unique successful manifest tasks with --request-id KEY, or accept stage artifacts first')
    # Validate the whole selection before publishing any file.
    tasks = [(request, eligible_task(root, manifest, request)) for request in request_ids]
    letters = []
    for request, task in tasks:
        if task['stage'] != stage:
            raise ValueError(f'{request}: task stage does not match {stage}')
        letters.append(task['letter'])
    if len(set(letters)) != len(letters):
        raise ValueError('select only one successful attempt per seat')
    identity_names = set()
    for seat in manifest['seats']:
        for key in ('model', 'model_identity'):
            identity = seat.get(key)
            if isinstance(identity, str) and identity:
                identity_names.update((identity, identity.rsplit('/', 1)[-1]))
    reports = []
    for request, task in tasks:
        raw = safe_path(root, task['output_path'])
        original = raw.read_bytes()
        if hashlib.sha256(original).hexdigest() != task['raw_sha256']:
            raise ValueError(f'{request}: immutable raw output changed')
        published, findings = scrub_text(original.decode('utf-8'), identity_names)
        payload = published.encode('utf-8')
        digest = hashlib.sha256(payload).hexdigest()
        token = hashlib.sha256(request.encode('utf-8')).hexdigest()[:16]
        relative = f'{stage}/published/{task["letter"]}-{token}-{digest[:16]}.md'
        path = safe_path(root, relative, exists=False)
        if path == raw:
            raise ValueError('published and raw paths must differ')
        # A maliciously chosen raw path must never be rewritten as another
        # response's publication, even if it is not this task's raw path.
        if any(path == safe_path(root, t['output_path'], exists=False)
               for t in manifest['tasks'].values()):
            raise ValueError('publication path collides with a raw attempt')
        _write_immutable(path, payload)
        report = {"request_id": request, "stage": stage, "letter": task['letter'],
                  "epoch": task['epoch'], "raw_path": task['output_path'],
                  "raw_sha256": task['raw_sha256'], "path": relative,
                  "sha256": digest, **findings}
        report_relative = f'_moderator/scrub/{stage}-{task["letter"]}-{token}-{digest[:16]}.json'
        report_path = safe_path(root, report_relative, exists=False)
        _write_immutable(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        reports.append({**report, "report_path": report_relative})
        if sha256(raw) != task['raw_sha256']:
            raise ValueError(f'{request}: immutable raw output changed during scrubbing')
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=pathlib.Path)
    parser.add_argument('stage')
    parser.add_argument('--request-id', action='append')
    args = parser.parse_args(argv)
    try:
        reports = scrub(args.run, args.stage, args.request_id)
    except (ValueError, OSError, UnicodeError) as exc:
        print(f'scrub: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({"artifacts": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

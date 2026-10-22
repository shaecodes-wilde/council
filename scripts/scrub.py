#!/usr/bin/env python3
"""Redact panelist self-identification from one round's answers, in place.

usage: scrub.py <debate-dir> <round>

Only self-references are rewritten ("As Claude, ...", "I'm GPT", "Devin here"),
so a topic that is *about* these models keeps its content. Remaining
model/vendor mentions are listed for the moderator to eyeball, not changed.
"""
import pathlib
import re
import sys

NAMES = (r"Claude|Anthropic|Opus|Sonnet|Fable|Haiku|GPT[-\w.]*|OpenAI|ChatGPT|Codex|"
         r"Sol|DeepSeek|OpenCode|Devin|Cognition|SWE-\d[\w.-]*|Windsurf|Gemini|Google|"
         r"Grok|xAI|Kimi|Moonshot|Qwen|Llama|Mistral")
SELF = [
    rf"\b(?:as|being)\s+(?:an?\s+)?(?:{NAMES})(?:\s+[\w.-]+)?(?:\s+model)?\b",
    rf"\b(?:I am|I'm|I’m)\s+(?:an?\s+)?(?:{NAMES})(?:\s+[\w.-]+)?\b",
    rf"\b(?:{NAMES})(?:\s+[\w.-]+)?\s+here\b",
    rf"\b(?:my|this)\s+(?:model|vendor|provider)\s+\((?:{NAMES})[^)]*\)",
    rf"\b(?:built|made|trained|developed)\s+by\s+(?:{NAMES})\b",
]
SELF_RE = re.compile("|".join(SELF), re.IGNORECASE)
MENTION_RE = re.compile(rf"\b(?:{NAMES})\b")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    rdir = pathlib.Path(sys.argv[1]) / f"r{sys.argv[2]}"
    files = sorted(rdir.glob("*.md"))
    if not files:
        print(f"no answers in {rdir}")
        return 1
    for f in files:
        text = f.read_text(encoding="utf-8")
        new, n = SELF_RE.subn("[as this panelist]", text)
        if n:
            f.write_text(new, encoding="utf-8")
        mentions = sorted({m.group(0) for m in MENTION_RE.finditer(new)})
        note = f"; other mentions to check: {', '.join(mentions)}" if mentions else ""
        print(f"{f.name}: {n} self-reference(s) redacted{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

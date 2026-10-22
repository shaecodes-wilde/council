# data.json for build_visual.py

Write `<debate-dir>/data.json` and run:

```
python3 -I <skill>/scripts/build_visual.py <debate-dir>/data.json <debate-dir>/breakdown.html
```

Every section is optional and is skipped when its key is missing. Letters are the
anonymous panel letters, and `seats` maps them to real names for display.
Write all text in plain language for the user: short labels, no internal jargon.

```json
{
  "title": "Council: <topic>",
  "folder": "/abs/path/debates/2026-10-22-<slug>",
  "stats": "4 panelists · 3 rounds · 12 documents",

  "seats": [
    {"letter": "A", "name": "Opus 5.5 · High", "short": "Opus", "color": 1},
    {"letter": "B", "name": "GPT-6.1 Sol · High", "short": "Sol", "color": 2},
    {"letter": "C", "name": "DeepSeek V4.1 Flash · High", "short": "DeepSeek", "color": 3},
    {"letter": "D", "name": "Devin SWE-2 · Max", "short": "Devin", "color": 4, "dropped": ["R2"]}
  ],

  "rounds": [
    {"name": "1 · Independent", "note": "4 proposals"},
    {"name": "2 · Debate", "note": "7 disputes, 4 settled"},
    {"name": "3 · Score & vote", "note": "A wins on Borda 8–6", "highlight": true}
  ],

  "proposals_title": "The four proposals: round 1 → after the debate",
  "proposals": [
    {"letter": "A", "headline": "big idea in one line", "r1": "what it proposed first",
     "final": "what it became after the debate", "badge": "Winner"}
  ],

  "score_chart": {
    "title": "Score path",
    "note": "Panel averages.",
    "dimensions": ["Correctness", "Simplicity", "Risk"],
    "series": [
      {"label": "Today", "values": [6, 5, 7], "hollow": true},
      {"label": "Winning plan", "letter": "A", "values": [9, 8.5, 8]}
    ],
    "min": 4, "max": 10
  },

  "consensus": [
    {"text": "what they agree on", "agree": ["A", "B", "C", "D"]}
  ],

  "disputes": [
    {"id": "D1", "question": "short neutral question",
     "positions": {"A": {"label": "Keep X", "changed": false},
                   "B": {"label": "Drop X", "changed": true}},
     "outcome": "3–1 keep X", "status": "settled"}
  ],

  "redteam": {
    "winner": "A",
    "verdicts": [{"letter": "B", "verdict": "ship-after-fixes", "summary": "one line"}],
    "issues": [{"issue": "...", "fix": "...", "raised": {"B": "must", "C": "should"}}]
  },

  "factcheck": [
    {"claim": "short claim", "by": "C", "result": "false", "evidence": "what the source shows"}
  ],

  "user_calls": [
    {"question": "neutral question", "options": ["option 1", "option 2"], "raised_by": ["A", "C"]}
  ],
  "unknowns": [
    {"title": "Question only the client can answer", "text": "why it matters"}
  ]
}
```

Notes:
- `short`: the seat's name in table headers and chips ("Opus", "Sol", "DeepSeek", "Devin"). `name` is the full label.
- `tally`: leave it out. The script loads `r3/tally.json` automatically. It renders the
  scorer × proposal matrix, peer averages, Borda points and first-place counts. If
  `score_chart` is missing, it also draws a per-dimension peer-score chart from the tally.
- `status`:
  - `settled`: at least 3/4, or every remaining seat, on one side.
  - `split`: no majority.
  - `user`: the panel says it's the user's or client's decision.
- `result` is one of `true` / `false` / `partly` / `unverifiable`.
- `verdict` is one of `ship` / `ship-after-fixes` / `block`.
- Colours: seat colour 1-6 maps to T3's `--chart-1..6`. Keep each seat on the same colour
  for the whole page.
- Neutral wording everywhere: no "moderator recommends". The `user_calls` options carry no
  recommendation, and are listed in the order the panel raised them.
- After building, `html_preview` the page, fix anything clipped, then `html_render`
  it at a height of min(contentHeight, 2000).

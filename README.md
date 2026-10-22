# council

An agent skill for Claude Code and Codex. It runs a round-by-round debate between subagents on
different models, then shows a visual breakdown in the chat.

The default panel:

| Seat | Model |
|---|---|
| Opus | Claude Opus 5.5 · High |
| Sol | GPT-6.1 Sol · High |
| DeepSeek | DeepSeek V4.1 Flash · High (via OpenCode) |
| Devin | Devin SWE-2 · Max |

## How it works

1. **Independent:** each panelist writes a proposal without seeing the others.
2. **Debate:** each panelist reads the others' proposals. It argues the numbered disputes and
   revises its own proposal.
3. **Score & vote:** each panelist scores every proposal and ranks the others (it can't rank
   its own). The winner is picked by Borda count.
4. **Red team** (optional, `--redteam`): the winner writes a merged plan and the others attack it.

Panelists are anonymized as A/B/C/D during the debate. The moderator (the agent you invoke it
from) checks verifiable claims between rounds, but stays neutral and never votes or makes
recommendations. The final page shows:

- the proposals;
- the peer scores and ranked vote;
- where the panelists agree;
- the dispute matrix;
- red-team verdicts;
- fact-checks;
- the open questions you have to decide.

## Requirements

- T3 Code with its MCP orchestration tools
  (`delegate_task`, `orchestrator_capabilities`, `html_preview`, `html_render`). Panelists run
  as T3 child tasks.
- Providers set up in T3 for the seats you use: Claude, Codex, OpenCode and Devin (through the
  ACP registry). Seats whose model isn't in the catalog are dropped, and a debate needs at
  least 3 seats.
- Python 3.12+ (standard library only).

## Install

```sh
git clone https://github.com/shaecodes-wilde/council ~/.local/share/agent-skills/council
ln -s ~/.local/share/agent-skills/council ~/.claude/skills/council   # Claude Code
ln -s ~/.local/share/agent-skills/council ~/.agents/skills/council   # Codex
```

## Usage

```
/council <topic or question> [options]
  --mode code|strategy|redesign|artifact
  --score "dim1, dim2, ..."
  --redteam
  --without devin
  --add fable | provider:model@opt=val
  --context <paths...>
```

Each debate is saved to `debates/<date>-<slug>/` in the current project. The folder holds the
briefs, every round's answers, the fact-checks, `tally.json` and `breakdown.html`.

## Files

- `SKILL.md`: the moderator's instructions.
- `references/briefs.md`: brief templates for each round, and the JSON block formats.
- `references/visual.md`: the `data.json` schema for the breakdown page.
- `scripts/scrub.py`: redacts panelists' self-identification between rounds.
- `scripts/tally.py`: validates round-3 ballots and computes peer scores and the Borda count.
- `scripts/build_visual.py`: renders `breakdown.html` using T3 theme variables.

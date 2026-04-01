# archon-eval

Meeting evaluation CLI for Archon agent meetings. Stores structured metrics and CEO quality scores locally in SQLite. The tool is open source — your data lives at `~/.archon/evaluations.db` and never leaves your machine.

## Install

**With Nix (recommended):**
```bash
# Run directly without installing
nix run github:LeviathanST/archon-eval -- trend

# Or enter a dev shell
nix develop
python3 eval.py trend
```

**Without Nix (just needs Python 3.x):**
```bash
git clone <repo>
cd archon-eval
python3 eval.py init
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Create the DB and schema at `~/.archon/evaluations.db` |
| `add <meeting_id>` | Add a meeting record (interactive, or JSON from stdin) |
| `agent <meeting_id> <agent_id>` | Add agent metrics for a meeting |
| `score <meeting_id>` | Add CEO quality scores for a meeting |
| `trend` | Print a trend table across all meetings |
| `show <meeting_id>` | Show all data for a specific meeting |
| `list` | List all meetings with IDs, dates, and scores |

**Custom DB path:**
```bash
python3 eval.py --db /tmp/test.db trend
```

**Scripting (JSON from stdin):**
```bash
echo '{"title":"PR Review","date":"2026-04-01","agents_invited":3}' \
  | python3 eval.py add WStuhggs3sG5
```

## Quality Score Rubric

| Dimension | Max | Criteria |
|-----------|-----|----------|
| Build | 2 | 0=talked past each other. 1=acknowledged. 2=synthesized. |
| Tool purpose | 2 | 0=parroted agenda. 1=relevant. 2=specific, actionable. |
| Disagreement | 1 | 0=echo chamber. 1=pushed back with evidence. |
| New info | 2 | 0=restated known. 1=surfaced connection. 2=changed understanding. |
| Actionable | 2 | 0=vague. 1=clear next steps. 2=assigned with owners. |
| **Total** | **9** | 7+ productive. 4-6 acceptable. <4 waste. |

## Schema

Three tables:

- **`meetings`** — one row per meeting. Static metrics: participation, phases, duration, bugs, tool calls.
- **`agent_metrics`** — one row per agent per meeting. LLM latency, nmem call counts, relevance signals.
- **`quality_scores`** — one row per meeting. CEO scores (0-9) + hard questions (could be async? all agents needed?).

## Data stays local

`~/.archon/evaluations.db` is SQLite on your disk. No network calls, no telemetry. Use `--db` to override the path.

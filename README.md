# archon-eval

Meeting evaluation CLI for Archon agent meetings. Stores structured metrics and quality scores locally in SQLite. The tool is open source — your data lives at `~/.archon/evaluations.db` and never leaves your machine.

## Install

**With Nix (recommended):**
```bash
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

### Core

| Command | Description |
|---------|-------------|
| `init` | Create the DB and schema |
| `add <meeting_id>` | Add a meeting record (interactive, or JSON from stdin) |
| `agent <meeting_id> <agent_id>` | Add agent metrics for a meeting |
| `score <meeting_id>` | Add CEO quality scores for a meeting |
| `trend` | Print trend table with all metrics |
| `show <meeting_id>` | Show all data for a specific meeting |
| `list` | List all meetings |

### Vesper's Metrics

| Command | Description |
|---------|-------------|
| `delta <meeting_id>` | Score contribution deltas per message (interactive) |
| `delta <meeting_id> --auto --log <path>` | Auto-score with Gemini (see below) |
| `yield <meeting_id>` | Compute decision yield (decisions / phases) |
| `utilization <meeting_id>` | Set phase utilization (fraction of agents speaking) |
| `lag` | Record decision outcome tracking across meetings |

### Auto-scoring with Gemini

The `delta --auto` flag uses `gemini` CLI (Gemini 2.5 Flash) to automatically score each message's contribution delta from a meeting log file. Uses cached credentials — no API key needed.

```bash
# Auto-score from a meeting log (gemini CLI must be authenticated)
python3 eval.py delta pr19-review-torque --auto --log /tmp/review-meeting.log
```

The scorer reads speaking turns from the log, builds conversation history incrementally, and asks Gemini to classify each message:
- **0** = echo/restatement of what was already said
- **1** = adds detail or evidence to an existing claim
- **2** = introduces a new claim, constraint, or contradiction

**Requirements:** `gemini` CLI installed and authenticated (`gemini` uses cached browser login credentials).

## Metrics

### Static (per meeting)

| Metric | Source | Type |
|--------|--------|------|
| Participation rate | agents_participated / agents_invited | Mechanical |
| Phases completed | phases_completed / phases_total | Mechanical |
| Duration | Runner analytics | Mechanical |
| Tool calls | Runner analytics | Mechanical |

### Vesper's 4 Metrics

| Metric | What it measures | Type |
|--------|-----------------|------|
| **Decision Yield** | Decisions per phase. Higher = more productive. | Mechanical |
| **Phase Utilization** | Fraction of agents speaking per phase (0-1). Leading indicator. | Mechanical |
| **Contribution Delta** | New information per message (0-2). Catches echo loops. | LLM-scored |
| **Outcome Lag** | How many meetings until a decision is acted on. | Cross-meeting |

### Legacy Quality Score (0-9)

| Dimension | Max | Criteria |
|-----------|-----|----------|
| Build | 2 | 0=talked past. 1=acknowledged. 2=synthesized. |
| Tool purpose | 2 | 0=parroted. 1=relevant. 2=specific, actionable. |
| Disagreement | 1 | 0=echo chamber. 1=pushed back with evidence. |
| New info | 2 | 0=restated known. 1=surfaced. 2=changed understanding. |
| Actionable | 2 | 0=vague. 1=clear steps. 2=assigned owners. |

> Note: The 0-9 rubric is kept for backward compatibility. Vesper's metrics are more objective and comparable. Use Decision Yield as the primary indicator.

## Dashboard

Generate charts + markdown for archon-vision:
```bash
python3 dashboard.py
```

Outputs matplotlib PNGs to `$ARCHON_VISION_DIR/public/charts/` and a dashboard.md.

## Post-meeting pipeline

```bash
./post-meeting.sh <meeting_id> --logs <prefix>
```

Auto-extracts metrics from runner logs, interactive CEO scoring, regenerates dashboard, commits to archon-vision.

## Schema

Five tables:

- **`meetings`** — one row per meeting. Static metrics + Vesper's computed columns (decision_yield, phase_utilization, contribution_delta_avg).
- **`agent_metrics`** — one row per agent per meeting. LLM latency, nmem calls, relevance signals.
- **`quality_scores`** — one row per meeting. Legacy 0-9 CEO scores.
- **`contribution_deltas`** — one row per message per meeting. Delta scores (0-2) with notes.
- **`outcome_lag`** — tracks decisions across meetings. Status: executed, referenced, contradicted, never_referenced.

## Data stays local

`~/.archon/evaluations.db` is SQLite on your disk. No network calls, no telemetry. Use `--db` to override the path.

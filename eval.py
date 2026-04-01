#!/usr/bin/env python3
"""archon-eval — Meeting evaluation CLI backed by SQLite."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_DEFAULT = Path.home() / ".archon" / "evaluations.db"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    methodology          TEXT,
    date                 TEXT NOT NULL,
    agents_invited       INTEGER,
    agents_participated  INTEGER,
    phases_total         INTEGER,
    phases_completed     INTEGER,
    duration_seconds     REAL,
    decisions_made       INTEGER DEFAULT 0,
    action_items         INTEGER DEFAULT 0,
    total_tool_calls     INTEGER DEFAULT 0,
    cross_reference      BOOLEAN DEFAULT 0,
    bugs_found           INTEGER DEFAULT 0,
    notes                TEXT,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_metrics (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id           TEXT NOT NULL REFERENCES meetings(id),
    agent_id             TEXT NOT NULL,
    provider             TEXT,
    messages_sent        INTEGER DEFAULT 0,
    messages_received    INTEGER DEFAULT 0,
    nmem_recall_count    INTEGER DEFAULT 0,
    nmem_recall_ms       REAL    DEFAULT 0,
    nmem_remember_count  INTEGER DEFAULT 0,
    nmem_remember_ms     REAL    DEFAULT 0,
    llm_call_count       INTEGER DEFAULT 0,
    llm_avg_ms           REAL    DEFAULT 0,
    relevance_must_speak INTEGER DEFAULT 0,
    relevance_could_add  INTEGER DEFAULT 0,
    relevance_pass       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quality_scores (
    meeting_id          TEXT PRIMARY KEY REFERENCES meetings(id),
    build_score         INTEGER CHECK(build_score BETWEEN 0 AND 2),
    tool_purpose_score  INTEGER CHECK(tool_purpose_score BETWEEN 0 AND 2),
    disagreement_score  INTEGER CHECK(disagreement_score BETWEEN 0 AND 1),
    new_info_score      INTEGER CHECK(new_info_score BETWEEN 0 AND 2),
    actionable_score    INTEGER CHECK(actionable_score BETWEEN 0 AND 2),
    total_score         INTEGER GENERATED ALWAYS AS (
                            build_score + tool_purpose_score + disagreement_score +
                            new_info_score + actionable_score
                        ) STORED,
    could_be_async      BOOLEAN,
    all_agents_needed   BOOLEAN,
    budget_matched_topic BOOLEAN,
    better_than_previous BOOLEAN,
    notes               TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contribution_deltas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id      TEXT NOT NULL REFERENCES meetings(id),
    agent_id        TEXT NOT NULL,
    message_index   INTEGER NOT NULL,
    delta_score     INTEGER CHECK(delta_score BETWEEN 0 AND 2),
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcome_lag (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_meeting_id     TEXT NOT NULL REFERENCES meetings(id),
    decision_description    TEXT NOT NULL,
    referenced_meeting_id   TEXT REFERENCES meetings(id),
    lag_meetings            INTEGER,
    status                  TEXT CHECK(status IN ('executed','referenced','contradicted','never_referenced')),
    created_at              TEXT DEFAULT (datetime('now'))
);
"""

SCHEMA_MIGRATE = """
-- Add Vesper's metrics columns to meetings (idempotent via try/except in Python)
-- decision_yield: decisions_made / phases_completed
-- phase_utilization: avg fraction of agents who spoke per phase (0.0-1.0)
-- contribution_delta_avg: avg delta score across all messages (0.0-2.0)
"""


def get_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Migrate: add new columns to existing tables (idempotent)
    for col, typ in [
        ("decision_yield", "REAL"),
        ("phase_utilization", "REAL"),
        ("contribution_delta_avg", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def prompt(label: str, default=None, cast=str, optional=False):
    suffix = f" [{default}]" if default is not None else (" (optional)" if optional else "")
    while True:
        raw = input(f"  {label}{suffix}: ").strip()
        if not raw:
            if default is not None:
                return default
            if optional:
                return None
            print("  (required)")
            continue
        if cast is bool:
            return raw.lower() in ("y", "yes", "1", "true")
        try:
            return cast(raw)
        except (ValueError, TypeError):
            print(f"  Expected {cast.__name__}")


def _bool_str(val) -> str:
    if val is None:
        return "-"
    return "Yes" if val else "No"


def _score_str(val) -> str:
    return str(val) if val is not None else "-"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(conn: sqlite3.Connection, args) -> None:
    init_db(conn)
    print(f"DB initialized at {args.db}")


def cmd_add(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id

    # Accept JSON from stdin for scripting
    if not sys.stdin.isatty():
        data = json.load(sys.stdin)
        data["id"] = meeting_id
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        conn.execute(
            f"INSERT OR REPLACE INTO meetings ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
        conn.commit()
        print(f"Meeting {meeting_id} added.")
        return

    print(f"\nAdding meeting {meeting_id}")
    print("(Press Enter to leave optional fields blank)\n")

    title         = prompt("Title")
    methodology   = prompt("Methodology (review/brainstorm/triage/hiring)", optional=True)
    date          = prompt("Date (YYYY-MM-DD)", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    invited       = prompt("Agents invited", cast=int, optional=True)
    participated  = prompt("Agents participated", cast=int, optional=True)
    phases_total  = prompt("Phases total", cast=int, optional=True)
    phases_done   = prompt("Phases completed", cast=int, optional=True)
    duration      = prompt("Duration (seconds)", cast=float, optional=True)
    decisions     = prompt("Decisions made", cast=int, default=0)
    actions       = prompt("Action items", cast=int, default=0)
    tool_calls    = prompt("Total tool calls", cast=int, default=0)
    cross_ref     = prompt("Cross-reference? (y/n)", cast=bool, default=False)
    bugs          = prompt("Bugs found", cast=int, default=0)
    notes         = prompt("Notes", optional=True)

    conn.execute(
        """
        INSERT OR REPLACE INTO meetings
        (id, title, methodology, date, agents_invited, agents_participated,
         phases_total, phases_completed, duration_seconds, decisions_made,
         action_items, total_tool_calls, cross_reference, bugs_found, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (meeting_id, title, methodology, date, invited, participated,
         phases_total, phases_done, duration, decisions,
         actions, tool_calls, cross_ref, bugs, notes),
    )
    conn.commit()
    print(f"\nMeeting {meeting_id} saved.")


def cmd_agent(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id
    agent_id   = args.agent_id

    # Check meeting exists
    row = conn.execute("SELECT id FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        print(f"Meeting {meeting_id} not found. Run `add` first.")
        sys.exit(1)

    if not sys.stdin.isatty():
        data = json.load(sys.stdin)
        data["meeting_id"] = meeting_id
        data["agent_id"]   = agent_id
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        conn.execute(
            f"INSERT INTO agent_metrics ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
        conn.commit()
        print(f"Agent {agent_id} metrics for {meeting_id} saved.")
        return

    print(f"\nAgent metrics for {agent_id} in meeting {meeting_id}\n")

    provider       = prompt("Provider (cli-claude/cli-gemini)", optional=True)
    msg_sent       = prompt("Messages sent", cast=int, default=0)
    msg_recv       = prompt("Messages received", cast=int, default=0)
    recall_count   = prompt("nmem_recall count", cast=int, default=0)
    recall_ms      = prompt("nmem_recall total ms", cast=float, default=0.0)
    remember_count = prompt("nmem_remember count", cast=int, default=0)
    remember_ms    = prompt("nmem_remember total ms", cast=float, default=0.0)
    llm_count      = prompt("LLM call count", cast=int, default=0)
    llm_avg_ms     = prompt("LLM avg ms", cast=float, default=0.0)
    must_speak     = prompt("Relevance: must_speak", cast=int, default=0)
    could_add      = prompt("Relevance: could_add", cast=int, default=0)
    rel_pass       = prompt("Relevance: pass", cast=int, default=0)

    conn.execute(
        """
        INSERT INTO agent_metrics
        (meeting_id, agent_id, provider, messages_sent, messages_received,
         nmem_recall_count, nmem_recall_ms, nmem_remember_count, nmem_remember_ms,
         llm_call_count, llm_avg_ms, relevance_must_speak, relevance_could_add, relevance_pass)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (meeting_id, agent_id, provider, msg_sent, msg_recv,
         recall_count, recall_ms, remember_count, remember_ms,
         llm_count, llm_avg_ms, must_speak, could_add, rel_pass),
    )
    conn.commit()
    print(f"\nAgent {agent_id} metrics saved.")


def cmd_score(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id

    row = conn.execute("SELECT id FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not row:
        print(f"Meeting {meeting_id} not found.")
        sys.exit(1)

    if not sys.stdin.isatty():
        data = json.load(sys.stdin)
        data["meeting_id"] = meeting_id
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        conn.execute(
            f"INSERT OR REPLACE INTO quality_scores ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
        conn.commit()
        print(f"Quality score for {meeting_id} saved.")
        return

    print(f"\nCEO Quality Score for meeting {meeting_id}")
    print("(Build 0-2 | Tool 0-2 | Disagree 0-1 | New info 0-2 | Actionable 0-2)\n")

    build       = prompt("Build score (0-2)", cast=int)
    tool_p      = prompt("Tool purpose score (0-2)", cast=int)
    disagree    = prompt("Disagreement score (0-1)", cast=int)
    new_info    = prompt("New info score (0-2)", cast=int)
    actionable  = prompt("Actionable score (0-2)", cast=int)
    async_q     = prompt("Could this have been async? (y/n)", cast=bool)
    all_needed  = prompt("Were all agents needed? (y/n)", cast=bool)
    budget_ok   = prompt("Did budget match topic? (y/n)", cast=bool)
    better      = prompt("Better than previous? (y/n)", optional=True, cast=bool)
    notes       = prompt("Notes", optional=True)

    conn.execute(
        """
        INSERT OR REPLACE INTO quality_scores
        (meeting_id, build_score, tool_purpose_score, disagreement_score,
         new_info_score, actionable_score, could_be_async, all_agents_needed,
         budget_matched_topic, better_than_previous, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (meeting_id, build, tool_p, disagree, new_info, actionable,
         async_q, all_needed, budget_ok, better, notes),
    )
    conn.commit()
    total = build + tool_p + disagree + new_info + actionable
    verdict = "productive" if total >= 7 else ("acceptable" if total >= 4 else "waste")
    print(f"\nScore saved. Total: {total}/9 ({verdict})")


def cmd_trend(conn: sqlite3.Connection, args) -> None:
    rows = conn.execute(
        """
        SELECT
            m.id,
            m.date,
            m.title,
            m.agents_invited,
            m.agents_participated,
            m.phases_completed,
            m.phases_total,
            m.total_tool_calls,
            m.cross_reference,
            m.decisions_made,
            m.bugs_found,
            m.duration_seconds,
            m.decision_yield,
            m.phase_utilization,
            m.contribution_delta_avg,
            q.total_score
        FROM meetings m
        LEFT JOIN quality_scores q ON q.meeting_id = m.id
        ORDER BY m.date, m.created_at
        """
    ).fetchall()

    if not rows:
        print("No meetings recorded.")
        return

    # Header
    header = (
        f"{'Date':<12} {'ID':<14} {'Title':<28} "
        f"{'Part%':>5} {'Phases':>6} {'Tools':>5} "
        f"{'D.Yld':>5} {'P.Utl':>5} {'Delta':>5} "
        f"{'Score':>5} {'Min':>5}"
    )
    print(header)
    print("-" * len(header))

    for r in rows:
        part_pct = (
            f"{r['agents_participated']/r['agents_invited']*100:.0f}%"
            if r["agents_invited"] and r["agents_participated"] is not None
            else "-"
        )
        phases = (
            f"{r['phases_completed']}/{r['phases_total']}"
            if r["phases_total"] is not None
            else "-"
        )
        score = f"{r['total_score']}/9" if r["total_score"] is not None else "-"
        mins  = f"{r['duration_seconds']/60:.1f}" if r["duration_seconds"] else "-"
        dy    = f"{r['decision_yield']:.1f}" if r["decision_yield"] is not None else "-"
        pu    = f"{r['phase_utilization']:.2f}" if r["phase_utilization"] is not None else "-"
        cd    = f"{r['contribution_delta_avg']:.1f}" if r["contribution_delta_avg"] is not None else "-"
        title = (r["title"] or "")[:27]
        print(
            f"{r['date']:<12} {r['id']:<14} {title:<28} "
            f"{part_pct:>5} {phases:>6} {r['total_tool_calls'] or 0:>5} "
            f"{dy:>5} {pu:>5} {cd:>5} "
            f"{score:>5} {mins:>5}"
        )


def cmd_show(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id

    m = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not m:
        print(f"Meeting {meeting_id} not found.")
        sys.exit(1)

    print(f"\n=== Meeting {meeting_id} ===")
    print(f"Title       : {m['title']}")
    print(f"Date        : {m['date']}")
    print(f"Methodology : {m['methodology'] or '-'}")
    part = (
        f"{m['agents_participated']}/{m['agents_invited']}"
        if m["agents_invited"] else "-"
    )
    print(f"Agents      : {part}")
    phases = (
        f"{m['phases_completed']}/{m['phases_total']}"
        if m["phases_total"] else "-"
    )
    print(f"Phases      : {phases}")
    dur = f"{m['duration_seconds']/60:.1f}min" if m["duration_seconds"] else "-"
    print(f"Duration    : {dur}")
    print(f"Decisions   : {m['decisions_made']}")
    print(f"Action items: {m['action_items']}")
    print(f"Tool calls  : {m['total_tool_calls']}")
    print(f"Cross-ref   : {_bool_str(m['cross_reference'])}")
    print(f"Bugs found  : {m['bugs_found']}")
    if m["notes"]:
        print(f"Notes       : {m['notes']}")

    agents = conn.execute(
        "SELECT * FROM agent_metrics WHERE meeting_id=? ORDER BY id",
        (meeting_id,),
    ).fetchall()
    if agents:
        print("\n--- Agent Metrics ---")
        for a in agents:
            print(f"  [{a['agent_id']}] provider={a['provider'] or '-'}")
            print(f"    Messages   : sent={a['messages_sent']} recv={a['messages_received']}")
            print(f"    nmem_recall: {a['nmem_recall_count']} calls, {a['nmem_recall_ms']:.0f}ms total")
            print(f"    nmem_save  : {a['nmem_remember_count']} calls, {a['nmem_remember_ms']:.0f}ms total")
            print(f"    LLM calls  : {a['llm_call_count']} (avg {a['llm_avg_ms']:.0f}ms)")
            print(
                f"    Relevance  : must={a['relevance_must_speak']} "
                f"could={a['relevance_could_add']} pass={a['relevance_pass']}"
            )

    q = conn.execute(
        "SELECT * FROM quality_scores WHERE meeting_id=?", (meeting_id,)
    ).fetchone()
    if q:
        total = q["total_score"]
        verdict = "productive" if total >= 7 else ("acceptable" if total >= 4 else "waste")
        print(f"\n--- CEO Quality Score: {total}/9 ({verdict}) ---")
        print(f"  Build         : {_score_str(q['build_score'])}/2")
        print(f"  Tool purpose  : {_score_str(q['tool_purpose_score'])}/2")
        print(f"  Disagreement  : {_score_str(q['disagreement_score'])}/1")
        print(f"  New info      : {_score_str(q['new_info_score'])}/2")
        print(f"  Actionable    : {_score_str(q['actionable_score'])}/2")
        print(f"  Could be async: {_bool_str(q['could_be_async'])}")
        print(f"  All needed    : {_bool_str(q['all_agents_needed'])}")
        print(f"  Budget OK     : {_bool_str(q['budget_matched_topic'])}")
        print(f"  Better than prev: {_bool_str(q['better_than_previous'])}")
        if q["notes"]:
            print(f"  Notes: {q['notes']}")


def cmd_list(conn: sqlite3.Connection, args) -> None:
    rows = conn.execute(
        """
        SELECT m.id, m.date, m.title, q.total_score
        FROM meetings m
        LEFT JOIN quality_scores q ON q.meeting_id = m.id
        ORDER BY m.date, m.created_at
        """
    ).fetchall()

    if not rows:
        print("No meetings recorded.")
        return

    print(f"{'ID':<14} {'Date':<12} {'Score':>5}  Title")
    print("-" * 60)
    for r in rows:
        score = f"{r['total_score']}/9" if r["total_score"] is not None else "  -  "
        print(f"{r['id']:<14} {r['date']:<12} {score:>5}  {r['title']}")


# ---------------------------------------------------------------------------
# Vesper's metrics commands
# ---------------------------------------------------------------------------

def _auto_score_delta(messages: list[dict]) -> list[dict]:
    """Use Gemini CLI to auto-score contribution deltas for a list of messages."""
    results = []
    history_so_far = []

    for i, msg in enumerate(messages):
        history_text = "\n".join(
            f"[{h['agent_id']}]: {h['content']}" for h in history_so_far
        ) or "(no prior messages)"

        prompt_text = f"""Score this meeting message for contribution delta.

Prior messages:
{history_text}

New message from [{msg['agent_id']}]:
{msg['content']}

Score:
0 = echo/restatement of what was already said
1 = adds detail or evidence to an existing claim
2 = introduces a new claim, constraint, or contradiction

Reply with ONLY a JSON object: {{"score": 0|1|2, "reason": "one sentence"}}"""

        try:
            result = subprocess.run(
                ["gemini", "-p", prompt_text, "--model", "gemini-2.5-flash"],
                capture_output=True, text=True, timeout=30,
            )
            raw = result.stdout.strip()
            # Try to parse JSON from response (may have markdown fencing)
            raw = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)
            score = max(0, min(2, int(parsed.get("score", 1))))
            reason = parsed.get("reason", "")
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
            score = 1  # default to "adds detail" on failure
            reason = f"auto-score failed: {e}"

        results.append({
            "agent_id": msg["agent_id"],
            "message_index": i,
            "delta_score": score,
            "notes": reason,
        })
        history_so_far.append(msg)
        print(f"  [{msg['agent_id']}] msg {i}: delta={score} — {reason}")

    return results


def cmd_delta(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id
    m = conn.execute("SELECT id FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not m:
        print(f"Meeting {meeting_id} not found.")
        sys.exit(1)

    # --auto mode: read messages from log file and score with Gemini
    if getattr(args, "auto", False):
        log_file = args.log
        if not log_file:
            print("Error: --auto requires --log <path> pointing to a meeting log file.")
            sys.exit(1)

        # Parse speaking turns from log
        messages = []
        with open(log_file) as f:
            for line in f:
                # Match: 💬 [agent_id] content  OR  [agent] Speaking: "content..."
                if "💬 [" in line:
                    parts = line.split("💬 [", 1)[1]
                    agent_id = parts.split("]", 1)[0]
                    content = parts.split("]", 1)[1].strip()
                    messages.append({"agent_id": agent_id, "content": content[:500]})
                elif '] Speaking: "' in line:
                    agent_id = line.split("] Speaking:")[0].split("[")[-1]
                    content = line.split('Speaking: "', 1)[1].rstrip('"\n')
                    messages.append({"agent_id": agent_id, "content": content[:500]})

        if not messages:
            print(f"No speaking turns found in {log_file}")
            sys.exit(1)

        print(f"\nAuto-scoring {len(messages)} messages from {log_file}...\n")
        deltas = _auto_score_delta(messages)

        # Clear existing deltas for this meeting
        conn.execute("DELETE FROM contribution_deltas WHERE meeting_id=?", (meeting_id,))

        for d in deltas:
            conn.execute(
                "INSERT INTO contribution_deltas (meeting_id, agent_id, message_index, delta_score, notes) VALUES (?,?,?,?,?)",
                (meeting_id, d["agent_id"], d["message_index"], d["delta_score"], d.get("notes")),
            )

        avg = conn.execute(
            "SELECT AVG(delta_score) FROM contribution_deltas WHERE meeting_id=?", (meeting_id,)
        ).fetchone()[0]
        conn.execute("UPDATE meetings SET contribution_delta_avg=? WHERE id=?", (avg, meeting_id))
        conn.commit()
        print(f"\nAuto-scored {len(deltas)} messages. Avg delta: {avg:.2f}")
        return

    if not sys.stdin.isatty():
        data = json.load(sys.stdin)
        # Expect: {"deltas": [{"agent_id": "x", "message_index": 0, "delta_score": 2, "notes": "..."}, ...]}
        for d in data.get("deltas", [data]):
            conn.execute(
                "INSERT INTO contribution_deltas (meeting_id, agent_id, message_index, delta_score, notes) VALUES (?,?,?,?,?)",
                (meeting_id, d["agent_id"], d["message_index"], d["delta_score"], d.get("notes")),
            )
        avg = conn.execute(
            "SELECT AVG(delta_score) FROM contribution_deltas WHERE meeting_id=?", (meeting_id,)
        ).fetchone()[0]
        conn.execute("UPDATE meetings SET contribution_delta_avg=? WHERE id=?", (avg, meeting_id))
        conn.commit()
        print(f"Contribution deltas for {meeting_id} saved. Avg: {avg:.2f}")
        return

    print(f"\nContribution Delta scoring for {meeting_id}")
    print("Per message: 0=echo, 1=adds detail, 2=new claim/constraint\n")
    idx = 0
    while True:
        agent = input(f"  Agent (or 'done'): ").strip()
        if agent.lower() == "done":
            break
        score = prompt(f"  Delta score for {agent} msg {idx}", cast=int)
        notes = prompt(f"  Notes", optional=True)
        conn.execute(
            "INSERT INTO contribution_deltas (meeting_id, agent_id, message_index, delta_score, notes) VALUES (?,?,?,?,?)",
            (meeting_id, agent, idx, score, notes),
        )
        idx += 1

    avg = conn.execute(
        "SELECT AVG(delta_score) FROM contribution_deltas WHERE meeting_id=?", (meeting_id,)
    ).fetchone()[0]
    conn.execute("UPDATE meetings SET contribution_delta_avg=? WHERE id=?", (avg, meeting_id))
    conn.commit()
    print(f"\nDeltas saved. Avg: {avg:.2f}" if avg else "\nNo deltas recorded.")


def cmd_lag(conn: sqlite3.Connection, args) -> None:
    if not sys.stdin.isatty():
        data = json.load(sys.stdin)
        conn.execute(
            "INSERT INTO outcome_lag (decision_meeting_id, decision_description, referenced_meeting_id, lag_meetings, status) VALUES (?,?,?,?,?)",
            (data["decision_meeting_id"], data["decision_description"],
             data.get("referenced_meeting_id"), data.get("lag_meetings"), data.get("status", "never_referenced")),
        )
        conn.commit()
        print(f"Outcome lag recorded.")
        return

    print("\nRecord decision outcome tracking")
    meeting_id = prompt("Decision meeting ID")
    desc = prompt("Decision description")
    ref_meeting = prompt("Referenced in meeting ID", optional=True)
    lag = prompt("Lag (meetings between)", cast=int, optional=True)
    status = prompt("Status (executed/referenced/contradicted/never_referenced)", default="never_referenced")
    conn.execute(
        "INSERT INTO outcome_lag (decision_meeting_id, decision_description, referenced_meeting_id, lag_meetings, status) VALUES (?,?,?,?,?)",
        (meeting_id, desc, ref_meeting, lag, status),
    )
    conn.commit()
    print("Outcome lag recorded.")


def cmd_yield(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id
    m = conn.execute("SELECT decisions_made, phases_completed FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not m:
        print(f"Meeting {meeting_id} not found.")
        sys.exit(1)

    decisions = m["decisions_made"] or 0
    phases = m["phases_completed"] or 1
    dy = decisions / phases
    conn.execute("UPDATE meetings SET decision_yield=? WHERE id=?", (dy, meeting_id))
    conn.commit()
    print(f"Decision Yield for {meeting_id}: {dy:.2f} ({decisions} decisions / {phases} phases)")


def cmd_utilization(conn: sqlite3.Connection, args) -> None:
    meeting_id = args.meeting_id
    m = conn.execute("SELECT agents_participated FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    if not m:
        print(f"Meeting {meeting_id} not found.")
        sys.exit(1)

    if not sys.stdin.isatty():
        val = float(sys.stdin.read().strip())
    else:
        val = prompt("Phase utilization (0.0-1.0)", cast=float)

    conn.execute("UPDATE meetings SET phase_utilization=? WHERE id=?", (val, meeting_id))
    conn.commit()
    print(f"Phase utilization for {meeting_id}: {val:.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="eval",
        description="archon-eval — meeting evaluation CLI",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_DEFAULT,
        help=f"Path to SQLite DB (default: {DB_DEFAULT})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init",  help="Initialize the DB")

    p_add = sub.add_parser("add",   help="Add a meeting record")
    p_add.add_argument("meeting_id")

    p_agent = sub.add_parser("agent", help="Add agent metrics for a meeting")
    p_agent.add_argument("meeting_id")
    p_agent.add_argument("agent_id")

    p_score = sub.add_parser("score", help="Add CEO quality scores for a meeting")
    p_score.add_argument("meeting_id")

    sub.add_parser("trend", help="Show trend table across all meetings")

    p_show = sub.add_parser("show",  help="Show all data for a meeting")
    p_show.add_argument("meeting_id")

    sub.add_parser("list",  help="List all meetings")

    p_delta = sub.add_parser("delta", help="Score contribution deltas per message")
    p_delta.add_argument("meeting_id")
    p_delta.add_argument("--auto", action="store_true", help="Auto-score with Gemini (requires GEMINI_API_KEY)")
    p_delta.add_argument("--log", help="Path to meeting log file (required for --auto)")

    sub.add_parser("lag", help="Record decision outcome tracking")

    p_yield = sub.add_parser("yield", help="Compute decision yield for a meeting")
    p_yield.add_argument("meeting_id")

    p_util = sub.add_parser("utilization", help="Set phase utilization for a meeting")
    p_util.add_argument("meeting_id")

    args = parser.parse_args()
    conn = get_db(args.db)

    # Auto-init schema if tables don't exist
    init_db(conn)

    dispatch = {
        "init":        cmd_init,
        "add":         cmd_add,
        "agent":       cmd_agent,
        "score":       cmd_score,
        "trend":       cmd_trend,
        "show":        cmd_show,
        "list":        cmd_list,
        "delta":       cmd_delta,
        "lag":         cmd_lag,
        "yield":       cmd_yield,
        "utilization": cmd_utilization,
    }
    dispatch[args.command](conn, args)


if __name__ == "__main__":
    main()

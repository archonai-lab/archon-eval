#!/usr/bin/env python3
"""dashboard.py — Generate meeting analytics dashboard for archon-vision mkdocs site.

Usage:
    python3 dashboard.py

Reads ~/.archon/evaluations.db, writes charts to
/home/leviathanst/archon-vision/docs/archon-agent/charts/ and
dashboard.md to /home/leviathanst/archon-vision/docs/archon-agent/dashboard.md
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DB_PATH     = Path.home() / ".archon" / "evaluations.db"
CHARTS_DIR  = Path("/home/leviathanst/archon-vision/docs/archon-agent/charts")
DASHBOARD   = Path("/home/leviathanst/archon-vision/docs/archon-agent/dashboard.md")

CHARTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_data(conn: sqlite3.Connection):
    meetings = conn.execute(
        """
        SELECT
            m.id, m.title, m.methodology, m.date,
            m.agents_invited, m.agents_participated,
            m.phases_total, m.phases_completed,
            m.duration_seconds, m.total_tool_calls,
            m.cross_reference, m.decisions_made,
            m.action_items, m.bugs_found, m.notes,
            q.build_score, q.tool_purpose_score,
            q.disagreement_score, q.new_info_score,
            q.actionable_score, q.total_score,
            q.could_be_async, q.all_agents_needed,
            q.budget_matched_topic, q.better_than_previous,
            q.notes AS score_notes
        FROM meetings m
        LEFT JOIN quality_scores q ON q.meeting_id = m.id
        ORDER BY m.date, m.created_at
        """
    ).fetchall()

    agent_metrics = conn.execute(
        """
        SELECT * FROM agent_metrics ORDER BY meeting_id, agent_id
        """
    ).fetchall()

    return meetings, agent_metrics


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

DARK_BG     = "#1e1e2e"
SURFACE     = "#2a2a3e"
GREEN       = "#50fa7b"
BLUE        = "#8be9fd"
YELLOW      = "#f1fa8c"
RED         = "#ff5555"
ORANGE      = "#ffb86c"
PINK        = "#ff79c6"
PURPLE      = "#bd93f9"
MUTED       = "#6272a4"
TEXT_COLOR  = "#cdd6f4"

TARGET_DURATION_MIN = 10.0


def _setup_dark_fig(w=10, h=4.5):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(DARK_BG)
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax.xaxis.label.set_color(TEXT_COLOR)
    ax.yaxis.label.set_color(TEXT_COLOR)
    ax.title.set_color(TEXT_COLOR)
    for spine in ax.spines.values():
        spine.set_edgecolor(MUTED)
    return fig, ax


def _setup_dark_fig_twinx(w=10, h=4.5):
    fig, ax1 = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(DARK_BG)
    ax1.set_facecolor(SURFACE)
    ax1.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax1.xaxis.label.set_color(TEXT_COLOR)
    ax1.yaxis.label.set_color(TEXT_COLOR)
    ax1.title.set_color(TEXT_COLOR)
    for spine in ax1.spines.values():
        spine.set_edgecolor(MUTED)
    ax2 = ax1.twinx()
    ax2.set_facecolor(SURFACE)
    ax2.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax2.yaxis.label.set_color(TEXT_COLOR)
    for spine in ax2.spines.values():
        spine.set_edgecolor(MUTED)
    return fig, ax1, ax2


def _save(fig, name: str) -> Path:
    path = CHARTS_DIR / name
    fig.savefig(str(path), dpi=150, bbox_inches="tight",
                facecolor=DARK_BG, transparent=False)
    plt.close(fig)
    return path


def short_id(meeting_id: str) -> str:
    """Shorten meeting ID for axis labels."""
    # e.g. mtg1-sdk-review -> mtg1, WStuhggs3sG5 -> Wstu...
    if meeting_id.startswith("mtg"):
        return meeting_id.split("-")[0]
    return meeting_id[:7]


# ---------------------------------------------------------------------------
# Chart 1: Participation & Phases (dual-axis)
# ---------------------------------------------------------------------------

def chart_participation(meetings) -> Path:
    fig, ax1, ax2 = _setup_dark_fig_twinx(w=11, h=5)

    labels = [short_id(m["id"]) for m in meetings]
    x = np.arange(len(labels))
    width = 0.35

    # Participation %
    part_pcts = []
    for m in meetings:
        if m["agents_invited"] and m["agents_participated"] is not None:
            part_pcts.append(m["agents_participated"] / m["agents_invited"] * 100)
        else:
            part_pcts.append(0)

    # Phases ratio %
    phase_pcts = []
    for m in meetings:
        if m["phases_total"] and m["phases_completed"] is not None:
            phase_pcts.append(m["phases_completed"] / m["phases_total"] * 100)
        else:
            phase_pcts.append(0)

    bars1 = ax1.bar(x - width / 2, part_pcts,  width, color=GREEN,  alpha=0.85, label="Participation %", zorder=3)
    bars2 = ax2.bar(x + width / 2, phase_pcts, width, color=BLUE,   alpha=0.85, label="Phases completed %", zorder=3)

    ax1.set_ylim(0, 120)
    ax2.set_ylim(0, 120)
    ax1.set_ylabel("Participation %", color=GREEN)
    ax2.set_ylabel("Phases completed %", color=BLUE)
    ax1.yaxis.label.set_color(GREEN)
    ax2.yaxis.label.set_color(BLUE)
    ax1.tick_params(axis="y", colors=GREEN)
    ax2.tick_params(axis="y", colors=BLUE)

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=30, ha="right", color=TEXT_COLOR)
    ax1.set_title("Participation & Phases Completed", color=TEXT_COLOR, pad=12, fontsize=12)
    ax1.yaxis.grid(True, color=MUTED, alpha=0.3, linestyle="--")
    ax1.set_axisbelow(True)

    # Bar value labels
    for bar in bars1:
        h = bar.get_height()
        if h > 0:
            ax1.text(bar.get_x() + bar.get_width() / 2, h + 2, f"{h:.0f}%",
                     ha="center", va="bottom", color=GREEN, fontsize=8)
    for bar in bars2:
        h = bar.get_height()
        if h > 0:
            ax2.text(bar.get_x() + bar.get_width() / 2, h + 2, f"{h:.0f}%",
                     ha="center", va="bottom", color=BLUE, fontsize=8)

    legend_patches = [
        mpatches.Patch(color=GREEN, alpha=0.85, label="Participation %"),
        mpatches.Patch(color=BLUE,  alpha=0.85, label="Phases completed %"),
    ]
    ax1.legend(handles=legend_patches, facecolor=SURFACE, edgecolor=MUTED,
               labelcolor=TEXT_COLOR, fontsize=9, loc="upper left")

    fig.tight_layout()
    return _save(fig, "participation.png")


# ---------------------------------------------------------------------------
# Chart 2: Tool Usage (stacked bar — nmem_recall vs nmem_remember)
# ---------------------------------------------------------------------------

def chart_tool_usage(meetings, agent_metrics) -> Path:
    fig, ax = _setup_dark_fig(w=11, h=5)

    labels = [short_id(m["id"]) for m in meetings]
    x = np.arange(len(labels))

    # Sum per meeting
    recall_by_meeting    = {m["id"]: 0 for m in meetings}
    remember_by_meeting  = {m["id"]: 0 for m in meetings}
    cross_ref_meetings   = {m["id"]: bool(m["cross_reference"]) for m in meetings}

    for a in agent_metrics:
        mid = a["meeting_id"]
        if mid in recall_by_meeting:
            recall_by_meeting[mid]   += a["nmem_recall_count"] or 0
            remember_by_meeting[mid] += a["nmem_remember_count"] or 0

    recall_vals   = [recall_by_meeting[m["id"]]   for m in meetings]
    remember_vals = [remember_by_meeting[m["id"]] for m in meetings]

    bars_recall   = ax.bar(x, recall_vals,   0.6, color=PURPLE, alpha=0.9, label="nmem_recall", zorder=3)
    bars_remember = ax.bar(x, remember_vals, 0.6, bottom=recall_vals,
                           color=PINK, alpha=0.9, label="nmem_remember", zorder=3)

    # Highlight cross-reference meetings with an outline
    for i, m in enumerate(meetings):
        if cross_ref_meetings[m["id"]]:
            total = recall_vals[i] + remember_vals[i]
            ax.bar(i, total, 0.6, color="none",
                   edgecolor=YELLOW, linewidth=2.5, zorder=4, label="_nolegend_")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", color=TEXT_COLOR)
    ax.set_ylabel("Memory tool calls", color=TEXT_COLOR)
    ax.set_title("Memory Tool Usage per Meeting", color=TEXT_COLOR, pad=12, fontsize=12)
    ax.yaxis.grid(True, color=MUTED, alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)

    xref_patch = mpatches.Patch(facecolor="none", edgecolor=YELLOW,
                                linewidth=2, label="Cross-reference meeting")
    legend_patches = [
        mpatches.Patch(color=PURPLE, alpha=0.9, label="nmem_recall"),
        mpatches.Patch(color=PINK,   alpha=0.9, label="nmem_remember"),
        xref_patch,
    ]
    ax.legend(handles=legend_patches, facecolor=SURFACE, edgecolor=MUTED,
              labelcolor=TEXT_COLOR, fontsize=9)

    fig.tight_layout()
    return _save(fig, "tool-usage.png")


# ---------------------------------------------------------------------------
# Chart 3: Meeting Duration (line)
# ---------------------------------------------------------------------------

def chart_duration(meetings) -> Path:
    fig, ax = _setup_dark_fig(w=11, h=5)

    labels  = []
    durations = []
    for m in meetings:
        if m["duration_seconds"] is not None:
            labels.append(short_id(m["id"]))
            durations.append(m["duration_seconds"] / 60)

    if not labels:
        ax.text(0.5, 0.5, "No duration data", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=12)
        ax.set_title("Meeting Duration", color=TEXT_COLOR, pad=12, fontsize=12)
        fig.tight_layout()
        return _save(fig, "duration.png")

    x = np.arange(len(labels))
    ax.plot(x, durations, color=ORANGE, linewidth=2.5, marker="o",
            markersize=8, markerfacecolor=ORANGE, zorder=4)
    ax.fill_between(x, durations, alpha=0.15, color=ORANGE)

    # Color points: green if at/below target, red if above
    for i, d in enumerate(durations):
        color = GREEN if d <= TARGET_DURATION_MIN else RED
        ax.scatter(i, d, color=color, s=80, zorder=5)
        ax.text(i, d + 0.4, f"{d:.1f}m", ha="center", va="bottom",
                color=color, fontsize=8)

    # Target line
    ax.axhline(TARGET_DURATION_MIN, color=GREEN, linestyle="--", linewidth=1.5,
               alpha=0.7, label=f"Target ({TARGET_DURATION_MIN:.0f} min)")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", color=TEXT_COLOR)
    ax.set_ylabel("Duration (minutes)", color=TEXT_COLOR)
    ax.set_title("Meeting Duration Trend", color=TEXT_COLOR, pad=12, fontsize=12)
    ax.yaxis.grid(True, color=MUTED, alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)

    ax.legend(facecolor=SURFACE, edgecolor=MUTED, labelcolor=TEXT_COLOR, fontsize=9)

    fig.tight_layout()
    return _save(fig, "duration.png")


# ---------------------------------------------------------------------------
# Chart 4: Quality Scores (stacked bar by dimension)
# ---------------------------------------------------------------------------

def chart_quality(meetings) -> Path:
    scored = [m for m in meetings if m["total_score"] is not None]

    fig, ax = _setup_dark_fig(w=max(8, len(scored) * 2.5), h=5.5)

    if not scored:
        ax.text(0.5, 0.5, "No quality scores yet", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=12)
        ax.set_title("Quality Scores", color=TEXT_COLOR, pad=12, fontsize=12)
        fig.tight_layout()
        return _save(fig, "quality.png")

    labels = [short_id(m["id"]) for m in scored]
    x = np.arange(len(labels))
    w = 0.55

    dims = [
        ("build_score",        "Build",       "#8be9fd"),
        ("tool_purpose_score", "Tool purpose", "#bd93f9"),
        ("disagreement_score", "Disagreement","#ff79c6"),
        ("new_info_score",     "New info",    "#50fa7b"),
        ("actionable_score",   "Actionable",  "#ffb86c"),
    ]

    bottoms = np.zeros(len(scored))
    for col, label, color in dims:
        vals = [m[col] or 0 for m in scored]
        ax.bar(x, vals, w, bottom=bottoms, color=color, alpha=0.88,
               label=label, zorder=3)
        bottoms += np.array(vals, dtype=float)

    # Colored band backgrounds
    ax.axhspan(0, 3, facecolor=RED,    alpha=0.04, zorder=0)
    ax.axhspan(3, 7, facecolor=YELLOW, alpha=0.04, zorder=0)
    ax.axhspan(7, 9, facecolor=GREEN,  alpha=0.04, zorder=0)

    # Band labels (right edge)
    ax.text(len(scored) - 0.5 + 0.35, 1.5, "waste",        color=RED,    fontsize=7, va="center")
    ax.text(len(scored) - 0.5 + 0.35, 5.0, "acceptable",   color=YELLOW, fontsize=7, va="center")
    ax.text(len(scored) - 0.5 + 0.35, 8.0, "productive",   color=GREEN,  fontsize=7, va="center")

    # Total labels on bars
    for i, m in enumerate(scored):
        total = m["total_score"]
        ax.text(i, total + 0.12, f"{total}/9", ha="center", va="bottom",
                color=TEXT_COLOR, fontsize=9, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", color=TEXT_COLOR)
    ax.set_ylabel("Score", color=TEXT_COLOR)
    ax.set_title("CEO Quality Scores", color=TEXT_COLOR, pad=12, fontsize=12)
    ax.set_ylim(0, 10.5)
    ax.yaxis.grid(True, color=MUTED, alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)

    ax.legend(facecolor=SURFACE, edgecolor=MUTED, labelcolor=TEXT_COLOR,
              fontsize=8, loc="upper left", ncols=2)

    fig.tight_layout()
    return _save(fig, "quality.png")


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def _bool_md(val) -> str:
    if val is None:
        return "—"
    return "Yes" if val else "No"


def _score_md(val) -> str:
    return str(val) if val is not None else "—"


def _dur_md(seconds) -> str:
    if seconds is None:
        return "—"
    m = seconds / 60
    return f"{m:.1f} min"


def _part_md(invited, participated) -> str:
    if not invited or participated is None:
        return "—"
    pct = participated / invited * 100
    return f"{participated}/{invited} ({pct:.0f}%)"


def _phases_md(total, completed) -> str:
    if not total or completed is None:
        return "—"
    return f"{completed}/{total}"


def _verdict(total_score) -> str:
    if total_score is None:
        return ""
    if total_score >= 7:
        return "productive"
    if total_score >= 4:
        return "acceptable"
    return "waste"


def render_meeting_log(meetings) -> str:
    lines = [
        "| Meeting | Date | Participation | Phases | Duration | Tools | Score |",
        "|---------|------|:---:|:---:|:---:|:---:|:---:|",
    ]
    for m in meetings:
        score_str = f"{m['total_score']}/9" if m["total_score"] is not None else "—"
        lines.append(
            f"| [{m['id']}](#{m['id'].lower().replace(' ', '-')}) {m['title']} "
            f"| {m['date']} "
            f"| {_part_md(m['agents_invited'], m['agents_participated'])} "
            f"| {_phases_md(m['phases_total'], m['phases_completed'])} "
            f"| {_dur_md(m['duration_seconds'])} "
            f"| {m['total_tool_calls'] or 0} "
            f"| {score_str} |"
        )
    return "\n".join(lines)


def render_agent_table(agents) -> str:
    if not agents:
        return "_No agent metrics recorded._"
    lines = [
        "| Agent | Provider | Msgs sent | nmem_recall | nmem_remember | LLM calls | LLM avg ms |",
        "|-------|----------|:---------:|:-----------:|:-------------:|:---------:|:----------:|",
    ]
    for a in agents:
        lines.append(
            f"| `{a['agent_id']}` | {a['provider'] or '—'} "
            f"| {a['messages_sent']} "
            f"| {a['nmem_recall_count']} ({a['nmem_recall_ms']:.0f}ms) "
            f"| {a['nmem_remember_count']} ({a['nmem_remember_ms']:.0f}ms) "
            f"| {a['llm_call_count']} "
            f"| {a['llm_avg_ms']:.0f} |"
        )
    return "\n".join(lines)


def render_quality_breakdown(m) -> str:
    if m["total_score"] is None:
        return ""
    verdict = _verdict(m["total_score"])
    lines = [
        "",
        f"**CEO Quality Score: {m['total_score']}/9 — {verdict}**",
        "",
        "| Dimension | Score | Max |",
        "|-----------|:-----:|:---:|",
        f"| Build | {_score_md(m['build_score'])} | 2 |",
        f"| Tool purpose | {_score_md(m['tool_purpose_score'])} | 2 |",
        f"| Disagreement | {_score_md(m['disagreement_score'])} | 1 |",
        f"| New info | {_score_md(m['new_info_score'])} | 2 |",
        f"| Actionable | {_score_md(m['actionable_score'])} | 2 |",
        "",
        f"Could be async: {_bool_md(m['could_be_async'])} &nbsp;·&nbsp; "
        f"All agents needed: {_bool_md(m['all_agents_needed'])} &nbsp;·&nbsp; "
        f"Budget matched: {_bool_md(m['budget_matched_topic'])} &nbsp;·&nbsp; "
        f"Better than prev: {_bool_md(m['better_than_previous'])}",
    ]
    if m["score_notes"]:
        lines += ["", f"> {m['score_notes']}"]
    return "\n".join(lines)


def render_per_meeting(meetings, agent_metrics) -> str:
    # Group agents by meeting
    agents_by_meeting: dict[str, list] = {}
    for a in agent_metrics:
        agents_by_meeting.setdefault(a["meeting_id"], []).append(a)

    blocks = []
    for m in meetings:
        score_str = (
            f", score {m['total_score']}/9 — {_verdict(m['total_score'])}"
            if m["total_score"] is not None else ""
        )
        summary_line = f"{m['id']} — {m['title']} ({m['date']}{score_str})"

        agents = agents_by_meeting.get(m["id"], [])
        agent_table = render_agent_table(agents)
        quality = render_quality_breakdown(m)

        notes_block = f"\n\n> {m['notes']}" if m["notes"] else ""

        block = (
            f"<details>\n"
            f"<summary>{summary_line}</summary>\n\n"
            f"{agent_table}\n"
            f"{quality}"
            f"{notes_block}\n\n"
            f"</details>"
        )
        blocks.append(block)

    return "\n\n".join(blocks)


def generate_dashboard(meetings, agent_metrics) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    meeting_log     = render_meeting_log(meetings)
    per_meeting     = render_per_meeting(meetings, agent_metrics)

    return f"""# Meeting Dashboard

> Auto-generated from `~/.archon/evaluations.db`  
> Last updated: {ts}

## Trends

![Participation & Phases](charts/participation.png)

![Tool Usage](charts/tool-usage.png)

![Duration](charts/duration.png)

![Quality Scores](charts/quality.png)

## Meeting Log

{meeting_log}

## Per-Meeting Details

{per_meeting}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = get_db()

    print("Loading data from DB...")
    meetings, agent_metrics = load_data(conn)
    print(f"  {len(meetings)} meetings, {len(agent_metrics)} agent metric rows")

    print("Generating Chart 1: Participation & Phases...")
    chart_participation(meetings)

    print("Generating Chart 2: Tool Usage...")
    chart_tool_usage(meetings, agent_metrics)

    print("Generating Chart 3: Duration...")
    chart_duration(meetings)

    print("Generating Chart 4: Quality Scores...")
    chart_quality(meetings)

    print("Rendering dashboard.md...")
    md = generate_dashboard(meetings, agent_metrics)
    DASHBOARD.write_text(md, encoding="utf-8")

    print(f"\nDone.")
    print(f"  Charts : {CHARTS_DIR}/")
    print(f"  Dashboard: {DASHBOARD}")


if __name__ == "__main__":
    main()

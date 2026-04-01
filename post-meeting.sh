#!/usr/bin/env bash
# post-meeting.sh — Automate the post-meeting evaluation pipeline.
#
# Usage:
#   ./post-meeting.sh <meeting_id> --logs <prefix_or_paths> [--no-score]
#
# Examples:
#   ./post-meeting.sh Y45MLcfmuVkq --logs m8
#   ./post-meeting.sh Y45MLcfmuVkq --logs /tmp/eval-sherlock.log,/tmp/eval-rune.log
#   ./post-meeting.sh Y45MLcfmuVkq --logs m8 --no-score

set -euo pipefail

EVAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_PY="$EVAL_DIR/eval.py"
DASHBOARD_PY="$EVAL_DIR/dashboard.py"
ARCHON_VISION="${ARCHON_VISION_DIR:-$HOME/archon-vision}"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

MEETING_ID=""
LOGS_ARG=""
NO_SCORE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --logs)   LOGS_ARG="$2"; shift 2 ;;
    --no-score) NO_SCORE=true; shift ;;
    -*)       echo "Unknown option: $1" >&2; exit 1 ;;
    *)
      if [[ -z "$MEETING_ID" ]]; then
        MEETING_ID="$1"
      else
        echo "Unexpected argument: $1" >&2; exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$MEETING_ID" ]]; then
  echo "Usage: post-meeting.sh <meeting_id> --logs <prefix|paths,...> [--no-score]" >&2
  exit 1
fi

if [[ -z "$LOGS_ARG" ]]; then
  echo "Error: --logs is required" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve log files
# ---------------------------------------------------------------------------

declare -a LOG_FILES=()

if [[ "$LOGS_ARG" == *"/"* || "$LOGS_ARG" == *".log"* ]]; then
  # Explicit paths, comma-separated
  IFS=',' read -ra LOG_FILES <<< "$LOGS_ARG"
else
  # Prefix — glob /tmp/<prefix>-*.log
  while IFS= read -r -d '' f; do
    LOG_FILES+=("$f")
  done < <(find /tmp -maxdepth 1 -name "${LOGS_ARG}-*.log" -print0 2>/dev/null)

  if [[ ${#LOG_FILES[@]} -eq 0 ]]; then
    echo "Error: no log files found matching /tmp/${LOGS_ARG}-*.log" >&2
    exit 1
  fi
fi

echo ""
echo "Meeting ID : $MEETING_ID"
echo "Log files  :"
for f in "${LOG_FILES[@]}"; do
  echo "  $f"
done
echo ""

# ---------------------------------------------------------------------------
# Parse Meeting Analytics block from a single log file.
# Outputs a JSON object to stdout.
# ---------------------------------------------------------------------------

parse_log() {
  local logfile="$1"

  # Agent name from header line: "Meeting Analytics -- <agent>"
  local agent_name
  agent_name=$(grep -m1 "^Meeting Analytics --" "$logfile" | awk '{print $NF}')
  if [[ -z "$agent_name" ]]; then
    echo "Warning: no 'Meeting Analytics' block found in $logfile" >&2
    return 1
  fi

  # Duration
  local duration
  duration=$(grep -m1 "^Duration:" "$logfile" | grep -oP 'Duration:\s*\K[0-9.]+')

  # Messages received / sent
  local msg_recv msg_sent
  msg_recv=$(grep -m1 "^Duration:" "$logfile" | grep -oP 'Messages received:\s*\K[0-9]+')
  msg_sent=$(grep -m1 "^Duration:" "$logfile" | grep -oP 'Messages sent:\s*\K[0-9]+')

  # nmem_recall — line like: "  nmem_recall          1x  0.2s"
  local recall_count recall_ms
  recall_count=$(grep -m1 "nmem_recall " "$logfile" | grep -oP '^\s+nmem_recall\s+\K[0-9]+(?=x)')
  # total seconds -> convert to ms
  local recall_s
  recall_s=$(grep -m1 "nmem_recall " "$logfile" | grep -oP '[0-9.]+(?=s\s*$)')
  recall_ms=$(awk "BEGIN { printf \"%.0f\", ${recall_s:-0} * 1000 }")

  # nmem_remember
  local remember_count remember_ms remember_s
  remember_count=$(grep -m1 "nmem_remember " "$logfile" | grep -oP '^\s+nmem_remember\s+\K[0-9]+(?=x)')
  remember_s=$(grep -m1 "nmem_remember " "$logfile" | grep -oP '[0-9.]+(?=s\s*$)')
  remember_ms=$(awk "BEGIN { printf \"%.0f\", ${remember_s:-0} * 1000 }")

  # LLM calls — line like: "  Total: 27 calls, 588.0s (avg 21.8s)"
  local llm_count llm_avg_ms llm_avg_s
  llm_count=$(grep -m1 "Total:.*calls" "$logfile" | grep -oP 'Total:\s*\K[0-9]+(?=\s+calls)')
  llm_avg_s=$(grep -m1 "Total:.*calls" "$logfile" | grep -oP 'avg\s+\K[0-9.]+(?=s\))')
  llm_avg_ms=$(awk "BEGIN { printf \"%.0f\", ${llm_avg_s:-0} * 1000 }")

  # Relevance — line like: "Relevance: 3x must_speak, 11x could_add, 9x pass"
  local must_speak could_add rel_pass
  must_speak=$(grep -m1 "^Relevance:" "$logfile" | grep -oP '[0-9]+(?=x must_speak)')
  could_add=$(grep -m1  "^Relevance:" "$logfile" | grep -oP '[0-9]+(?=x could_add)')
  rel_pass=$(grep -m1   "^Relevance:" "$logfile" | grep -oP '[0-9]+(?=x pass)')

  # Emit JSON (agent_name returned separately via name-ref — use a temp file trick)
  # We can't easily return two values, so we print agent_name on line 1 and JSON on line 2.
  printf '%s\n' "$agent_name"
  printf '{'
  printf '"messages_sent":%s,'       "${msg_sent:-0}"
  printf '"messages_received":%s,'   "${msg_recv:-0}"
  printf '"nmem_recall_count":%s,'   "${recall_count:-0}"
  printf '"nmem_recall_ms":%s,'      "${recall_ms:-0}"
  printf '"nmem_remember_count":%s,' "${remember_count:-0}"
  printf '"nmem_remember_ms":%s,'    "${remember_ms:-0}"
  printf '"llm_call_count":%s,'      "${llm_count:-0}"
  printf '"llm_avg_ms":%s,'          "${llm_avg_ms:-0}"
  printf '"relevance_must_speak":%s,' "${must_speak:-0}"
  printf '"relevance_could_add":%s,'  "${could_add:-0}"
  printf '"relevance_pass":%s'        "${rel_pass:-0}"
  printf '}\n'

  # Return duration for aggregation (to stdout as 3rd line)
  printf '%s\n' "${duration:-0}"
}

# ---------------------------------------------------------------------------
# Parse all logs — collect agent data and overall duration
# ---------------------------------------------------------------------------

declare -A AGENT_JSON   # agent_id -> json string
declare -A AGENT_PHASES # agent_id -> phases string
TOTAL_DURATION=0

for logfile in "${LOG_FILES[@]}"; do
  if [[ ! -f "$logfile" ]]; then
    echo "Warning: log file not found: $logfile" >&2
    continue
  fi

  # parse_log outputs 3 lines: agent_name, json, duration
  mapfile -t parsed < <(parse_log "$logfile" 2>&1)
  if [[ ${#parsed[@]} -lt 3 ]]; then
    echo "Warning: could not parse $logfile (${parsed[*]})" >&2
    continue
  fi

  agent_id="${parsed[0]}"
  agent_json="${parsed[1]}"
  agent_dur="${parsed[2]}"

  AGENT_JSON["$agent_id"]="$agent_json"

  # Phases
  phases=$(grep -m1 "^Duration:" "$logfile" | grep -oP 'Phases:\s*\K[A-Z ->]+' || echo "")
  AGENT_PHASES["$agent_id"]="$phases"

  # Track max duration across agents (they run concurrently)
  TOTAL_DURATION=$(awk "BEGIN { d=$agent_dur; t=$TOTAL_DURATION; print (d > t ? d : t) }")
done

if [[ ${#AGENT_JSON[@]} -eq 0 ]]; then
  echo "Error: no agent logs parsed successfully." >&2
  exit 1
fi

AGENT_COUNT=${#AGENT_JSON[@]}
echo "Parsed $AGENT_COUNT agent log(s): ${!AGENT_JSON[*]}"
echo ""

# ---------------------------------------------------------------------------
# Interactive: meeting metadata
# ---------------------------------------------------------------------------

echo "=== Meeting Info ==="
read -rp "  Title: " MEETING_TITLE
if [[ -z "$MEETING_TITLE" ]]; then
  echo "Title is required." >&2; exit 1
fi

read -rp "  Methodology (review/brainstorm/triage/hiring) [optional]: " METHODOLOGY
read -rp "  Date (YYYY-MM-DD) [$(date +%Y-%m-%d)]: " MEETING_DATE
MEETING_DATE="${MEETING_DATE:-$(date +%Y-%m-%d)}"
read -rp "  Decisions made [0]: " DECISIONS; DECISIONS="${DECISIONS:-0}"
read -rp "  Action items [0]: " ACTIONS; ACTIONS="${ACTIONS:-0}"
read -rp "  Total tool calls [optional]: " TOOL_CALLS; TOOL_CALLS="${TOOL_CALLS:-0}"
read -rp "  Cross-reference? (y/n) [n]: " CROSS_REF_RAW
CROSS_REF=0; [[ "${CROSS_REF_RAW,,}" == "y" ]] && CROSS_REF=1
read -rp "  Bugs found [0]: " BUGS; BUGS="${BUGS:-0}"
read -rp "  Notes [optional]: " MEETING_NOTES

# Compute phases from first agent (they should be the same meeting)
FIRST_AGENT="${!AGENT_PHASES[*]}"
FIRST_AGENT="${FIRST_AGENT%% *}"
PHASES_STR="${AGENT_PHASES[$FIRST_AGENT]:-}"
PHASES_TOTAL=$(echo "$PHASES_STR" | awk -F'->' '{print NF}')
PHASES_TOTAL="${PHASES_TOTAL:-0}"

echo ""

# ---------------------------------------------------------------------------
# Step 1: Insert meeting record
# ---------------------------------------------------------------------------

echo "--- Inserting meeting record..."
MEETING_JSON=$(printf '{
  "title": %s,
  "methodology": %s,
  "date": "%s",
  "agents_invited": %d,
  "agents_participated": %d,
  "phases_total": %d,
  "phases_completed": %d,
  "duration_seconds": %s,
  "decisions_made": %d,
  "action_items": %d,
  "total_tool_calls": %d,
  "cross_reference": %d,
  "bugs_found": %d,
  "notes": %s
}' \
  "$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MEETING_TITLE")" \
  "$(python3 -c "import json,sys; v=sys.argv[1]; print(json.dumps(v) if v else 'null')" "${METHODOLOGY:-}")" \
  "$MEETING_DATE" \
  "$AGENT_COUNT" \
  "$AGENT_COUNT" \
  "$PHASES_TOTAL" \
  "$PHASES_TOTAL" \
  "$TOTAL_DURATION" \
  "$DECISIONS" \
  "$ACTIONS" \
  "$TOOL_CALLS" \
  "$CROSS_REF" \
  "$BUGS" \
  "$(python3 -c "import json,sys; v=sys.argv[1]; print(json.dumps(v) if v else 'null')" "${MEETING_NOTES:-}")")

echo "$MEETING_JSON" | python3 "$EVAL_PY" add "$MEETING_ID"

# ---------------------------------------------------------------------------
# Step 2: Insert agent metrics
# ---------------------------------------------------------------------------

echo ""
echo "--- Inserting agent metrics..."
for agent_id in "${!AGENT_JSON[@]}"; do
  echo "  Agent: $agent_id"
  echo "${AGENT_JSON[$agent_id]}" | python3 "$EVAL_PY" agent "$MEETING_ID" "$agent_id"
done

# ---------------------------------------------------------------------------
# Step 3: CEO quality score (interactive)
# ---------------------------------------------------------------------------

if [[ "$NO_SCORE" == false ]]; then
  echo ""
  echo "=== CEO Quality Score ==="
  echo "  Scoring guide:"
  echo "    build         0-2  (Did this build on previous work?)"
  echo "    tool_purpose  0-2  (Right tool for the job?)"
  echo "    disagreement  0-1  (Any real disagreement/pushback?)"
  echo "    new_info      0-2  (New information surfaced?)"
  echo "    actionable    0-2  (Clear action items produced?)"
  echo ""

  score_prompt() {
    local label="$1" max="$2"
    local val
    while true; do
      read -rp "  $label (0-$max): " val
      if [[ "$val" =~ ^[0-9]+$ ]] && (( val >= 0 && val <= max )); then
        echo "$val"
        return
      fi
      echo "  Enter a number between 0 and $max"
    done
  }

  bool_prompt() {
    local label="$1"
    local val
    while true; do
      read -rp "  $label (y/n): " val
      case "${val,,}" in
        y|yes) echo "true"; return ;;
        n|no)  echo "false"; return ;;
        *)     echo "  Enter y or n" ;;
      esac
    done
  }

  BUILD=$(score_prompt "build" 2)
  TOOL_PURPOSE=$(score_prompt "tool_purpose" 2)
  DISAGREEMENT=$(score_prompt "disagreement" 1)
  NEW_INFO=$(score_prompt "new_info" 2)
  ACTIONABLE=$(score_prompt "actionable" 2)
  COULD_ASYNC=$(bool_prompt "Could this have been async?")
  ALL_NEEDED=$(bool_prompt "Were all agents needed?")
  BUDGET_OK=$(bool_prompt "Did budget match topic?")
  read -rp "  Better than previous? (y/n/skip) [skip]: " BETTER_RAW
  case "${BETTER_RAW,,}" in
    y|yes) BETTER="true" ;;
    n|no)  BETTER="false" ;;
    *)     BETTER="null" ;;
  esac
  read -rp "  Score notes [optional]: " SCORE_NOTES

  TOTAL=$(( BUILD + TOOL_PURPOSE + DISAGREEMENT + NEW_INFO + ACTIONABLE ))
  if (( TOTAL >= 7 )); then VERDICT="productive"
  elif (( TOTAL >= 4 )); then VERDICT="acceptable"
  else VERDICT="waste"
  fi
  echo ""
  echo "  Total: $TOTAL/9 ($VERDICT)"

  echo ""
  echo "--- Inserting quality score..."
  SCORE_JSON=$(printf '{
  "build_score": %d,
  "tool_purpose_score": %d,
  "disagreement_score": %d,
  "new_info_score": %d,
  "actionable_score": %d,
  "could_be_async": %s,
  "all_agents_needed": %s,
  "budget_matched_topic": %s,
  "better_than_previous": %s,
  "notes": %s
}' \
    "$BUILD" "$TOOL_PURPOSE" "$DISAGREEMENT" "$NEW_INFO" "$ACTIONABLE" \
    "$COULD_ASYNC" "$ALL_NEEDED" "$BUDGET_OK" "$BETTER" \
    "$(python3 -c "import json,sys; v=sys.argv[1]; print(json.dumps(v) if v else 'null')" "${SCORE_NOTES:-}")")

  echo "$SCORE_JSON" | python3 "$EVAL_PY" score "$MEETING_ID"
fi

# ---------------------------------------------------------------------------
# Step 4: Regenerate dashboard
# ---------------------------------------------------------------------------

echo ""
echo "--- Regenerating dashboard..."
python3 "$DASHBOARD_PY"

# ---------------------------------------------------------------------------
# Step 5: Commit and push archon-vision
# ---------------------------------------------------------------------------

echo ""
echo "--- Committing archon-vision..."
cd "$ARCHON_VISION"

if git diff --quiet && git diff --cached --quiet; then
  echo "  No changes to commit in archon-vision."
else
  git add docs/archon-agent/
  git commit -m "eval: add meeting $MEETING_ID

Auto-generated by post-meeting.sh"
  git push
  echo "  Pushed to archon-vision."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "=== Done ==="
echo "  Meeting : $MEETING_ID"
echo "  Agents  : ${!AGENT_JSON[*]}"
if [[ "$NO_SCORE" == false ]]; then
  echo "  Score   : $TOTAL/9 ($VERDICT)"
fi
echo ""
echo "  View full record: python3 $EVAL_PY show $MEETING_ID"

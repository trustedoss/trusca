#!/usr/bin/env bash
# TrustedOSS Portal — dogfooding wall-clock timer.
#
# Records milestones and friction with elapsed-since-start so a dogfooding
# session can be reported as wall-clock data instead of guesswork. Designed
# for the persona dogfooding pass described in
# docs/sessions/2026-05-11-dogfooding-first-30min.md and the template at
# docs/sessions/dogfooding-template.md.
#
# Usage:
#   bash scripts/dogfood-timer.sh start <task>
#   bash scripts/dogfood-timer.sh mark <task> "<milestone label>"
#   bash scripts/dogfood-timer.sh friction <task> <D|U|S|P|C> "<where>" "<note>"
#   bash scripts/dogfood-timer.sh report <task>          # print without finalizing
#   bash scripts/dogfood-timer.sh end <task>             # finalize + print
#
# Tasks: any string. Recommend a / b / g (Task α / β / γ) to match the
# session plan; ASCII keeps shell quoting trivial.
#
# Storage: ${HOME}/.trustedoss-dogfood/<task>.log (one line per event).
# Format: <unix-ts>\t<verb>\t<elapsed-seconds>\t<mm:ss>\t<payload>

set -euo pipefail

LOG_DIR="${HOME}/.trustedoss-dogfood"
mkdir -p "${LOG_DIR}"

die() { printf '%s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage:
  dogfood-timer.sh start <task>
  dogfood-timer.sh mark <task> "<milestone label>"
  dogfood-timer.sh friction <task> <D|U|S|P|C> "<where>" "<note>"
  dogfood-timer.sh report <task>
  dogfood-timer.sh end <task>

Examples:
  bash scripts/dogfood-timer.sh start a
  bash scripts/dogfood-timer.sh mark a "wizard 완료"
  bash scripts/dogfood-timer.sh friction a D "docker-compose.md L140" "DT OPEN never closes"
  bash scripts/dogfood-timer.sh end a
USAGE
  exit 2
}

[[ $# -ge 2 ]] || usage
verb="$1"; task="$2"; shift 2
log_file="${LOG_DIR}/${task}.log"

read_start() {
  [[ -f "${log_file}" ]] || die "no start record for task '${task}' — run 'start' first"
  awk -F '\t' '$2 == "start" { print $1; exit }' "${log_file}"
}

mmss() {
  local seconds=$1
  printf '%02d:%02d' $((seconds / 60)) $((seconds % 60))
}

write_event() {
  # write_event verb elapsed payload
  local v=$1 e=$2 p=$3
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date +%s)" "$v" "$e" "$(mmss "$e")" "$p" >> "${log_file}"
}

case "$verb" in
  start)
    : > "${log_file}"
    now=$(date +%s)
    printf '%s\tstart\t0\t00:00\t%s\n' "$now" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
      >> "${log_file}"
    printf '▶ task %s started at %s\n' "$task" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    ;;

  mark)
    [[ $# -ge 1 ]] || usage
    label="$1"
    start=$(read_start)
    elapsed=$(( $(date +%s) - start ))
    write_event mark "$elapsed" "$label"
    printf '  +%s  %s\n' "$(mmss "$elapsed")" "$label"
    ;;

  friction)
    [[ $# -ge 3 ]] || usage
    category="$1"; where="$2"; note="$3"
    case "$category" in D|U|S|P|C) ;; *) die "category must be one of D U S P C" ;; esac
    start=$(read_start)
    elapsed=$(( $(date +%s) - start ))
    # Use ` | ` as field separator inside payload so it survives the outer TSV.
    write_event friction "$elapsed" "${category} | ${where} | ${note}"
    printf '  +%s  ⚠ %s — %s\n' "$(mmss "$elapsed")" "$category" "$where"
    ;;

  report|end)
    [[ -f "${log_file}" ]] || die "no records for task '${task}'"
    printf '── task %s timeline ──\n' "$task"
    awk -F '\t' '
      $2 == "start"    { printf "  +%s  start (%s)\n",    $4, $5 }
      $2 == "mark"     { printf "  +%s  %s\n",            $4, $5 }
      $2 == "friction" { printf "  +%s  ⚠ %s\n",          $4, $5 }
      $2 == "end"      { printf "── total: %s ──\n",      $4 }
    ' "${log_file}"
    if [[ "$verb" = end ]]; then
      start=$(read_start)
      total=$(( $(date +%s) - start ))
      write_event end "$total" "task complete"
      printf '── total: %s ──\n' "$(mmss "$total")"
    fi
    ;;

  *) usage ;;
esac

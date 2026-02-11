#!/usr/bin/env bash
set -u
now(){ date -Iseconds; }
BASE_URL="${BASE_URL:?BASE_URL not set}"
TARGET="${TARGET:-propiese}"
INTERVAL="${INTERVAL:-20}"
LASTFILE="${LASTFILE:?LASTFILE not set}"

LAST=0
if [ -f "$LASTFILE" ]; then
  LAST=$(cat "$LASTFILE" 2>/dev/null || echo 0)
fi
if ! [[ "$LAST" =~ ^[0-9]+$ ]]; then LAST=0; fi

echo "$(now) poller_start pid=$$ ppid=$PPID interval=${INTERVAL}s base=$BASE_URL target=$TARGET last=$LAST"
trap 'echo "$(now) poller_term pid=$$"' TERM INT HUP

ITER=0
while true; do
  NOW="$(now)"
  JSON=$(curl -sS -m 10 "$BASE_URL/api/messages?target=$TARGET&limit=200" || true)
  LATEST=$(printf "%s" "$JSON" | jq -r --argjson d "$LAST" '.messages[-1].id // $d' 2>/dev/null || echo "$LAST")

  if [[ "$LATEST" =~ ^[0-9]+$ ]] && [ "$LATEST" -gt "$LAST" ]; then
    echo "$NOW new_messages"
    printf "%s" "$JSON" | jq -r --argjson last "$LAST" '.messages[] | select(.id > $last) | "#\(.id) [\(.sender)] \(.text|split("\n")[0])"' 2>/dev/null || true
    LAST="$LATEST"
    echo "$LAST" > "$LASTFILE"
  fi

  ITER=$((ITER+1))
  if (( ITER % 3 == 0 )); then
    echo "$NOW heartbeat last=$LAST"
  fi

  sleep "$INTERVAL"
done
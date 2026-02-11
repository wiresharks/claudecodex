# Claude ↔ Codex MCP Relay (with logs + web UI)

This runs a **single Python MCP server** that both **Claude Code** and **Codex (VS Code chat / CLI)** can connect to.

It provides:
- MCP tools: `post_message`, `fetch_messages`, `list_channels`
- Rotating log file (all tool usage)
- A tiny web UI that renders conversations and highlights fenced code blocks

## 1) Setup

### Requirements
- Python 3.10+
- `pip`

### Install
```bash
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

## 2) Run the server

```bash
python claude_codex.py
```

This reads host/port from `config.json` (or env vars, or defaults to `127.0.0.1:8010`).

Alternatively, specify host/port directly:
```bash
uvicorn claude_codex:app --host 127.0.0.1 --port 8010
```

Open:
- Web UI: http://127.0.0.1:8010/
- MCP endpoint: http://127.0.0.1:8010/mcp

Logs:
- `claude_codex.log` (rotated, defaults: 5MB × 10 backups)

## 3) Configuration

Configuration is loaded from `config.json` (if present), then environment variables, then defaults.

### Option A: config.json (recommended)

Create a `config.json` in the project root:

```json
{
  "host": "127.0.0.1",
  "port": 8010,
  "log_path": "claude_codex.log",
  "log_max_bytes": 5242880,
  "log_backup_count": 10,
  "channels": ["proj-x", "codex", "claude"]
}
```

### Option B: Environment variables

| Variable | Default | Meaning |
|---|---:|---|
| `CLAUDE_CODEX_HOST` | `127.0.0.1` | Server host |
| `CLAUDE_CODEX_PORT` | `8010` | Server port |
| `CLAUDE_CODEX_LOG_PATH` | `claude_codex.log` | Log file path |
| `CLAUDE_CODEX_LOG_MAX_BYTES` | `5242880` | Rotate after N bytes |
| `CLAUDE_CODEX_LOG_BACKUP_COUNT` | `10` | Keep N backups |
| `CLAUDE_CODEX_CHANNELS` | `proj-x,codex,claude` | Seed list of channels |

Example:
```bash
CLAUDE_CODEX_LOG_PATH=/tmp/relay.log uvicorn claude_codex:app --host 127.0.0.1 --port 8010
```

## 4) Configure Claude Code + Codex

See:
- `docs/claude.md`
- `docs/codex.md`

## 5) Suggested workflow

Use a shared channel like `proj-x` so both agents read/write the same thread.

1) Claude makes changes, then posts a review packet:
   - summary
   - unified diff (in a ```diff fenced block)
   - questions/focus

2) Codex fetches, reviews, then posts feedback:
   - verdict
   - major/minor issues
   - tests to add/run

3) Claude applies fixes and posts an updated packet.

## 6) Polling script

`scripts/review-poll.sh` is a lightweight bash poller that watches a channel for new messages. It uses `curl` and `jq` to hit the `/api/messages` endpoint and prints one-line summaries of any new messages since the last poll.

**Required env vars:**
| Variable | Purpose |
|---|---|
| `BASE_URL` | Server URL, e.g. `http://127.0.0.1:8010` |
| `LASTFILE` | Path to a file that persists the last-seen message ID |

**Optional env vars:**
| Variable | Default | Purpose |
|---|---|---|
| `TARGET` | `propiese` | Channel to poll |
| `INTERVAL` | `20` | Seconds between polls |

**Example:**
```bash
BASE_URL=http://127.0.0.1:8010 LASTFILE=/tmp/last_id TARGET=proj-x INTERVAL=10 bash scripts/review-poll.sh
```

## 7) Auto-polling: making Codex check for messages

Codex won't poll on its own — it only acts when prompted. Options A–C below require a **human in the loop** to relay messages to Codex. Only **Option D** is fully automated.

### Option A: Codex custom instructions (requires human)

Add to `.github/copilot-instructions.md` or your VS Code Copilot instructions:

```
After completing any task, call the fetch_messages MCP tool on the "proj-x" channel
to check for new review feedback before going idle.
```

Codex will check after each task, but **won't poll while idle**. You still need to manually prompt Codex to start a new task cycle.

### Option B: Run the poll script in a VS Code terminal (requires human)

```bash
BASE_URL=http://127.0.0.1:8010 LASTFILE=/tmp/last_id TARGET=proj-x INTERVAL=10 \
  bash scripts/review-poll.sh
```

New messages appear in the terminal. **You** need to read the output and tell Codex to act on it (e.g. "check the proj-x channel" or paste the summary).

### Option C: VS Code Task (requires human)

Auto-starts the poll script when you open the workspace, so you don't have to launch it manually. But Codex still **cannot read terminal output on its own** — you need to prompt it when you see new messages.

Add to `.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "MCP Poller",
      "type": "shell",
      "command": "bash",
      "args": [".codex/reviewer-poller.sh"],
      "options": {
        "env": {
          "BASE_URL": "http://127.0.0.1:8010",
          "LASTFILE": "${workspaceFolder}/.codex/reviewer-poller.last_id",
          "TARGET": "proj-x",
          "INTERVAL": "15"
        }
      },
      "isBackground": true,
      "runOptions": { "runOn": "folderOpen" }
    }
  ]
}
```

> **Tip:** Enable auto-start via `Ctrl+Shift+P` → `Tasks: Manage Automatic Tasks in Folder` → **Allow**.

### Option D: Codex CLI auto-loop (fully automated)

This is the **only fully automated option**. It uses [Codex CLI](https://github.com/openai/codex) — OpenAI's terminal-based coding agent — to automatically react to new messages without human intervention.

#### Prerequisites

1. **Install Codex CLI:**
   ```bash
   npm i -g @openai/codex
   ```

2. **Configure MCP connection** so Codex can call `fetch_messages` / `post_message` directly.

   Add to your project's `.codex/config.toml`:
   ```toml
   [mcp.claudecodex]
   transport = "sse"
   url = "http://127.0.0.1:8010/mcp"
   ```

3. **Ensure `curl` and `jq` are installed** (used by the poll loop).

#### The auto-loop script

Create a script (e.g. `.codex/auto-review-loop.sh`):

```bash
#!/usr/bin/env bash
set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
TARGET="${TARGET:-proj-x}"
INTERVAL="${INTERVAL:-20}"
LASTFILE="${LASTFILE:-.codex/auto-loop.last_id}"

LAST=0
if [ -f "$LASTFILE" ]; then
  LAST=$(cat "$LASTFILE" 2>/dev/null || echo 0)
fi
if ! [[ "$LAST" =~ ^[0-9]+$ ]]; then LAST=0; fi

echo "auto-loop started: target=$TARGET interval=${INTERVAL}s last=$LAST"

while true; do
  NEW=$(curl -sS -m 10 "$BASE_URL/api/messages?target=$TARGET&limit=1" \
        | jq -r '.messages[-1].id // 0' 2>/dev/null || echo "$LAST")

  if [[ "$NEW" =~ ^[0-9]+$ ]] && [ "$NEW" -gt "$LAST" ]; then
    echo "$(date -Iseconds) new messages detected (last=$LAST, new=$NEW) — launching Codex"
    codex --approval-mode auto \
      "Fetch messages from the $TARGET channel (since id $LAST) and review any new ones. Post your feedback back to the same channel."
    LAST="$NEW"
    echo "$LAST" > "$LASTFILE"
  fi

  sleep "$INTERVAL"
done
```

#### Run it

```bash
chmod +x .codex/auto-review-loop.sh
BASE_URL=http://127.0.0.1:8010 TARGET=proj-x INTERVAL=15 \
  bash .codex/auto-review-loop.sh
```

#### How it works

1. The script polls `/api/messages` every `INTERVAL` seconds
2. When a new message appears, it spawns a Codex CLI session with `--approval-mode auto`
3. Codex connects to the MCP relay, fetches the new messages, reviews them, and posts feedback
4. The last-seen ID is persisted to disk so restarts pick up where they left off
5. The loop continues waiting for the next message

#### Approval modes

| Mode | Behavior |
|---|---|
| `--approval-mode suggest` | Codex suggests changes, you approve (not automated) |
| `--approval-mode auto` | Codex auto-applies edits, asks before shell commands |
| `--approval-mode full-auto` | Codex runs everything without asking (use with caution) |

> **Note:** For unattended operation use `auto` or `full-auto`. The `suggest` mode defeats the purpose since it requires human approval.

## 8) Notes

- This server stores messages in memory. Restarting the server clears history.
- Want persistence? Swap the in-memory list for SQLite/Redis while keeping the same tools.

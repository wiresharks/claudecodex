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
| `WATCH_SENDER` | `claude` | Only show messages from this sender (ignores own messages) |

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

The script uses `codex exec` (non-interactive/headless mode) so that Codex **exits automatically** after completing each review. The regular `codex` and `codex resume` commands open an interactive session that would block the loop.

```bash
#!/usr/bin/env bash
set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
TARGET="${TARGET:-proj-x}"
INTERVAL="${INTERVAL:-20}"
LASTFILE="${LASTFILE:-.codex/auto-loop.last_id}"
SESSION_ID="${SESSION_ID:-}"       # optional: resume an existing Codex session
WATCH_SENDER="${WATCH_SENDER:-claude}"  # only react to messages from this sender

LAST=0
if [ -f "$LASTFILE" ]; then
  LAST=$(cat "$LASTFILE" 2>/dev/null || echo 0)
fi
if ! [[ "$LAST" =~ ^[0-9]+$ ]]; then LAST=0; fi

echo "auto-loop started: target=$TARGET sender=$WATCH_SENDER interval=${INTERVAL}s last=$LAST session=${SESSION_ID:-new}"

while true; do
  JSON=$(curl -sS -m 10 "$BASE_URL/api/messages?target=$TARGET&limit=200" || true)

  # Only look at messages from WATCH_SENDER — ignore own (codex) messages
  NEW=$(printf "%s" "$JSON" | jq -r --argjson d "$LAST" --arg sender "$WATCH_SENDER" \
    '[.messages[] | select(.sender == $sender)] | last | .id // $d' 2>/dev/null || echo "$LAST")

  if [[ "$NEW" =~ ^[0-9]+$ ]] && [ "$NEW" -gt "$LAST" ]; then
    echo "$(date -Iseconds) new messages from $WATCH_SENDER (last=$LAST, new=$NEW) — launching Codex"
    PROMPT="Fetch messages from the $TARGET channel (since id $LAST) sent by $WATCH_SENDER and review them. Post your feedback back to the same channel."
    if [ -n "$SESSION_ID" ]; then
      # Resume existing session — keeps full conversation context across reviews
      codex exec --dangerously-bypass-approvals-and-sandbox resume "$SESSION_ID" "$PROMPT"
    else
      # Start a fresh session
      codex exec --dangerously-bypass-approvals-and-sandbox "$PROMPT"
    fi
    LAST="$NEW"
    echo "$LAST" > "$LASTFILE"
  fi

  sleep "$INTERVAL"
done
```

#### Run it

```bash
chmod +x .codex/auto-review-loop.sh

# Start a new session each time
BASE_URL=http://127.0.0.1:8010 TARGET=proj-x INTERVAL=15 \
  bash .codex/auto-review-loop.sh

# Or resume an existing Codex session (keeps conversation context)
BASE_URL=http://127.0.0.1:8010 TARGET=proj-x SESSION_ID=abc123 \
  bash .codex/auto-review-loop.sh
```

#### How it works

1. The script polls `/api/messages` every `INTERVAL` seconds
2. When a new message appears, it runs `codex exec` (non-interactive mode) which processes the task and **exits automatically**
3. If `SESSION_ID` is set, it uses `codex exec resume <SESSION_ID>` to continue an existing session — Codex keeps the full conversation history and builds on previous reviews
4. Codex connects to the MCP relay, fetches the new messages, reviews them, and posts feedback
5. The last-seen ID is persisted to disk so restarts pick up where they left off
6. The loop continues waiting for the next message

> **Why `codex exec` instead of `codex`?** The regular `codex` command opens an interactive terminal UI that waits for user input and never exits. `codex exec` is the headless/non-interactive mode designed for scripts and automation — it runs the task and exits when done.

#### Codex CLI session management

Each Codex CLI chat gets a **session ID** stored under `~/.codex/sessions/`. You can use these to maintain continuity across reviews.

| Command | Purpose |
|---|---|
| `/status` | Show current session ID (inside a running session) |
| `codex resume` | Pick a session to resume from a list |
| `codex resume <SESSION_ID>` | Resume a specific session by ID |
| `codex fork` | Fork a session into a new thread |
| `codex fork --last` | Fork the most recent session |

You can also type `/resume` inside the CLI to get an interactive session picker.

**Tip:** After the first Codex run, grab the session ID from `/status` or `~/.codex/sessions/` and pass it as `SESSION_ID` to the auto-loop. This way every review iteration builds on the same conversation thread, giving Codex full context of previous feedback.

#### Approval and sandbox modes

| Flag | Approvals | File writes | Network |
|---|---|---|---|
| *(default)* | Asks before commands/writes | Workspace only | Blocked |
| `--full-auto` | None | Workspace only | Blocked |
| `--full-auto` + network config | None | Workspace only | Allowed |
| `--sandbox danger-full-access` | Asks before commands | Anywhere | Allowed |
| `--dangerously-bypass-approvals-and-sandbox` | None | Anywhere | Allowed |

> **Important:** `--full-auto` blocks network access by default. Since Codex needs to reach the MCP relay, the auto-loop script above will fail unless network access is enabled.

#### Enabling network access

**Option 1:** Enable network in config (recommended). Add to `.codex/config.toml`:

```toml
[sandbox_workspace_write]
network_access = true
```

Then `--full-auto` works as-is.

**Option 2:** Pass sandbox config inline:

```bash
codex exec --full-auto -c 'sandbox_workspace_write.network_access=true' "$PROMPT"
```

**Option 3:** Bypass the sandbox entirely (use with caution):

```bash
codex exec --dangerously-bypass-approvals-and-sandbox "$PROMPT"
```

#### Full-auto script with network access

This variant uses `--dangerously-bypass-approvals-and-sandbox` to ensure Codex can reach the MCP relay without any restrictions:

```bash
#!/usr/bin/env bash
set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
TARGET="${TARGET:-proj-x}"
INTERVAL="${INTERVAL:-20}"
LASTFILE="${LASTFILE:-.codex/auto-loop.last_id}"
SESSION_ID="${SESSION_ID:-}"
WATCH_SENDER="${WATCH_SENDER:-claude}"  # only react to messages from this sender

LAST=0
if [ -f "$LASTFILE" ]; then
  LAST=$(cat "$LASTFILE" 2>/dev/null || echo 0)
fi
if ! [[ "$LAST" =~ ^[0-9]+$ ]]; then LAST=0; fi

echo "auto-loop started: target=$TARGET sender=$WATCH_SENDER interval=${INTERVAL}s last=$LAST session=${SESSION_ID:-new}"

while true; do
  JSON=$(curl -sS -m 10 "$BASE_URL/api/messages?target=$TARGET&limit=200" || true)

  # Only look at messages from WATCH_SENDER — ignore own (codex) messages
  NEW=$(printf "%s" "$JSON" | jq -r --argjson d "$LAST" --arg sender "$WATCH_SENDER" \
    '[.messages[] | select(.sender == $sender)] | last | .id // $d' 2>/dev/null || echo "$LAST")

  if [[ "$NEW" =~ ^[0-9]+$ ]] && [ "$NEW" -gt "$LAST" ]; then
    echo "$(date -Iseconds) new messages from $WATCH_SENDER (last=$LAST, new=$NEW) — launching Codex"
    PROMPT="Fetch messages from the $TARGET channel (since id $LAST) sent by $WATCH_SENDER and review them. Post your feedback back to the same channel."
    if [ -n "$SESSION_ID" ]; then
      codex exec --dangerously-bypass-approvals-and-sandbox resume "$SESSION_ID" "$PROMPT"
    else
      codex exec --dangerously-bypass-approvals-and-sandbox "$PROMPT"
    fi
    LAST="$NEW"
    echo "$LAST" > "$LASTFILE"
  fi

  sleep "$INTERVAL"
done
```

> **Safer alternative:** If you prefer to keep the workspace sandbox, add `network_access = true` to your `.codex/config.toml` (see Option 1 above) and use `--full-auto` instead.

## 8) Notes

- This server stores messages in memory. Restarting the server clears history.
- Want persistence? Swap the in-memory list for SQLite/Redis while keeping the same tools.

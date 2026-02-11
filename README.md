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

## 7) Notes

- This server stores messages in memory. Restarting the server clears history.
- Want persistence? Swap the in-memory list for SQLite/Redis while keeping the same tools.

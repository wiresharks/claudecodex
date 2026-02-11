from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from mcp.server.fastmcp import FastMCP

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
HOST = os.environ.get("CLAUDE_CODEX_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_CODEX_PORT", "8010"))
MCP_PATH = os.environ.get("CLAUDE_CODEX_MCP_PATH", "/mcp")  # MCP endpoint base path
LOG_PATH = os.environ.get("CLAUDE_CODEX_LOG_PATH", "claude_codex.log")

LOG_MAX_BYTES = int(os.environ.get("CLAUDE_CODEX_LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5MB
LOG_BACKUP_COUNT = int(os.environ.get("CLAUDE_CODEX_LOG_BACKUP_COUNT", "10"))

DEFAULT_CHANNELS = os.environ.get("CLAUDE_CODEX_CHANNELS", "proj-x,codex,claude").split(",")

# -----------------------------------------------------------------------------
# Logging (rotating)
# -----------------------------------------------------------------------------
logger = logging.getLogger("claude_codex")
logger.setLevel(logging.INFO)

_handler = RotatingFileHandler(
    LOG_PATH,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

# -----------------------------------------------------------------------------
# Message store (in-memory)
# -----------------------------------------------------------------------------
_messages: list[dict[str, Any]] = []
_next_id = 1
_lock = asyncio.Lock()


def _append_message(target: str, sender: str, text: str) -> dict[str, Any]:
    global _next_id
    msg = {
        "id": _next_id,
        "ts": time.time(),
        "target": target,
        "sender": sender,
        "text": text,
    }
    _next_id += 1
    _messages.append(msg)
    return msg


# -----------------------------------------------------------------------------
# MCP server (Streamable HTTP)
# -----------------------------------------------------------------------------
mcp = FastMCP("claude-codex-relay", json_response=True)


@mcp.tool()
async def post_message(target: str, sender: str, text: str) -> dict[str, Any]:
    """
    Post a message into a target inbox (channel).
    Typical targets: "codex", "claude", or a shared channel like "proj-x".
    """
    async with _lock:
        msg = _append_message(target=target, sender=sender, text=text)

    logger.info(
        "post_message %s",
        json.dumps(
            {"id": msg["id"], "target": target, "sender": sender, "text_len": len(text)},
            ensure_ascii=False,
        ),
    )
    return {"ok": True, "posted": msg["id"]}


@mcp.tool()
async def fetch_messages(target: str, since_id: int = 0, limit: int = 50) -> dict[str, Any]:
    """
    Fetch messages for a target with id > since_id.
    """
    limit = max(1, min(int(limit), 200))
    since_id = int(since_id)

    async with _lock:
        out = [m for m in _messages if m["target"] == target and m["id"] > since_id]
        out = out[:limit]
        latest = out[-1]["id"] if out else since_id

    logger.info(
        "fetch_messages %s",
        json.dumps(
            {"target": target, "since_id": since_id, "limit": limit, "returned": len(out), "latest_id": latest},
            ensure_ascii=False,
        ),
    )
    return {"messages": out, "latest_id": latest}


@mcp.tool()
async def list_channels() -> dict[str, Any]:
    """
    List known channels (targets) based on env CLAUDE_CODEX_CHANNELS and observed traffic.
    """
    async with _lock:
        observed = sorted({m["target"] for m in _messages})
    chans = sorted(set([c.strip() for c in DEFAULT_CHANNELS if c.strip()] + observed))
    return {"channels": chans}


# -----------------------------------------------------------------------------
# Web UI
# -----------------------------------------------------------------------------
HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MCP Relay Conversation</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    header { display:flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    h1 { font-size: 18px; margin: 0; }
    .muted { color: #666; font-size: 13px; }
    .row { display:flex; gap: 10px; align-items:center; margin: 14px 0; flex-wrap: wrap; }
    input, select { padding: 6px 8px; font-size: 14px; }
    button { padding: 6px 10px; font-size: 14px; cursor: pointer; }
    .msg { border: 1px solid #e5e5e5; border-radius: 10px; padding: 12px; margin: 10px 0; }
    .meta { display:flex; gap: 10px; align-items:center; margin-bottom: 8px; color:#444; font-size: 12px; flex-wrap: wrap; }
    .badge { padding: 2px 8px; border-radius: 999px; background: #f2f2f2; }
    pre { overflow:auto; padding: 10px; border-radius: 8px; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .text { white-space: pre-wrap; }
    .footer { margin-top: 20px; font-size: 12px; color: #666; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  </style>
  <link rel="stylesheet"
        href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/default.min.css">
</head>
<body>
  <header>
    <h1>MCP Relay</h1>
    <div class="muted">Live view of messages posted through MCP tools.</div>
  </header>

  <div class="row">
    <label>Channel:</label>
    <select id="channel"></select>
    <button id="reload">Reload</button>
    <span class="muted">Use a shared channel like <span class="mono">proj-x</span> for one combined thread.</span>
  </div>

  <div id="list"></div>

  <div class="footer">
    MCP endpoint: <span class="mono" id="mcpEndpoint"></span> •
    API: <span class="mono">/api/messages</span>
  </div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <script>
    const listEl = document.getElementById("list");
    const chanEl = document.getElementById("channel");
    const reloadBtn = document.getElementById("reload");
    const mcpEndpointEl = document.getElementById("mcpEndpoint");

    function escapeHtml(s) {
      return (s ?? "").toString()
        .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
        .replaceAll('"',"&quot;").replaceAll("'","&#039;");
    }

    // Supports fenced code blocks ```lang ... ```
    function renderText(text) {
      const t = text || "";
      const parts = t.split(/```/g);
      if (parts.length === 1) {
        return `<div class="text">${escapeHtml(t)}</div>`;
      }
      let html = "";
      for (let i = 0; i < parts.length; i++) {
        const chunk = parts[i];
        if (i % 2 === 0) {
          if (chunk) html += `<div class="text">${escapeHtml(chunk)}</div>`;
        } else {
          const firstNewline = chunk.indexOf("\n");
          let lang = "";
          let code = chunk;
          if (firstNewline !== -1) {
            lang = chunk.slice(0, firstNewline).trim();
            code = chunk.slice(firstNewline + 1);
          }
          const langClass = lang ? `language-${escapeHtml(lang)}` : "";
          html += `<pre><code class="${langClass}">${escapeHtml(code)}</code></pre>`;
        }
      }
      return html;
    }

    async function loadChannels() {
      const res = await fetch(`/api/channels`);
      const data = await res.json();
      const chans = data.channels || ["proj-x","codex","claude"];
      chanEl.innerHTML = chans.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
      if (!chanEl.value) chanEl.value = chans[0] || "proj-x";
    }

    async function load() {
      const target = chanEl.value || "proj-x";
      const res = await fetch(`/api/messages?target=${encodeURIComponent(target)}&limit=200`);
      const data = await res.json();

      const items = data.messages || [];
      listEl.innerHTML = items.map(m => {
        const ts = new Date((m.ts || 0) * 1000).toLocaleString();
        return `
          <div class="msg">
            <div class="meta">
              <span class="badge">#${m.id}</span>
              <span class="badge">to: ${escapeHtml(m.target)}</span>
              <span class="badge">from: ${escapeHtml(m.sender)}</span>
              <span class="muted">${escapeHtml(ts)}</span>
            </div>
            ${renderText(m.text)}
          </div>
        `;
      }).join("");

      document.querySelectorAll("pre code").forEach(el => hljs.highlightElement(el));
      mcpEndpointEl.textContent = location.origin + "%(mcp_path)s";
    }

    reloadBtn.addEventListener("click", load);

    (async () => {
      await loadChannels();
      await load();
      setInterval(load, 1200);
    })();
  </script>
</body>
</html>
"""

async def homepage(request):
    return HTMLResponse(HTML_PAGE % {"mcp_path": MCP_PATH})


async def api_messages(request):
    target = request.query_params.get("target", "proj-x")
    limit = int(request.query_params.get("limit", "200"))
    limit = max(1, min(limit, 500))

    async with _lock:
        msgs = [m for m in _messages if m["target"] == target]
        msgs = msgs[-limit:]

    return JSONResponse({"target": target, "messages": msgs})


async def api_channels(request):
    async with _lock:
        observed = sorted({m["target"] for m in _messages})
    chans = sorted(set([c.strip() for c in DEFAULT_CHANNELS if c.strip()] + observed))
    return JSONResponse({"channels": chans})


async def healthz(request):
    return PlainTextResponse("ok")


# -----------------------------------------------------------------------------
# Starlette app with MCP mounted
# -----------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Required when mounting MCP into another ASGI app; ensures MCP background tasks run.
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/", homepage, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/api/messages", api_messages, methods=["GET"]),
        Route("/api/channels", api_channels, methods=["GET"]),
        Mount(MCP_PATH, app=mcp.streamable_http_app(), name="mcp"),
    ],
    lifespan=lifespan,
)

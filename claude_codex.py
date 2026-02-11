from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from mcp.server.fastmcp import FastMCP

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
_BASE_DIR = Path(__file__).parent
_CONFIG_PATH = _BASE_DIR / "config.json"

# Load config.json if available
_file_config: dict[str, Any] = {}
if _CONFIG_PATH.exists():
    try:
        _file_config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass


def _get_config(key: str, default: Any = None) -> Any:
    """Get config value from config.json, then env var, then default."""
    if key in _file_config:
        return _file_config[key]
    env_key = f"CLAUDE_CODEX_{key.upper()}"
    return os.environ.get(env_key, default)


HOST = _get_config("host", "127.0.0.1")
PORT = int(_get_config("port", 8010))
MCP_PATH = _get_config("mcp_path", "/mcp")
LOG_PATH = _get_config("log_path", "claude_codex.log")
LOG_MAX_BYTES = int(_get_config("log_max_bytes", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = int(_get_config("log_backup_count", 10))

_channels_raw = _get_config("channels", "proj-x,codex,claude")
DEFAULT_CHANNELS = _channels_raw if isinstance(_channels_raw, list) else _channels_raw.split(",")

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
_index_html_cache: str | None = None


def _load_index_html() -> str:
    global _index_html_cache
    if _index_html_cache is None:
        _index_html_cache = (_BASE_DIR / "index.html").read_text(encoding="utf-8")
    return _index_html_cache


async def homepage(request):
    html = _load_index_html().replace("{{MCP_PATH}}", MCP_PATH)
    return HTMLResponse(html)


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

# Paths to exclude from uvicorn access logs (polling endpoints)
_QUIET_PATHS = {"/", "/api/messages", "/api/channels", "/healthz"}


class QuietAccessLogMiddleware:
    """Suppress uvicorn access logs for high-frequency polling endpoints."""

    def __init__(self, app):
        self.app = app
        self._uvicorn_access = logging.getLogger("uvicorn.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in _QUIET_PATHS:
            # Temporarily disable uvicorn access logging
            original_level = self._uvicorn_access.level
            self._uvicorn_access.setLevel(logging.WARNING)
            try:
                await self.app(scope, receive, send)
            finally:
                self._uvicorn_access.setLevel(original_level)
        else:
            await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Required when mounting MCP into another ASGI app; ensures MCP background tasks run.
    async with mcp.session_manager.run():
        yield


_app = Starlette(
    routes=[
        Route("/", homepage, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/api/messages", api_messages, methods=["GET"]),
        Route("/api/channels", api_channels, methods=["GET"]),
        Mount(MCP_PATH, app=mcp.streamable_http_app(), name="mcp"),
    ],
    lifespan=lifespan,
)

app = QuietAccessLogMiddleware(_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("claude_codex:app", host=HOST, port=PORT, reload=False)

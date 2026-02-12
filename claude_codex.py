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
from mcp.server.transport_security import TransportSecuritySettings

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


def _as_positive_int(value: Any, key_name: str) -> int:
    out = int(value)
    if out <= 0:
        raise ValueError(f"{key_name} must be > 0")
    return out


def _get_rotating_max_bytes(max_mb_key: str, legacy_max_bytes_key: str, default_mb: int = 25) -> int:
    max_mb = _get_config(max_mb_key, None)
    if max_mb is not None:
        return _as_positive_int(max_mb, max_mb_key) * 1024 * 1024

    legacy_bytes = _get_config(legacy_max_bytes_key, None)
    if legacy_bytes is not None:
        return _as_positive_int(legacy_bytes, legacy_max_bytes_key)

    return default_mb * 1024 * 1024


HOST = _get_config("host", "127.0.0.1")
PORT = int(_get_config("port", 8010))
_DEFAULT_LOG_DIR = _BASE_DIR / "log"
LOG_PATH = str(_get_config("log_path", str(_DEFAULT_LOG_DIR / "claude_codex.log")))
LOG_MAX_BYTES = _get_rotating_max_bytes("log_max_mb", "log_max_bytes", default_mb=25)
LOG_BACKUP_COUNT = int(_get_config("log_backup_count", 10))
CHANNEL_LOG_PATH = str(_get_config("channel_log_path", str(_DEFAULT_LOG_DIR / "channel_messages.log")))
CHANNEL_LOG_MAX_BYTES = _get_rotating_max_bytes("channel_log_max_mb", "channel_log_max_bytes", default_mb=25)
CHANNEL_LOG_BACKUP_COUNT = int(_get_config("channel_log_backup_count", 10))

_channels_raw = _get_config("channels", "proj-x,codex,claude")
DEFAULT_CHANNELS = _channels_raw if isinstance(_channels_raw, list) else _channels_raw.split(",")

_allowed_hosts_raw = _get_config("allowed_hosts", "")
_ALLOWED_HOSTS: list[str] = (
    _allowed_hosts_raw if isinstance(_allowed_hosts_raw, list)
    else [h.strip() for h in _allowed_hosts_raw.split(",") if h.strip()]
) if _allowed_hosts_raw else []

# -----------------------------------------------------------------------------
# Logging (rotating)
# -----------------------------------------------------------------------------
def _has_rotating_handler(logger_obj: logging.Logger, file_path: str) -> bool:
    target = Path(file_path).resolve()
    for existing in logger_obj.handlers:
        if isinstance(existing, RotatingFileHandler):
            try:
                if Path(existing.baseFilename).resolve() == target:
                    return True
            except OSError:
                continue
    return False


def _ensure_parent_dir(file_path: str) -> None:
    Path(file_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


logger = logging.getLogger("claude_codex")
logger.setLevel(logging.INFO)

_ensure_parent_dir(LOG_PATH)
_handler = RotatingFileHandler(
    LOG_PATH,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
if not _has_rotating_handler(logger, LOG_PATH):
    logger.addHandler(_handler)

channel_logger = logging.getLogger("claude_codex.channel_messages")
channel_logger.setLevel(logging.INFO)
channel_logger.propagate = False

_ensure_parent_dir(CHANNEL_LOG_PATH)
_channel_handler = RotatingFileHandler(
    CHANNEL_LOG_PATH,
    maxBytes=CHANNEL_LOG_MAX_BYTES,
    backupCount=CHANNEL_LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_channel_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
if not _has_rotating_handler(channel_logger, CHANNEL_LOG_PATH):
    channel_logger.addHandler(_channel_handler)

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


def _log_channel_message(msg: dict[str, Any], source: str) -> None:
    channel_logger.info(
        json.dumps(
            {
                "id": msg["id"],
                "ts": msg["ts"],
                "target": msg["target"],
                "sender": msg["sender"],
                "text": msg["text"],
                "source": source,
            },
            ensure_ascii=False,
        )
    )


async def _store_message(target: str, sender: str, text: str, source: str) -> dict[str, Any]:
    target = (target or "").strip()
    sender = (sender or "").strip()
    text = "" if text is None else str(text)

    if not target:
        raise ValueError("target is required")
    if not sender:
        raise ValueError("sender is required")
    if not text.strip():
        raise ValueError("text is required")

    async with _lock:
        msg = _append_message(target=target, sender=sender, text=text)

    logger.info(
        "post_message %s",
        json.dumps(
            {
                "id": msg["id"],
                "target": target,
                "sender": sender,
                "text_len": len(text),
                "source": source,
            },
            ensure_ascii=False,
        ),
    )
    _log_channel_message(msg, source=source)
    return msg


# -----------------------------------------------------------------------------
# MCP server (Streamable HTTP)
# -----------------------------------------------------------------------------
_default_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_default_hosts + [h if ":" in h else f"{h}:*" for h in _ALLOWED_HOSTS],
)

mcp = FastMCP("claude-codex-relay", json_response=True, transport_security=_transport_security)


@mcp.tool()
async def post_message(target: str, sender: str, text: str) -> dict[str, Any]:
    """
    Post a message into a target inbox (channel).
    Typical targets: "codex", "claude", or a shared channel like "proj-x".
    """
    msg = await _store_message(target=target, sender=sender, text=text, source="mcp")
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
    return HTMLResponse(_load_index_html())


async def api_messages(request):
    target = request.query_params.get("target", "proj-x")
    limit = int(request.query_params.get("limit", "200"))
    limit = max(1, min(limit, 500))

    async with _lock:
        msgs = [m for m in _messages if m["target"] == target]
        msgs = msgs[-limit:]

    return JSONResponse({"target": target, "messages": msgs})


async def api_post_message(request):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "Request body must be a JSON object"}, status_code=400)

    target = payload.get("target", "")
    sender = payload.get("sender", "")
    text = payload.get("text", "")

    try:
        msg = await _store_message(target=target, sender=sender, text=text, source="web-ui")
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse({"ok": True, "posted": msg["id"], "message": msg}, status_code=201)


async def api_channels(request):
    async with _lock:
        observed = sorted({m["target"] for m in _messages})
    chans = sorted(set([c.strip() for c in DEFAULT_CHANNELS if c.strip()] + observed))
    return JSONResponse({"channels": chans})


async def healthz(request):
    return PlainTextResponse("ok")


# -----------------------------------------------------------------------------
# OpenAPI / Discovery
# -----------------------------------------------------------------------------
OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Claude-Codex MCP Relay",
        "version": "1.0.0",
        "description": "MCP relay server for Claude Code and Codex communication",
    },
    "servers": [{"url": "/"}],
    "paths": {
        "/": {"get": {"summary": "Web UI", "responses": {"200": {"description": "HTML page"}}}},
        "/healthz": {"get": {"summary": "Health check", "responses": {"200": {"description": "ok"}}}},
        "/api/messages": {
            "get": {
                "summary": "Fetch messages for a channel",
                "parameters": [
                    {"name": "target", "in": "query", "schema": {"type": "string", "default": "proj-x"}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 200}},
                ],
                "responses": {"200": {"description": "Messages list"}},
            },
            "post": {
                "summary": "Post a message to a channel",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["target", "sender", "text"],
                                "properties": {
                                    "target": {"type": "string"},
                                    "sender": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}, "400": {"description": "Validation error"}},
            }
        },
        "/api/channels": {"get": {"summary": "List channels", "responses": {"200": {"description": "Channels list"}}}},
        "/mcp": {
            "post": {
                "summary": "MCP endpoint (Streamable HTTP)",
                "description": "MCP tools: post_message, fetch_messages, list_channels. Requires MCP session handshake (initialize, notifications/initialized).",
                "responses": {"200": {"description": "MCP response"}},
            }
        },
    },
}


async def openapi_json(request):
    return JSONResponse(OPENAPI_SPEC)


async def docs_page(request):
    html = """<!doctype html>
<html><head><title>API Docs</title></head><body>
<h1>Claude-Codex MCP Relay</h1>
<h2>MCP Endpoint</h2>
<p><code>/mcp</code> (Streamable HTTP, requires session handshake)</p>
<h2>MCP Tools</h2>
<ul>
  <li><code>post_message(target, sender, text)</code> - Post message to a channel</li>
  <li><code>fetch_messages(target, since_id?, limit?)</code> - Fetch messages from a channel</li>
  <li><code>list_channels()</code> - List available channels</li>
</ul>
<h2>HTTP API</h2>
<ul>
  <li><code>GET /api/messages?target=...&limit=...</code></li>
  <li><code>POST /api/messages</code> JSON body: <code>{"target","sender","text"}</code></li>
  <li><code>GET /api/channels</code></li>
  <li><code>GET /healthz</code></li>
</ul>
<p><a href="/openapi.json">OpenAPI spec</a></p>
</body></html>"""
    return HTMLResponse(html)


# -----------------------------------------------------------------------------
# Starlette app with MCP mounted
# -----------------------------------------------------------------------------

# Paths to exclude from uvicorn access logs (polling/discovery endpoints)
_QUIET_PATHS = {"/", "/api/messages", "/api/channels", "/healthz", "/docs", "/openapi.json"}


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
        Route("/docs", docs_page, methods=["GET"]),
        Route("/openapi.json", openapi_json, methods=["GET"]),
        Route("/api/messages", api_messages, methods=["GET"]),
        Route("/api/messages", api_post_message, methods=["POST"]),
        Route("/api/channels", api_channels, methods=["GET"]),
        Mount("", app=mcp.streamable_http_app(), name="mcp"),  # MCP endpoint at /mcp
    ],
    lifespan=lifespan,
)

app = QuietAccessLogMiddleware(_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("claude_codex:app", host=HOST, port=PORT, reload=False)

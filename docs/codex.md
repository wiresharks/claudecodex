# Configure Codex (reviewer)

Codex can connect to MCP servers via `~/.codex/config.toml` (shared between CLI and VS Code extension).

## Option A) Edit `~/.codex/config.toml`

```toml
[mcp_servers.claudecodex]
url = "http://127.0.0.1:8010/mcp"
```

Restart VS Code (or reload the Codex chat) so it picks up config changes.

## Option B) Use Codex CLI

```bash
codex mcp add relay --url http://127.0.0.1:8010/mcp
codex mcp list
```

## Reviewer prompt template (copy/paste)

> You are the reviewer agent in a two-agent workflow.Repeatedly:
>
> 1) Call `fetch_messages(target="proj-x")` on the relay MCP server.
> 2) When you receive a new review packet (summary + diff), review it for correctness, security, test gaps, and maintainability.
> 3) Post feedback back to the same channel using `post_message(target="proj-x", sender="codex", text=...)`.
>
> Your feedback must include: Verdict, Major issues, Minor suggestions, Tests, Questions.

### Example feedback structure

````text
Verdict: request changes

Major:
- ...

Minor:
- ...

Tests:
- ...

```diff
# optional suggested patch
```
````

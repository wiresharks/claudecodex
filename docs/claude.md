# Configure Claude Code (review packet publisher)

## 1) Add the MCP server

```bash
claude mcp add --transport http relay http://127.0.0.1:8010/mcp
```

If you want it globally (user scope), some Claude Code versions support:

```bash
claude mcp add --transport http --scope user relay http://127.0.0.1:8010/mcp
```

## 2) Prompt template for Claude (copy/paste)

> You are in a two-agent workflow with Codex over an MCP relay server.  
> After you make code changes, you MUST:  
> 1) Create a concise summary (what/why/risks/tests-run).  
> 2) Produce a unified diff of the changes (```diff fenced block).  
> 3) Post a single message using `post_message(target="proj-x", sender="claude", text=...)` that includes Summary + Diff + Questions.  
> 4) Then poll for feedback by calling `fetch_messages(target="proj-x")` and apply requested changes.

### Suggested review packet format

````text
Summary:
- ...

Tests:
- ...

Questions for review:
- ...

```diff
diff --git a/... b/...
...
```
````

## 3) Minimal tool calls

- Send: `post_message(target="proj-x", sender="claude", text="...")`
- Receive: `fetch_messages(target="proj-x", since_id=0)`

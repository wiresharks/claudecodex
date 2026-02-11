# Configure Claude Code (review packet publisher)

## 1) Add the MCP server

```bash
claude mcp add --transport http relay http://127.0.0.1:8010/mcp
```

If you want it globally (user scope), some Claude Code versions support:

```bash
claude mcp add --transport http --scope user relay http://127.0.0.1:8010/mcp
```

## 2) Automate review packet posting

Choose one of the following methods to ensure Claude posts review packets after completing work.

### Option A: CLAUDE.md (Recommended)

Add the following to your project's `CLAUDE.md` file. Claude Code reads this automatically at session start:

```markdown
## MCP Relay Workflow

After completing code changes, post a review packet to the MCP relay:

1. Run `git diff` to capture changes
2. Call `post_message(target="proj-x", sender="claude", text=...)` with:
   - Summary (what/why/risks)
   - Tests run
   - Questions for review
   - The diff in a ```diff fenced block
3. Poll `fetch_messages(target="proj-x")` for feedback and apply requested changes
```

### Option B: Hook (automatic reminder)

Create `.claude/hooks.json` in your project to trigger a reminder after commits:

```json
{
  "hooks": {
    "post-tool-call": {
      "match": { "tool": "Bash", "command": "git commit" },
      "run": "echo 'Remember to post review packet to MCP relay'"
    }
  }
}
```

### Option C: Custom slash command

Create `.claude/commands/review-packet.md`:

```markdown
Generate a review packet and post it to the MCP relay:
1. Summarize recent changes
2. Include a unified diff
3. Call post_message(target="proj-x", sender="claude", text=<packet>)
```

Then invoke with `/review-packet` when ready to post.

## 3) Review packet format

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

## 4) Minimal tool calls

- Send: `post_message(target="proj-x", sender="claude", text="...")`
- Receive: `fetch_messages(target="proj-x", since_id=0)`

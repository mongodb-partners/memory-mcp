# Memory-MCP: Agent Instructions

Copy the relevant section below into your agent's instruction file (`CLAUDE.md`, `GEMINI.md`, `.cursorrules`, `copilot-instructions.md`, etc.) to give the coding agent persistent memory across conversations.

---

## How Memory-MCP Works

Memory-MCP is a MongoDB-backed MCP server that gives AI agents persistent memory. It stores conversation messages as short-term memories (STM, 24-hour TTL) and automatically promotes important ones to long-term memories (LTM). A background enrichment worker scores importance, generates summaries, and merges similar memories.

All data is scoped by `user_id` for multi-tenant isolation. When auth is enabled, the `user_id` is derived from the authenticated token; when auth is disabled, the agent must pass `user_id` explicitly on every tool call.

---

## When to Use Each Tool

Memory-MCP exposes **12 tools** organized into five categories. The agent should know which tool to use for which situation.

### Memory lifecycle

| Situation | Tool | Why |
|-----------|------|-----|
| Store what the user said or you responded | `store_memory` | Builds the memory corpus; STM auto-expires, important content auto-promotes to LTM |
| Recall context from past conversations | `recall_memory` | Semantic vector search ranked by recency + importance + relevance |
| Find memories using both meaning and keywords | `hybrid_search` | Combined vector + full-text search; better for multi-faceted queries |
| Remove outdated or incorrect memories | `delete_memory` | Soft-delete by ID, tags, or time range; use `dry_run` to preview |

### Decisions and preferences

| Situation | Tool | Why |
|-----------|------|-----|
| Store a user preference or choice | `store_decision` | Keyed key-value pairs that persist across sessions with optional TTL |
| Retrieve a previously stored preference | `recall_decision` | Hydrate agent state at the start of a conversation |

### Cache

| Situation | Tool | Why |
|-----------|------|-----|
| Check if a similar question was already answered | `check_cache` | Avoid re-computing expensive responses; returns cached answer if similarity >= threshold |
| Cache a response for future reuse | `store_cache` | Save query-response pairs; auto-expires after `CACHE_TTL_SECONDS` |
| Clear stale cached responses | `cache_invalidate` | Purge all or pattern-matched cache entries when answers change |

### Admin and diagnostics

| Situation | Tool | Why |
|-----------|------|-----|
| Check memory system health | `memory_health` | Returns tier counts, enrichment queue depth, total memories |
| Permanently delete all user data | `wipe_user_data` | GDPR/account deletion; requires `confirm=true`; irreversible |

### Web search

| Situation | Tool | Why |
|-----------|------|-----|
| Look up current information not in memory | `search_web` | Tavily API web search; requires `TAVILY_API_KEY` configured on the server |

---

## Recommended Workflows

### Start of a new conversation

```
1. recall_decision(key="user_preferences") → hydrate sticky preferences
2. recall_memory(query="<topic of conversation>") → load relevant past context
3. Proceed with the task using recalled context
```

### During a conversation

```
1. After each exchange → store_memory(messages=[...]) with the human + AI messages
2. When the user asks something you've answered before → check_cache(query=...)
3. After computing an expensive answer → store_cache(query=..., response=...)
4. When the user states a preference → store_decision(key=..., value=...)
```

### End of a conversation

```
1. store_memory with any final messages not yet stored
2. store_decision for any new preferences or choices made during the session
```

### Searching for past context

```
Need to find past context?
  ├─ Specific factual recall → recall_memory(query="...")
  ├─ Broad topic search → hybrid_search(query="...")
  ├─ Filter by category → recall_memory(query="...", tags=["project-x"])
  ├─ Filter by time → recall_memory(query="...", tier=["ltm"])
  └─ Check for cached answer → check_cache(query="...")
```

### Memory maintenance

```
1. memory_health(user_id) → check memory counts and enrichment queue
2. delete_memory(tags=["outdated"], dry_run=true) → preview bulk deletion
3. delete_memory(tags=["outdated"], confirm=true) → execute bulk deletion
4. cache_invalidate(invalidate_all=true) → clear all stale cache
```

---

## Available Tools (12 total)

### Memory Tools

| Tool | Description |
|------|-------------|
| `store_memory` | Store conversation messages as short-term memories; human messages >30 chars auto-create LTM candidates |
| `recall_memory` | Semantic search over memories ranked by recency, importance, and relevance |
| `delete_memory` | Soft-delete memories by ID, tags, or time range; supports dry-run preview |

### Cache Tools

| Tool | Description |
|------|-------------|
| `check_cache` | Check semantic cache for a previously cached response to a similar query |
| `store_cache` | Cache a query-response pair for future similarity lookups |

### Search Tools

| Tool | Description |
|------|-------------|
| `hybrid_search` | Combined vector + full-text search with RRF ranking |
| `search_web` | Web search via Tavily API (requires `TAVILY_API_KEY`) |

### Admin Tools

| Tool | Description |
|------|-------------|
| `memory_health` | Health statistics: tier counts, enrichment queue depth, total memories |
| `wipe_user_data` | Permanently delete all user data (requires `confirm=true`) |
| `cache_invalidate` | Invalidate cache entries by pattern or all |

### Decision Tools

| Tool | Description |
|------|-------------|
| `store_decision` | Store a keyed decision/preference with optional TTL |
| `recall_decision` | Recall a previously stored decision by key |

---

## Important Parameters

- **`user_id`**: Required on every tool call. This scopes all data to the user. When auth is enabled, this is resolved from the token. When auth is disabled, the agent must supply it consistently.
- **`conversation_id`**: Required on `store_memory`. Group messages from the same conversation together so they can be recalled as a unit.
- **`tier`**: Optional filter on `recall_memory` and `hybrid_search`. Use `["ltm"]` for long-term memories only, `["stm"]` for recent short-term only, or omit for both.
- **`tags`**: Optional filter. All specified tags must match (AND logic). Useful for categorizing memories by project, topic, or domain.
- **`dry_run`**: On `delete_memory`, preview how many memories would be deleted without actually deleting.
- **`confirm`**: Required for bulk `delete_memory` (by tags or time range) and for `wipe_user_data`. Prevents accidental mass deletion.
- **`ttl_days`**: On `store_decision`, sets expiration in days. Omit for no expiration.

---

## Per-Agent Templates

### CLAUDE.md

```markdown
# Memory-MCP: Persistent Memory

This workspace is connected to a memory-mcp server that gives you persistent
memory across conversations. Use memory-mcp tools to store and recall context.

## When to Use Memory Tools

- **Start of conversation**: recall_decision + recall_memory to load past context
- **During conversation**: store_memory after exchanges, check_cache before expensive work
- **User states a preference**: store_decision(key="preference_name", value="...")
- **End of conversation**: store_memory with final messages

## Tool Quick Reference

| Need | Tool |
|------|------|
| Store messages | store_memory |
| Recall past context | recall_memory |
| Broad topic search | hybrid_search |
| Check cached answer | check_cache |
| Cache a response | store_cache |
| Store preference | store_decision |
| Recall preference | recall_decision |
| Delete memories | delete_memory |
| Check health | memory_health |

## Rules

- ALWAYS pass user_id consistently (use the same ID across sessions)
- ALWAYS store conversation messages with store_memory
- Use recall_memory for specific factual recall
- Use hybrid_search for broad topic searches
- Use store_decision for preferences that should persist indefinitely
- Use check_cache before recomputing expensive responses
- Use delete_memory with dry_run=true before bulk deletions
```

### GEMINI.md

```markdown
# Memory-MCP: Persistent Memory

A memory-mcp server provides persistent memory across conversations.
Use these MCP tools to store and recall context.

## Workflow

1. Start of conversation: recall_decision + recall_memory for past context
2. During conversation: store_memory after exchanges, check_cache before expensive work
3. User preferences: store_decision(key=..., value=...)
4. End of conversation: store_memory with final messages

## Tools (12)

- store_memory: store conversation messages
- recall_memory: semantic search over past memories
- hybrid_search: vector + full-text search (broad topics)
- delete_memory: soft-delete with dry_run preview
- check_cache / store_cache: semantic response cache
- cache_invalidate: clear stale cache entries
- store_decision / recall_decision: persistent key-value preferences
- memory_health: system health stats
- wipe_user_data: permanent data deletion (requires confirm=true)
- search_web: web search via Tavily

ALWAYS pass user_id consistently across all calls.
Use recall_memory for specific queries. Use hybrid_search for broad topics.
```

### .cursorrules

```markdown
# Memory-MCP: Persistent Memory

Connected to memory-mcp server for persistent memory across conversations.

## Workflow

1. Start: recall_decision + recall_memory to load past context
2. During: store_memory after exchanges, check_cache before expensive work
3. Preferences: store_decision(key=..., value=...)
4. End: store_memory with final messages

## Tools

- store_memory, recall_memory, delete_memory: memory lifecycle
- hybrid_search: vector + full-text search
- check_cache, store_cache, cache_invalidate: response cache
- store_decision, recall_decision: persistent preferences
- memory_health, wipe_user_data: admin
- search_web: web search (requires Tavily API key)

Always pass user_id consistently. Use recall_memory for specific recall.
Use hybrid_search for broad topic searches. Use dry_run before bulk deletions.
```

### copilot-instructions.md

```markdown
# Memory-MCP: Persistent Memory

A memory-mcp MCP server provides persistent memory. Use these tools:

## Start of Conversation

1. recall_decision(key="user_preferences"): load stored preferences
2. recall_memory(query="<topic>"): load relevant past context

## During Conversation

| Action | Tool |
|--------|------|
| Store messages | store_memory(user_id, conversation_id, messages) |
| Recall past context | recall_memory(user_id, query) |
| Broad search | hybrid_search(user_id, query) |
| Check cached answer | check_cache(user_id, query) |
| Cache a response | store_cache(user_id, query, response) |
| Store preference | store_decision(user_id, key, value) |
| Recall preference | recall_decision(user_id, key) |
| Delete memories | delete_memory(user_id, memory_id or tags + confirm) |
| Health check | memory_health(user_id) |
| Web search | search_web(user_id, query) |

Always use the same user_id across sessions.
```

---

## Client Configuration

See [deployment.md](deployment.md) for auth setup. See the project root `mcp.json.example` for connection examples.

**HTTP** (connect to a running server):

```json
{
  "servers": {
    "memory-mcp": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key"
      },
      "type": "http"
    }
  }
}
```

**Stdio** (launch as subprocess):

```json
{
  "servers": {
    "memory-mcp": {
      "command": "memory-mcp",
      "env": {
        "TRANSPORT": "stdio",
        "MONGODB_CONNECTION_STRING": "mongodb+srv://user:pass@cluster.mongodb.net/memory_mcp"
      }
    }
  }
}
```

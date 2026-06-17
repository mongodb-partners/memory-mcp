# Deployment Guide

## Prerequisites

- Docker and Docker Compose
- A MongoDB Atlas cluster with vector search enabled
- AWS credentials with Bedrock access (for default embedding/LLM providers)

## Environment Variables

Create a `.env` file in the project root. At minimum:

```bash
MONGODB_CONNECTION_STRING=mongodb+srv://user:password@cluster.mongodb.net/memory_mcp
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1
```

See [configuration.md](configuration.md) for the complete variable reference.

## Deploy with Docker Compose

```bash
docker compose up -d
```

This builds the image from the `Dockerfile` and starts the `memory-mcp` service. The compose file loads variables from `.env` automatically.

### Docker Compose configuration

From `docker-compose.yml`:

- **Image**: Built from the project `Dockerfile` (Python 3.11-slim)
- **Network**: `memory-mcp-network` (bridge driver)
- **Memory limit**: 2 GB
- **Restart policy**: `unless-stopped`
- **Health check**: HTTP GET to `/health` on port 8000 (unauthenticated, returns 200 when the server is up)
  - Interval: 30s
  - Timeout: 10s
  - Retries: 3
  - Start period: 60s

### Dockerfile

The image uses `python:3.11-slim` and `uv` for dependency management:

1. Copies the `uv` binary from the official `ghcr.io/astral-sh/uv` image
2. Copies `pyproject.toml` and `uv.lock` first (for Docker layer caching)
3. Copies the source files needed by the build
4. Installs dependencies from the locked graph with `uv sync --frozen`
5. Exposes port 8000
6. Runs `uv run memory-mcp`

## Deploy without Docker

Install dependencies and run directly:

```bash
uv sync
uv run memory-mcp
```

The server listens on `0.0.0.0:8000` with Streamable HTTP transport.

## Verify Deployment

Check that the server is running:

```bash
curl -s http://localhost:8000/health
```

A JSON response `{"status":"ok"}` confirms the server is up. This endpoint does not require authentication.

Check container health:

```bash
docker compose ps
```

The `STATUS` column shows `healthy` when the health check passes.

View logs:

```bash
docker compose logs -f memory-mcp
```

## MongoDB Atlas Setup

Memory-MCP requires a MongoDB Atlas cluster with the following:

1. **Database**: `memory_mcp` (or the name set in `MONGODB_DATABASE_NAME`)
2. **Collections**: Created automatically on first use: `memories`, `semantic_cache`, `audit_log`, `decisions`, `rate_limits`, `governance_profiles`, `prompts`
3. **Standard indexes**: Created automatically at server startup (Stage 1)
4. **Atlas Search indexes**: Created automatically in the background (Stage 2). Three indexes:
   - `memories_vector_index`: Vector search on `embedding` field (1536 dimensions, cosine similarity)
   - `memories_fts_index`: Full-text search on `content` and `summary` fields
   - `cache_vector_index`: Vector search on cache embeddings

If Atlas Search index creation fails (e.g., on a non-Atlas deployment), the server continues running. The `hybrid_search` tool and vector-based `recall_memory` require these indexes to function.

## Authentication

Auth is disabled by default. When enabled, every request must carry a valid `Authorization: Bearer <token>` header. Tokens can be either a **static API key** or an **HS256 JWT**.

### Enable auth

Add these variables to your `.env`:

```bash
AUTH_ENABLED=true
AUTH_SECRET=your-secret-key-at-least-32-characters-long
```

`AUTH_SECRET` is the HS256 signing key used to issue and verify JWTs. It must be set when `AUTH_ENABLED=true`; otherwise auth silently falls back to disabled.

### API keys

To allow clients to authenticate with static API keys, set `MEMORY_MCP_API_KEYS` as a comma-separated list of `key=user_id` pairs:

```bash
MEMORY_MCP_API_KEYS="abc123=alice@acme.com,xyz789=bob@acme.com"
```

Clients send the key in the `Authorization` header:

```
Authorization: Bearer abc123
```

The server resolves the key to the associated user ID (`alice@acme.com`). If the key is not recognized, it falls through to JWT verification.

### JWT tokens

JWTs are signed with `AUTH_SECRET` using HS256. The required claims are:

| Claim | Description |
|-------|-------------|
| `sub` | User identity (mapped to `user_id` in tools) |
| `iss` | Must be `memory-mcp` |
| `exp` | Expiration timestamp (Unix epoch) |

Token lifetime defaults to 24 hours (`AUTH_TOKEN_EXPIRY_SECONDS=86400`).

### Verification order

When a request arrives with `Authorization: Bearer <token>`:

1. Check if the token matches a registered API key in `MEMORY_MCP_API_KEYS`
2. If not, attempt to decode it as an HS256 JWT signed with `AUTH_SECRET`
3. If neither succeeds, the request is rejected

### Client configuration

**HTTP mode**: pass the token in the `headers` field of `mcp.json`:

```json
{
  "servers": {
    "memory-mcp": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer abc123"
      },
      "type": "http"
    }
  }
}
```

**Stdio mode**: pass auth config as environment variables:

```json
{
  "servers": {
    "memory-mcp": {
      "command": "memory-mcp",
      "env": {
        "TRANSPORT": "stdio",
        "MONGODB_CONNECTION_STRING": "mongodb+srv://...",
        "AUTH_ENABLED": "true",
        "AUTH_SECRET": "your-secret-key-at-least-32-characters-long",
        "MEMORY_MCP_API_KEYS": "abc123=alice@acme.com"
      }
    }
  }
}
```

### Governance and rate limiting

When auth is enabled, you can optionally enable governance policies and rate limiting:

```bash
GOVERNANCE_ENABLED=true
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60
```

When `GOVERNANCE_ENABLED=true`, three default governance profiles (admin, power_user, end_user) are seeded to the database at startup. Each profile defines allowed operations and per-role request limits (`max_searches_per_day`, `max_memories_per_day`).

Rate limiting is governance-aware: when a governance profile exists for the user's role, the per-role limits from the profile override the global `RATE_LIMIT_MAX_REQUESTS` default. Both governance and rate limiting are checked via `check_access` before every tool invocation.

See [configuration.md](configuration.md) for the full list of auth, governance, and rate limiting variables.

## Production Considerations

### Memory

The default Docker memory limit is 2 GB. Adjust in `docker-compose.yml` under `deploy.resources.limits.memory` if the enrichment worker processes large batches.

### Enrichment Worker Tuning

The background enrichment worker runs within the server process. For high-volume workloads, tune these variables:

| Variable | Default | Effect |
|----------|---------|--------|
| `ENRICHMENT_INTERVAL_SECONDS` | `30` | Polling frequency |
| `ENRICHMENT_BATCH_SIZE` | `50` | Memories per poll cycle |
| `ENRICHMENT_CONCURRENCY` | `5` | Parallel enrichment tasks |

Higher concurrency increases AWS Bedrock API usage.

### Audit Flush Strategy

A periodic background worker (`AuditFlushWorker`) flushes buffered audit entries every `AUDIT_FLUSH_INTERVAL_SECONDS` (default: 60). This runs alongside the buffer-full trigger (every 10 entries), reducing the window of unflushed entries on crash.

For compliance-sensitive deployments, set `AUDIT_FLUSH_ON_WRITE=true` to flush every audit entry immediately. This increases MongoDB write load but guarantees no audit entries are lost on crash.

If MongoDB is unreachable during flush, entries are written to `audit_fallback.jsonl` in the working directory.

### Connection Pool

MongoDB connection pool defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_MIN_POOL_SIZE` | `2` | Minimum idle connections |
| `MONGODB_MAX_POOL_SIZE` | `20` | Maximum concurrent connections |

Increase `MONGODB_MAX_POOL_SIZE` if the server handles many concurrent MCP clients.

### Cache TTL

Cache entries expire after `CACHE_TTL_SECONDS` (default: 3600, i.e. 1 hour). Increase for workloads with stable, infrequently changing responses. Decrease if cached answers go stale quickly.

### Startup Seeding

On startup, the server seeds essential data to the database (Stage 1b, after index creation):

| Data | Collection | Condition | Count |
|------|-----------|-----------|-------|
| Governance profiles (admin, power_user, end_user) | `governance_profiles` | `GOVERNANCE_ENABLED=true` | 3 |
| Prompt templates (importance_assessment, summary_generation, merge_prompt) | `prompts` | Always | 3 |
| System decisions (system:governance_profile, system:prompt_experiment) | `decisions` | Always | 2 |

Seeding is idempotent: existing records are not overwritten. Failures are logged but non-fatal; the server continues startup.

### Auto-Capture

Auto-capture is enabled by default (`AUTO_CAPTURE_ENABLED=true`). It wraps configured MCP tools with middleware that stores tool interactions as STM memories. This ensures the memory store is populated through normal usage even when the LLM does not call `store_memory`.

To disable auto-capture:

```bash
AUTO_CAPTURE_ENABLED=false
```

See [configuration.md](configuration.md) for `AUTO_CAPTURE_TOOLS`, `AUTO_CAPTURE_MIN_LENGTH`, and `AUTO_CAPTURE_MAX_CONTENT_LENGTH`.

## Rollback

To stop and remove the container:

```bash
docker compose down
```

Data persists in MongoDB Atlas. No Docker volumes are used for application state.

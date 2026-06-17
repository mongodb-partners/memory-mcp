# Architecture

## System Overview

Memory-MCP is an MCP (Model Context Protocol) server that provides AI applications with persistent memory, semantic caching, and hybrid search capabilities. It runs as a single Python process using FastMCP and stores all data in MongoDB Atlas.

The server targets AI agents and LLM-based applications that need to remember past conversations, retrieve relevant context, and cache repeated queries.

## System Context

### Users / Actors

- **AI Agent (MCP Client)**: Calls MCP tools to store, recall, and search memories. Each agent identifies itself via a `user_id` parameter.

### External Systems

- **MongoDB Atlas**: Stores memories, cache entries, and audit logs. Provides vector search (`$vectorSearch`), full-text search (`$search`), and TTL-based expiration.
- **AWS Bedrock**: Generates text embeddings (Titan Embed) and runs LLM inference (Claude) for enrichment tasks (importance scoring, summarization, memory merging).
- **Voyage AI** (optional): Alternative embedding provider. Used when `EMBEDDING_PROVIDER=voyage`.
- **Tavily API** (optional): Web search provider for the `search_web` tool. Requires `TAVILY_API_KEY`.

## Containers

| Container | Technology | Responsibility |
|-----------|-----------|----------------|
| Memory-MCP Server | Python 3.11, FastMCP | Hosts MCP tools, manages service lifecycle, runs background workers |
| MongoDB Atlas | MongoDB 7.0+ | Persists memories, cache, and audit logs; provides search indexes |
| AWS Bedrock | Amazon Titan, Claude | Generates embeddings and LLM responses for enrichment |

## Component Details

### Core Layer (`core/`)

**`MCPConfig`** (`core/config.py`)
- Centralized configuration via Pydantic BaseSettings
- Loads from `.env` file or environment variables
- Validates types and provides defaults for 50+ settings
- Single required field: `MONGODB_CONNECTION_STRING`

**`DatabaseManager`** (`core/database.py`)
- Async-safe MongoDB connection pool singleton
- Double-checked locking with `asyncio.Lock`
- Configurable pool sizes (min 2, max 20)
- Initialized once during server startup, closed on shutdown

**`ServiceRegistry`** (`core/registry.py`)
- Module-level singleton holding all initialized services
- Tools call `ServiceRegistry.get()` to access services
- Initialized after all services are created during lifespan startup

**`Collections and Indexes`** (`core/collections.py`, `core/migrations.py`)
- Defines seven collections: `memories`, `semantic_cache`, `audit_log`, `decisions`, `rate_limits`, `governance_profiles`, `prompts`
- Two-stage index creation:
  - Stage 1 (blocking): Standard B-tree indexes for queries and TTL expiration
  - Stage 2 (background): Atlas Search indexes for vector and full-text search
- Non-Atlas deployments degrade gracefully (no vector/FTS search)

### Provider Layer (`providers/`)

**`ProviderManager`** (`providers/manager.py`)
- Factory that creates embedding and LLM providers based on configuration
- Exposes `.embedding` (EmbeddingProvider) and `.llm` (LLMProvider) attributes

**`BedrockEmbeddingProvider`** (`providers/bedrock.py`)
- Uses `boto3` with `bedrock-runtime` client
- Model: `amazon.titan-embed-text-v1` (1536 dimensions)
- Runs blocking boto3 calls via `asyncio.to_thread()`

**`BedrockLLMProvider`** (`providers/bedrock.py`)
- Uses the Bedrock `converse()` API with Claude
- Provides `assess_importance()` (returns 0.1-1.0) and `generate_summary()` methods

**`VoyageEmbeddingProvider`** (`providers/voyage.py`)
- Uses `httpx.AsyncClient` for the Voyage AI REST API
- Model: `voyage-3` (default)
- Batches requests at 128 texts per API call

### Service Layer (`services/`)

**`MemoryService`** (`services/memory.py`)
- **Store**: Creates STM documents with embeddings. Auto-creates LTM candidates for human messages >30 characters.
- **Recall**: Vector search with deduplication of STM/LTM pairs, calibrated 3-component ranking, and access counter updates.
- **Delete**: Soft-delete by ID, tags, or time range. Bulk deletes require `confirm=true`. Supports dry-run preview.
- **Evolve**: Detects similar memories and either reinforces (>0.85 similarity), queues merge (0.70-0.85), or creates new.

**`CacheService`** (`services/cache.py`)
- **Check**: Vector search on cached embeddings; returns hit if similarity >= threshold (default 0.95).
- **Store**: Inserts query-response pair with embedding and TTL.
- **Invalidate**: Bulk or pattern-based hard-delete of cache entries.

**`AuditService`** (`services/audit.py`)
- Buffers audit entries in memory.
- Flushes to MongoDB on buffer full, timer elapsed, or write-through mode.
- Falls back to local `audit_fallback.jsonl` file if MongoDB write fails.

**`AuditFlushWorker`** (`services/audit_flush_worker.py`)
- Runs as an `asyncio.Task` that periodically calls `AuditService.flush()`.
- Interval configurable via `AUDIT_FLUSH_INTERVAL_SECONDS` (default: 60s).
- Ensures buffered audit entries are persisted even if no new operations trigger a buffer-full flush.
- Prevents audit data loss on process crash by reducing the window of unflushed entries.

**`EnrichmentWorker`** (`services/enrichment.py`)
- Runs as an `asyncio.Task` within the server process.
- Polls for pending LTM memories every 30 seconds (configurable).
- Processes in batches of 50 with concurrency limit of 5.
- For each memory: assesses importance via LLM, generates summary, runs evolution check.
- Retries up to 3 times on failure; marks as failed on exhaustion.

**`ConsolidationWorker`** (`services/consolidation.py`)
- Runs as an `asyncio.Task` alongside the enrichment worker.
- Compresses old STM (past `stm_compression_age_hours`), forgets low-importance memories, and promotes qualified STM to LTM.
- Cycle interval configurable via `CONSOLIDATION_INTERVAL_HOURS`.

**`AutoCaptureMiddleware`** (`services/auto_capture.py`)
- Wraps registered MCP tools with transparent memory capture.
- After each tool call completes, fires an async task to store the tool name, parameters, and response as an STM memory with `conversation_id="auto:<tool_name>"`.
- Configurable via `AUTO_CAPTURE_ENABLED`, `AUTO_CAPTURE_TOOLS`, `AUTO_CAPTURE_MIN_LENGTH`, and `AUTO_CAPTURE_MAX_CONTENT_LENGTH`.
- Excluded tools (`store_memory`, `wipe_user_data`, `delete_memory`, `cache_invalidate`) are never captured regardless of config.
- Failures are logged but never propagated. Auto-capture is strictly non-blocking.

**`DecisionService`** (`services/decision.py`)
- Keyed key-value store for sticky decisions/preferences.
- Upserts by `(user_id, key)` with optional TTL via MongoDB TTL index on `expires_at`.
- Seeds system defaults at startup (`system:governance_profile`, `system:prompt_experiment`) so the system has sensible preferences from first boot.

**`GovernanceService`** (`services/governance.py`)
- Role-based access policies (admin, power_user, end_user) stored in `governance_profiles` collection.
- Seeds three default profiles at startup (admin, power_user, end_user) with per-role operation limits and allowed operations.
- In-memory cache with configurable TTL.
- Enabled via `GOVERNANCE_ENABLED=true`.

**`RateLimiter`** (`services/rate_limiter.py`)
- Sliding-window per-user rate limiting using the `rate_limits` collection.
- Governance-aware: when a governance profile exists for the user's role, per-role limits (`max_searches_per_day`, `max_memories_per_day`) override the global `RATE_LIMIT_MAX_REQUESTS` default.
- Enabled via `RATE_LIMIT_ENABLED=true`.

**`PromptLibrary`** (`services/prompt_library.py`)
- Versioned prompt templates stored in the `prompts` collection.
- Seeds default prompt templates at startup (`importance_assessment`, `summary_generation`, `merge_prompt`) so enrichment works from first boot.
- Falls back to hardcoded defaults when `PROMPT_EXPERIMENT_ENABLED=false`.

### Tool Layer (`tools/`)

Twelve MCP tools organized into five modules:

- `tools/memory_tools.py`: `store_memory`, `recall_memory`, `delete_memory`
- `tools/cache_tools.py`: `check_cache`, `store_cache`
- `tools/search_tools.py`: `hybrid_search`, `search_web`
- `tools/admin_tools.py`: `memory_health`, `wipe_user_data`, `cache_invalidate`
- `tools/decision_tools.py`: `store_decision`, `recall_decision`

Each tool delegates to its service via `ServiceRegistry.get()` and logs the operation through `AuditService`.

## Data Flow

### Store Memory

```
MCP Client
  → store_memory(user_id, conversation_id, messages)
    → MemoryService.store_stm()
      → EmbeddingProvider.generate_embeddings_batch(messages)
      → MongoDB insert: STM documents (tier="stm", expires_at=now+24h)
      → MongoDB insert: LTM candidates (tier="ltm", enrichment_status="pending")
    → AuditService.log(operation="memory:write")
  ← {stm_ids: [...], count: N}
```

### Recall Memory

```
MCP Client
  → recall_memory(user_id, query, limit=10)
    → MemoryService.recall()
      → EmbeddingProvider.generate_embedding(query)
      → MongoDB $vectorSearch (memories collection)
      → Deduplicate STM/LTM pairs (keep higher score)
      → Calibrated ranking: score = α·recency + β·importance + γ·relevance
      → MongoDB update: increment access_count, set last_accessed
    → AuditService.log(operation="memory:read")
  ← {results: [...], count: N}
```

### Hybrid Search

```
MCP Client
  → hybrid_search(user_id, query, limit=10)
    → EmbeddingProvider.generate_embedding(query)
    → MongoDB $rankFusion aggregation:
        vectorPipeline: $vectorSearch on embedding field
        fullTextPipeline: $search on content + summary fields
        combination.weights: {vector: rrf_vector_weight, text: rrf_text_weight}
    → $limit → $project (strip embedding)
    → AuditService.log(operation="search")
  ← {results: [...], count: N}
```

### Background Enrichment

```
EnrichmentWorker (continuous loop, every 30s)
  → Query: find memories where enrichment_status="pending", limit 50
  → For each memory (concurrency=5):
    → LLM: assess_importance(content) → float (0.1-1.0)
    → LLM: generate_summary(content) → string (≤100 words)
    → MemoryService.evolve_memory(user_id, content, embedding)
      → Vector search for similar LTM
      → >0.85 similarity: reinforce (boost importance 1.1×)
      → 0.70-0.85: queue merge (enrichment_status="merge_pending")
      → <0.70: create new memory
    → MongoDB update: enrichment_status="complete", importance, summary
```

### Startup Seeding (Stage 1b)

```
Server lifespan startup
  → Stage 1: ensure_indexes() creates standard B-tree indexes (blocking)
  → Stage 1b: Seed essential data (best-effort, non-fatal)
    → GovernanceService.seed_defaults() (if GOVERNANCE_ENABLED)
      → Upsert 3 profiles: admin, power_user, end_user
    → PromptLibrary.seed_defaults()
      → Insert 3 templates: importance_assessment, summary_generation, merge_prompt (skip if exists)
    → DecisionService.seed_defaults()
      → Insert 2 decisions: system:governance_profile, system:prompt_experiment (user_id="system", skip if exists)
  → Start background workers (enrichment, consolidation, audit flush)
  → Auto-capture: wrap registered tools (if AUTO_CAPTURE_ENABLED)
  → Stage 2: Atlas Search indexes (background, non-blocking)
```

### Auto-Capture

```
MCP Client
  → any_tool(user_id, ...)
    → AutoCaptureMiddleware.wrapped()
      → original_fn(*args, **kwargs) → result
      → asyncio.create_task(capture(tool_name, kwargs, result))
        → should_capture? (enabled, tool in list, not excluded, has user_id)
        → build_content(tool_name, params, response) → string (truncated to max_content_length)
        → MemoryService.store_stm(user_id, conversation_id="auto:<tool_name>", messages=[...])
    ← result (unmodified; capture is fire-and-forget)
```

## Key Design Decisions

### Single-process architecture

**Context:** Phase 0 requires a working system with minimal operational complexity.
**Decision:** Run everything (MCP server, enrichment worker, audit flushing) in a single FastMCP process.
**Rationale:** Eliminates inter-service communication, shared-nothing coordination, and deployment of multiple containers. The enrichment worker runs as an `asyncio.Task`, sharing the event loop.
**Trade-offs:** Vertical scaling only. The enrichment worker competes for CPU with request handling. Acceptable at Phase 0 scale.

### Two-tier memory model (STM/LTM)

**Context:** AI agents produce many messages, but not all are worth keeping permanently.
**Decision:** Store all messages as short-term memories with a 24-hour TTL. Automatically promote human messages >30 characters to long-term memory candidates for enrichment.
**Rationale:** TTL-based STM auto-purges via MongoDB index. LTM undergoes quality assessment before becoming permanent. Deduplication during recall prevents showing both STM and LTM versions of the same content.

### Calibrated ranking formula

**Context:** Raw vector similarity alone does not reflect how useful a memory is in context.
**Decision:** Rank results using `score = α·recency + β·importance + γ·relevance` where α=0.2, β=0.3, γ=0.5.
**Rationale:** Recency uses exponential decay (half-life ~30 days). Importance incorporates LLM-assessed value boosted by access frequency. Relevance is the vector similarity score. Weights are configurable.

### Soft-delete with TTL purge

**Context:** Hard-deleting memories makes audit trails incomplete and prevents accidental deletion recovery.
**Decision:** Delete operations set `deleted_at` and `is_deleted=true`. A TTL index on `deleted_at` purges soft-deleted documents after 30 days.
**Rationale:** All query filters exclude soft-deleted documents. Audit logs reference the original memory IDs. Recovery is possible within the purge window.

### Non-blocking Atlas Search index creation

**Context:** Atlas Search index creation can take minutes and may fail on non-Atlas deployments.
**Decision:** Standard B-tree indexes are created synchronously at startup (Stage 1). Atlas Search indexes are created in a background task (Stage 2) that polls for readiness with a 120-second timeout.
**Rationale:** The server becomes available after Stage 1 completes. If Stage 2 fails, the server runs without vector/FTS search; tools degrade but do not crash.

### Startup seeding over LLM-dependent initialization

**Context:** Governance profiles, prompt templates, and system decisions were previously hardcoded as in-memory fallbacks. The database was never populated unless the LLM happened to call the right tools.
**Decision:** Seed all essential configuration data to the database at startup (Stage 1b). Seeding is idempotent (skip if exists) and best-effort (failures are logged but non-fatal).
**Rationale:** Anything the system needs to function should not depend on the LLM choosing to create it. DB-first seeding eliminates wasted round-trips (query-miss-fallback), makes configuration editable by admins via the database, and ensures the system is fully operational from first boot.

### Auto-capture over LLM-dependent memory storage

**Context:** If the LLM never calls `store_memory`, the memory store remains empty and recall returns nothing, defeating the purpose of the system.
**Decision:** Wrap MCP tools with middleware that automatically stores tool interactions as STM memories. Capture is fire-and-forget (async task, failures logged, never blocks the response).
**Rationale:** Ensures the memory store is populated through normal usage even when the LLM does not cooperate. The auto-capture middleware is transparent to both the LLM and the tool implementation.

## Security Considerations

- **Multi-tenant isolation**: Every service method injects `user_id` into MongoDB query filters via `_base_filter()`. No cross-user data leakage is possible through the service layer.
- **Soft-delete filtering**: All queries include `deleted_at: null` to exclude soft-deleted documents.
- **Authentication**: When `AUTH_ENABLED=true`, every MCP request must carry a valid `Authorization: Bearer <token>` header. Tokens are verified as either a registered API key or an HS256 JWT. See [deployment.md](deployment.md) for configuration.
- **Governance and rate limiting**: Optional role-based access control (`GOVERNANCE_ENABLED`) and per-user request quotas (`RATE_LIMIT_ENABLED`) are available when auth is enabled.
- **Credential management**: AWS credentials and API keys are loaded from environment variables, not hardcoded.
- **Health endpoint**: The `/health` endpoint is unauthenticated by design for Docker and load balancer probes.

## Collections

| Collection | Purpose | TTL |
|------------|---------|-----|
| `memories` | STM and LTM storage | `expires_at` (STM: 24h), `deleted_at` (soft-delete: 30d) |
| `semantic_cache` | Query-response cache | `created_at` (default: 3600s) |
| `audit_log` | Operation audit trail | `timestamp` (365 days) |
| `decisions` | Sticky key-value decisions | `expires_at` (configurable per decision) |
| `rate_limits` | Per-user rate limit counters | `timestamp` (24h) |
| `governance_profiles` | Role-based access policies | — |
| `prompts` | Versioned prompt templates | — |

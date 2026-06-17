# Developer Guide

## Prerequisites

- Python 3.11 or later
- A MongoDB Atlas cluster with vector search support
- AWS credentials with Bedrock access (or a Voyage AI API key)

## Setup

```bash
git clone https://github.com/mongodb-partners/memory-mcp.git
cd memory-mcp
uv sync --extra dev
```

Copy the environment template and configure credentials:

```bash
cp .env.example .env
```

Edit `.env` with your MongoDB Atlas connection string and AWS credentials. See [configuration.md](configuration.md) for all available variables.

## Running Locally

```bash
uv run memory-mcp
```

The server starts on `http://0.0.0.0:8000` using Streamable HTTP transport. On startup it:

1. Connects to MongoDB and verifies the connection
2. Creates standard B-tree indexes (blocking, Stage 1)
3. Initializes embedding and LLM providers
4. Creates service instances and populates the service registry
5. Seeds essential data to the database: governance profiles, prompt templates, system decisions (Stage 1b, idempotent, best-effort)
6. Starts background workers: enrichment, consolidation, and audit flush
7. Wraps MCP tools with auto-capture middleware (if `AUTO_CAPTURE_ENABLED`)
8. Launches Atlas Search index creation in the background (Stage 2)

An unauthenticated `/health` endpoint is available for Docker and load balancer probes.

## Project Structure

```
__init__.py           # Package version (__version__ = "3.2.0")
__main__.py           # CLI entry point (memory-mcp command)
server.py             # FastMCP lifespan, tool registration, /health endpoint
core/
  config.py           # MCPConfig (Pydantic BaseSettings, 50+ fields)
  database.py         # DatabaseManager (async singleton)
  registry.py         # ServiceRegistry (singleton service holder)
  collections.py      # Index and collection schema definitions
  migrations.py       # Startup index creation (standard + Atlas Search)
auth/
  api_keys.py         # APIKeyManager (loads MEMORY_MCP_API_KEYS env var)
  token_verifier.py   # MemoryMCPTokenVerifier (API key + HS256 JWT)
providers/
  base.py             # Abstract EmbeddingProvider, LLMProvider
  bedrock.py          # AWS Bedrock embedding and LLM implementations
  voyage.py           # Voyage AI embedding implementation
  manager.py          # ProviderManager (factory)
services/
  memory.py           # MemoryService (store, recall, delete, evolve)
  cache.py            # CacheService (check, store, invalidate)
  audit.py            # AuditService (buffered logging)
  audit_flush_worker.py  # AuditFlushWorker (periodic background flush)
  auto_capture.py     # AutoCaptureMiddleware (transparent tool interaction capture)
  enrichment.py       # EnrichmentWorker (background async task)
  consolidation.py    # ConsolidationWorker (STM compression, forgetting, promotion)
  decision.py         # DecisionService (keyed key-value store with TTL, startup seeding)
  governance.py       # GovernanceService (role-based access policies, startup seeding)
  rate_limiter.py     # RateLimiter (sliding-window, governance-aware per-role limits)
  prompt_library.py   # PromptLibrary (versioned prompt templates, startup seeding)
tools/
  memory_tools.py     # store_memory, recall_memory, delete_memory
  cache_tools.py      # check_cache, store_cache
  search_tools.py     # hybrid_search, search_web
  admin_tools.py      # memory_health, wipe_user_data, cache_invalidate
  decision_tools.py   # store_decision, recall_decision
tests/
  unit/               # Unit tests (pytest + pytest-asyncio)
  integration/        # Functional tests (end-to-end MCP tool calls)
```

## Common Tasks

### Add a new MCP tool

1. Create or edit a tool file in `tools/` (e.g., `tools/my_tools.py`)
2. Define a `register_my_tools(mcp)` function that decorates async functions with `@mcp.tool()`
3. Each tool function should:
   - Get services via `ServiceRegistry.get()`
   - Delegate business logic to a service method
   - Log the operation via `AuditService.log()`
   - Return a JSON-serializable dict
4. Register the tools in `server.py` by calling `register_my_tools(mcp)` after the existing registrations

### Add a new embedding provider

1. Create a new file in `providers/` (e.g., `providers/openai.py`)
2. Implement `EmbeddingProvider` from `providers/base.py`:
   - `async generate_embedding(text: str) -> list[float]`
   - `async generate_embeddings_batch(texts: list[str]) -> list[list[float]]`
3. Add the provider to `ProviderManager.__init__()` in `providers/manager.py` with a new config branch
4. Add any new config fields to `MCPConfig` in `core/config.py`

### Add a new service

1. Create a service class in `services/` with the business logic
2. Accept the MongoDB collection and config in `__init__()`
3. Add the service as a field in `ServiceRegistry` (`core/registry.py`)
4. Initialize it in the `lifespan()` function in `server.py`
5. Pass it to `ServiceRegistry.initialize()`

## Running Tests

```bash
# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/unit/test_memory_service.py

# Run tests with verbose output
uv run pytest -v

# Run a specific test by name
uv run pytest -k "test_store_stm"
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`. All MongoDB operations are mocked with `AsyncMock`. No running database is required for unit tests.

### Test conventions

- Test files: `tests/unit/test_*.py`
- Test functions: `test_<description>` or `async def test_<description>`
- Fixtures for config, database mocks, and singleton resets are defined per test file
- Singletons (`DatabaseManager`, `ServiceRegistry`) are reset between tests via fixtures

## Code Conventions

- **Async throughout**: All service methods and tool functions are `async`. Use `await` for database operations and provider calls.
- **Type hints**: All function signatures use Python type hints. Use `str | None` (not `Optional[str]`).
- **Singletons**: `DatabaseManager` and `ServiceRegistry` use double-checked locking with `asyncio.Lock`.
- **Error handling**: Tools catch exceptions, log audit errors, and re-raise. Services raise exceptions directly.
- **BSON sanitization**: Convert `ObjectId` to `str` and `datetime` to ISO 8601 strings before returning from tools.

## Build and Package

Build a distributable wheel:

```bash
uv build
```

The wheel is output to `dist/`. The package name is `memory-mcp` and the installable command is `memory-mcp`.

Hatchling maps source files from the project root into the `memory_mcp` package namespace (see `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`).

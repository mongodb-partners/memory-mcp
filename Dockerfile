FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy project metadata and lockfile first for better layer caching
COPY pyproject.toml uv.lock ./

# Copy source files needed by the build
COPY __init__.py .
COPY __main__.py .
COPY server.py .
COPY core/ core/
COPY providers/ providers/
COPY services/ services/
COPY tools/ tools/
COPY auth/ auth/

# Install dependencies using locked graph
RUN uv sync --frozen

EXPOSE 8000

CMD ["uv", "run", "memory-mcp"]

"""Entry point: python -m memory_mcp."""

from memory_mcp.core.config import MCPConfig
from memory_mcp.server import mcp


def main():
    """CLI entry point for ``memory-mcp`` script."""
    config = MCPConfig()
    if config.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    main()

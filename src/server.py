"""
MCP Server — Local Repository Reader + Read-Only Database Access
Built with FastMCP (Python MCP SDK wrapper)

Transports supported:
  HTTP + SSE  (default) — clients connect via  GET  http://host:port/sse
  stdio                 — set MCP_TRANSPORT=stdio  (for Claude Desktop)

Environment variables:
  MCP_TRANSPORT   "http" (default) | "stdio"
  MCP_HOST        bind host,    default "0.0.0.0"
  MCP_PORT        bind port,    default "8000"
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastmcp import FastMCP


def _load_env_file() -> None:
    """Load environment variables from the project .env file if present."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if ((value.startswith('"') and value.endswith('"')) or
                    (value.startswith("'") and value.endswith("'"))):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value


_load_env_file()

from .repo_tools import register_repo_tools
from .db_tools   import register_db_tools

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mcp-server")

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="local-repo-db-server"
)

register_repo_tools(mcp)
register_db_tools(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "http").lower()

    if transport == "stdio":
        log.info("Starting MCP server — transport: stdio")
        mcp.run(transport="stdio")

    else:
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))


        # Build the Starlette/ASGI app from FastMCP
        app = mcp.http_app(path="/demo")      

        # Add /health endpoint
        from starlette.routing import Route
        from starlette.responses import JSONResponse as _JSON

        async def health(_):
            return _JSON({"status": "ok", "server": "local-repo-db-mcp"})

        # Run with uvicorn
        try:
            import uvicorn
        except ImportError:
            log.error(
                "uvicorn is not installed. Run:  pip install uvicorn"
            )
            sys.exit(1)

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
        )


if __name__ == "__main__":
    main()

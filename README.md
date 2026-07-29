# 🛠️ Local Repo + MySQL — MCP Server (HTTP + SSE)

A production-ready **Model Context Protocol (MCP) server** built with **FastMCP** running over **HTTP with SSE transport**.

Two capabilities:
1. **Read-only local Git repository access** — browse, read, search, git log, file tree.
2. **Read-only remote MySQL access** — SELECT queries only; mutations are hard-blocked at two independent layers.

---

## 📁 Project Structure

```
mcp-server/
├── src/
│   ├── __init__.py       # Package entry point
│   ├── server.py         # FastMCP HTTP+SSE bootstrap
│   ├── repo_tools.py     # Repository tools
│   └── db_tools.py       # MySQL tools (SELECT-only)
├── config/
│   └── claude_desktop_config.json
├── tests/
│   └── test_server.py
├── .env.example          # ← copy to .env and fill in values
├── pyproject.toml
└── README.md
```

---

## ⚡ Quick Start

### 1 — Install

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

pip install fastmcp mcp uvicorn starlette mysql-connector-python
```

### 2 — Configure

```bash
cp .env.example .env
# Edit .env — fill in MCP_TRANSPORT, MCP_HOST, MCP_PORT, MCP_API_KEY
```

> **Note:** the server's own `.env` no longer holds database credentials.
> Every query runs against the *target project's* database — see
> [Multi-Project Database Access](#-multi-project-database-access) below.

### 3 — Run the HTTP server

```bash
python -m src.server
```

Output:
```
2024-01-01 12:00:00  INFO     mcp-server — Starting MCP server — transport: HTTP+SSE  on  http://0.0.0.0:8000
2024-01-01 12:00:00  INFO     mcp-server — SSE endpoint  :  http://0.0.0.0:8000/sse
2024-01-01 12:00:00  INFO     mcp-server — Health check  :  http://0.0.0.0:8000/health
```

### 4 — Test it

```bash
# Health check
curl http://localhost:8000/health

# SSE stream (with auth)
curl -H "Authorization: Bearer your-api-key" http://localhost:8000/sse
```

---

## 🔗 Connecting Clients

### Claude Desktop / Cursor (HTTP — one shared server, many projects)

Every project sends its own `X-Project-Root` header alongside the same `url`.
The server loads that project's `.env` (never its own) for every DB call —
see [Multi-Project Database Access](#-multi-project-database-access).

```json
{
  "mcpServers": {
    "impacct-backend": {
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer change-me-to-a-strong-secret",
        "X-Project-Root": "/absolute/path/to/Impacct_Backend"
      }
    },
    "crm-backend": {
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Bearer change-me-to-a-strong-secret",
        "X-Project-Root": "/absolute/path/to/CRM_Backend"
      }
    }
  }
}
```

### Claude Desktop (stdio — one process per project, no separate server)

```json
{
  "mcpServers": {
    "local-repo-db": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/absolute/path/to/mcp-server",
      "env": {
        "MCP_TRANSPORT": "stdio",
        "REPO_ROOT": "/path/to/your/project"
      }
    }
  }
}
```

In stdio mode there's no per-request header, so the server falls back to
`REPO_ROOT` (or `cwd`) to find the project — same DB-config resolution
either way, just a different way of pointing at the project.

---

## 🗄️ Multi-Project Database Access

Database credentials are **never** read from this MCP server's own `.env`.
On every DB tool call, the server:

1. Resolves the *target project* — from the request's `X-Project-Root`
   header (HTTP), or `REPO_ROOT`/`cwd` (stdio).
2. Loads that project's own `.env` (`.env`, `.env.<APP_ENV>`, `.env.local`,
   `.env.<APP_ENV>.local` — later files override earlier ones).
3. Reads `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` from
   it (legacy `MYSQL_*` names are also accepted).
4. Opens a brand-new connection, runs the query, and always closes the
   connection — nothing is cached or reused across projects or requests.

Connecting a new project requires **no changes to the MCP server** — just
point a new client entry at it with its own `X-Project-Root` (or `cwd`).

```
Impacct Backend/.env  → DB_NAME=impacct_db   ─┐
CRM Backend/.env      → DB_NAME=crm_db        ├─▶ same running MCP server
ERP Backend/.env      → DB_NAME=erp_db       ─┘
```

---

## 🧰 Available MCP Tools

### Repository (6 tools)

| Tool | Description |
|---|---|
| `repo_info` | Branch, last commit, remote, git status |
| `list_files` | Glob-filtered listing, optional recursion |
| `read_file` | Read UTF-8 file content (max 2 MB) |
| `search_in_repo` | Regex/text grep across files |
| `git_log` | Recent commit history |
| `file_tree` | ASCII directory tree |

### MySQL / Database (6 tools)

| Tool | Description |
|---|---|
| `db_info` | Connection metadata (no secrets) |
| `mysql_test_connection` | Ping server, verify SSL, check credentials |
| `list_tables` | All table names |
| `describe_table` | Column schema for a table |
| `run_select_query` | Execute any SELECT statement |
| `preview_table` | First N rows without writing SQL |

---

## ⚙️ Environment Variables

### HTTP Server

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `http` | `"http"` for SSE over HTTP, `"stdio"` for local |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8000` | Bind port |
| `MCP_API_KEY` | _(none)_ | Bearer token for auth (strongly recommended) |

### Repository

| Variable | Default | Description |
|---|---|---|
| `REPO_ROOT` | `cwd` | Absolute path to local repository; also the stdio-mode fallback for locating a project's database `.env` |

### Database (read from the **target project's** `.env`, never the server's own)

Set these in the project you're connecting to, not in this server's `.env`.
`MYSQL_*` names are also accepted for backward compatibility.

| Variable | Required | Description |
|---|---|---|
| `DB_HOST` | ✅ | Remote server hostname or IP |
| `DB_PORT` | `3306` | MySQL port |
| `DB_NAME` | ✅ | Database / schema name |
| `DB_USER` | ✅ | MySQL username |
| `DB_PASSWORD` | ✅ | MySQL password |
| `DB_CONNECT_TIMEOUT` | `10` | Seconds before timeout |
| `DB_CHARSET` | `utf8mb4` | Character set |
| `DB_SSL` | `false` | `"true"` to enable TLS |
| `DB_SSL_CA` | — | CA certificate path (PEM) |
| `DB_SSL_CERT` | — | Client certificate (mutual TLS) |
| `DB_SSL_KEY` | — | Client private key (mutual TLS) |
| `DB_SSL_VERIFY_CERT` | `true` | `"false"` to skip cert check |

### Request-level routing (HTTP transport)

| Header | Required | Description |
|---|---|---|
| `X-Project-Root` | ✅ (HTTP, multi-project) | Absolute path to the target project's root — where its `.env` lives |

`DB_MAX_ROWS` (default `500`) is the one DB-related setting that **is** read
from the MCP server's own `.env` — it's a server-side safety cap, not a
credential.

---

## 🔒 Security

- **SQL guard (2 layers):** allow-list (must start with SELECT) + deny-list (blocks INSERT/UPDATE/DELETE/DROP/etc.)
- **Path sandboxing:** all file paths checked against `REPO_ROOT`; traversal attacks rejected
- **Bearer token auth:** set `MCP_API_KEY` to protect the HTTP endpoint
- **No secrets in responses:** `db_info` never returns passwords
- **No cross-project leakage:** DB config is resolved fresh per request from the target project's own `.env` and never cached or shared across projects

---

## 🚀 Deploy with Docker (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install fastmcp mcp uvicorn starlette mysql-connector-python
EXPOSE 8000
CMD ["python", "-m", "src.server"]
```

```bash
docker build -t mcp-server .
docker run -p 8000:8000 \
  -v /path/to/your/projects:/projects:ro \
  -e MCP_API_KEY=strong-secret \
  mcp-server
```

Database credentials aren't passed to the container — each client request's
`X-Project-Root` header points at a project directory under the mounted
`/projects` volume, and the server reads that project's own `.env` from there.

---

## 🧪 Run Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

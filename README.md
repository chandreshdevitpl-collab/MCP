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
# Edit .env — fill in MYSQL_HOST, MYSQL_DB, MYSQL_USER, MYSQL_PASSWORD, REPO_ROOT
```

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

### Claude Desktop (HTTP — recommended)

In `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "local-repo-db": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### Claude Desktop (stdio — alternative, no separate server needed)

```json
{
  "mcpServers": {
    "local-repo-db": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/absolute/path/to/mcp-server",
      "env": {
        "MCP_TRANSPORT": "stdio",
        "REPO_ROOT": "/path/to/repo",
        "DB_TYPE": "mysql",
        "MYSQL_HOST": "your-server.com",
        "MYSQL_DB": "mydb",
        "MYSQL_USER": "user",
        "MYSQL_PASSWORD": "secret"
      }
    }
  }
}
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
| `REPO_ROOT` | `cwd` | Absolute path to local repository |

### MySQL

| Variable | Required | Description |
|---|---|---|
| `MYSQL_HOST` | ✅ | Remote server hostname or IP |
| `MYSQL_PORT` | `3306` | MySQL port |
| `MYSQL_DB` | ✅ | Database / schema name |
| `MYSQL_USER` | ✅ | MySQL username |
| `MYSQL_PASSWORD` | ✅ | MySQL password |
| `MYSQL_CONNECT_TIMEOUT` | `10` | Seconds before timeout |
| `MYSQL_CHARSET` | `utf8mb4` | Character set |
| `MYSQL_SSL` | `false` | `"true"` to enable TLS |
| `MYSQL_SSL_CA` | — | CA certificate path (PEM) |
| `MYSQL_SSL_CERT` | — | Client certificate (mutual TLS) |
| `MYSQL_SSL_KEY` | — | Client private key (mutual TLS) |
| `MYSQL_SSL_VERIFY_CERT` | `true` | `"false"` to skip cert check |

---

## 🔒 Security

- **SQL guard (2 layers):** allow-list (must start with SELECT) + deny-list (blocks INSERT/UPDATE/DELETE/DROP/etc.)
- **Path sandboxing:** all file paths checked against `REPO_ROOT`; traversal attacks rejected
- **Bearer token auth:** set `MCP_API_KEY` to protect the HTTP endpoint
- **No secrets in responses:** `db_info` never returns passwords

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
  -e MYSQL_HOST=your-server.com \
  -e MYSQL_DB=mydb \
  -e MYSQL_USER=user \
  -e MYSQL_PASSWORD=secret \
  -e REPO_ROOT=/app/repo \
  -e MCP_API_KEY=strong-secret \
  mcp-server
```

---

## 🧪 Run Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

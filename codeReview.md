# Code Review Best Practices

# Format:
# - write a short rule description
# - optionally add a regex pattern after "->" to detect rule-relevant diff content

Avoid hard-coded credentials -> MYSQL_PASSWORD|POSTGRES_PASSWORD|DB_PASSWORD|PASSWORD
Load environment variables before use -> os\.environ|getenv|dotenv
Keep database access read-only -> SELECT\b|SHOW\b|DESCRIBE\b
Avoid committing TODO/FIXME comments -> TODO|FIXME
Use parameterized SQL instead of string formatting -> %\(|format\(|f".*\{.*\}"|
Restrict HTTP auth configuration to secure headers -> Authorization|Bearer\s+|MCP_API_KEY


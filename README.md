# Grafana Inspection Agent (LangGraph)

An automated Grafana inspection system orchestrated by **LangGraph StateGraph**. It collects dashboard panel metrics and alert data from Grafana, performs intelligent analysis via GitHub Copilot LLM, generates daily inspection reports and JVM health analysis reports, and delivers them through Email / Teams.

## Features

- **Dashboard Auto-Inspection** — Parallel metric collection across all panels with LLM-generated summaries
- **Alert Monitoring** — Full analysis of alert rules, active alerts, and alert history
- **DB/Kafka Panel Health Analysis** — Auto-filters database/Kafka panels, chunked parallel analysis
- **JVM Health Report** — Filters JVM-related panels (Heap, GC, Thread, Metaspace, etc.) for a dedicated report
- **Multi-Channel Notification** — Email (aiosmtplib) + Microsoft Teams (Webhook)
- **Multi-Language** — Chinese / English reports
- **Long-Term Memory** — Local storage via mem0 for cross-day trend comparison
- **GitHub Copilot LLM** — Access token → session token exchange via private protocol, default model `claude-sonnet-4.6`

## Workflow Topology

```
START
  │
  ▼
[inspect] ─── Grafana data collection (Dashboard + Alert)
  │
  ├──────────────┐
  ▼              ▼
[summarize]  [jvm_report]    ← parallel execution
  │              │
  └──────┬───────┘
         ▼
   [build_report] ─── Format plain text / HTML email / JVM email
         │
         ▼
     [notify] ─── Email + Teams delivery
         │
         ▼
        END
```

## Project Structure

```
src/grafana_agent_langgraph/
├── main.py              # CLI entry point, preflight checks + workflow launch
├── workflow.py           # LangGraph StateGraph definition (5 nodes)
├── grafana_client.py     # Grafana API async client (Dashboard / Alert / Metrics)
├── llm_client.py         # GitHub Copilot LLM client (token exchange + analysis)
├── report_generator.py   # Report formatting (plain text / HTML email / Teams card)
├── notifier.py           # Multi-channel notification (Email + Teams)
├── config.py             # Pydantic config models (YAML + env var override)
├── runtime.py            # Config loading and startup validation
└── logger.py             # Unified logging setup
```

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Create config file
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your Grafana URL, API Key, etc.

# 3. Set sensitive info (recommended via .env or environment variables)
export COPILOT_ACCESS_TOKEN="ghu_xxx"
export SMTP_USER="your-smtp-user"
export SMTP_PASSWORD="your-smtp-password"

# 4. Run
uv run python -m grafana_agent_langgraph.main
# Or use the CLI entry point
uv run grafana-agent-langgraph
```

You can also use a `.env` file with `uv run --env-file .env grafana-agent-langgraph`.

## Configuration

Supports a **YAML config file + environment variable override** dual-layer mechanism. Environment variables take precedence over YAML.

### Config File Path

Config files are resolved in the following order (highest to lowest priority):

1. Path specified by `GRAFANA_AGENT_CONFIG` / `APP_CONFIG_PATH` / `CONFIG_PATH` env vars
2. `config/config.yaml`
3. `config/config.example.yaml`

### Environment Variables

#### Required

| Variable | Description |
|----------|-------------|
| `GRAFANA_URL` | Grafana instance URL |
| `GRAFANA_API_KEY` | Grafana API Key (Service Account Token) |
| `COPILOT_ACCESS_TOKEN` | GitHub Access Token (exchanged for Copilot Session Token) |

#### LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `github_copilot` | LLM provider (only github_copilot supported) |
| `LLM_MODEL` | `claude-sonnet-4.6` | Model name |
| `COPILOT_API_BASE` | `https://api.githubcopilot.com` | Copilot API base URL |
| `COPILOT_TOKEN_URL` | `https://api.github.com/copilot_internal/v2/token` | Session token exchange endpoint |
| `COPILOT_EDITOR_VERSION` | `vscode/1.99.0` | Editor-Version header |
| `COPILOT_EDITOR_PLUGIN_VERSION` | `copilot-chat/0.26.7` | Editor-Plugin-Version header |
| `COPILOT_USER_AGENT` | `GitHubCopilotChat/0.26.7` | User-Agent header |
| `LLM_TEMPERATURE` | `0.1` | Generation temperature |
| `LLM_MAX_TOKENS` | `1000000` | Maximum token count |

#### Notification (sensitive fields recommended via env vars)

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | SMTP server host |
| `SMTP_PORT` | SMTP port (default 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password |
| `EMAIL_FROM` | Sender email address |
| `EMAIL_TO` | Recipient addresses (comma-separated) |
| `EMAIL_ENABLED` | Enable email notifications |
| `TEAMS_ENABLED` | Enable Teams notifications |
| `TEAMS_WEBHOOK_URL` | Teams Webhook URL |

#### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_TIMEOUT` | `30` | Grafana request timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Log level |
| `TIMEZONE` | `UTC` | Timezone |
| `LOOKBACK_HOURS` | `24` | Inspection lookback period (hours) |
| `LANGUAGE` | `zh` | Report language (`zh` / `en`) |

## Dependencies

| Package | Purpose |
|---------|---------|
| `aiohttp` | Async HTTP client (Copilot API + Grafana API) |
| `aiosmtplib` | Async SMTP email delivery |
| `langgraph` | StateGraph workflow orchestration |
| `langchain-core` | LangChain base framework |
| `pydantic` / `pydantic-settings` | Config data validation |
| `pyyaml` | YAML config parsing |
| `markdown` | Markdown → HTML conversion (email reports) |
| `python-dateutil` | Date/time handling |
| `email-validator` | Email address validation |

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Lint & Format
uv run ruff check src/
uv run ruff format src/

# Test
uv run pytest
```


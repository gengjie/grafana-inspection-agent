# Grafana Inspection Agent (LangGraph)

基于 **LangGraph StateGraph** 编排的 Grafana 自动化巡检系统。自动采集 Grafana Dashboard 面板指标与告警数据，通过 GitHub Copilot LLM 进行智能分析，生成巡检日报与 JVM 健康分析报告，并支持 Email / Teams 多渠道推送。

## 核心特性

- **Dashboard 自动巡检** — 并行采集所有面板指标数据，LLM 生成摘要
- **告警监控** — 告警规则、活跃告警、历史告警全量分析
- **DB/Kafka 面板健康分析** — 自动筛选数据库/Kafka 相关面板，分块并行 map 分析后在 collect 节点归并，作为 Dashboard 总结输入
- **慢查询 SQL 专项诊断** — 针对配置的数据库 Dashboard UID 列表生成独立慢查询诊断报告
- **JVM 健康分析报告** — 筛选 JVM 相关面板（Heap、GC、Thread、Metaspace 等），分块并行分析后执行 reduce 聚合生成最终专项报告
- **重启原因防误判约束** — JVM 诊断严格区分 OOM 明确信号与 K8s 调度/驱逐信号，避免把调度重启误判为内存问题
- **多渠道通知** — Email（aiosmtplib）+ Microsoft Teams（Webhook）
- **多语言** — 中文 / 英文报告
- **长期记忆** — 基于 mem0 本地存储，支持跨天趋势对比
- **GitHub Copilot LLM** — 使用 access token 交换 session token，通过私有协议调用，默认模型 `claude-sonnet-4.6`

## 工作流拓扑

```
START
  │
  ▼
[inspect] ─── 采集 Dashboard + Alert
  │
  ├──────────► [db_kafka_prepare] ─► [route_db_kafka_chunks]
  │                                  ├─(有 chunks)─► [db_kafka_chunk_worker x N] ─► [db_kafka_collect] ─► [dashboard_summary]
  │                                  └─(无 chunks)────────────────────────────────► [db_kafka_collect] ─► [dashboard_summary]
  │
  ├──────────► [alert_summary]
  │
  ├──────────► [slow_query_summary]
  │
  └──────────► [jvm_prepare] ─► [route_jvm_chunks]
                                     ├─(有 chunks)─► [jvm_chunk_worker x N] ─► [jvm_collect (LLM reduce 聚合)]
                                     └─(无 chunks)───────────────────────────► [jvm_collect]

[dashboard_summary] + [alert_summary] + [slow_query_summary] + [jvm_collect]
                                            └──────────────────────────────► [build_report] ─► [notify] ─► END
```

说明：
- DB/Kafka 分支采用图内 chunk map + collect 合并（`db_kafka_collect` 为确定性文本归并，不追加 LLM reduce 调用）。
- JVM 分支采用图内 chunk map，并在 `jvm_collect` 节点执行 LLM reduce 聚合，输出统一 JVM 报告。

## 项目结构

```
src/grafana_agent_langgraph/
├── main.py              # CLI 入口，预检查 + 启动工作流
├── workflow.py           # LangGraph StateGraph 定义（inspect + 并行分支 + map/collect 节点）
├── grafana_client.py     # Grafana API 异步客户端（Dashboard / Alert / Metrics）
├── llm_client.py         # GitHub Copilot LLM 传输客户端（Token 交换 + chat completion + chunk worker）
├── jvm_report.py # JVM 专项分析模块（分片规划 + reduce 聚合）
├── daily_report.py # 日报分析模块（Dashboard/Alert 总结 + 日报合成）
├── slow_query_report.py # 慢查询专项分析模块（目标看板选择 + SQL 诊断生成）
├── report_generator.py   # 报告格式化（纯文本 / HTML 邮件 / Teams 卡片）
├── notifier.py           # 多渠道通知发送（Email + Teams）
├── config.py             # Pydantic 配置模型（YAML + 环境变量覆盖）
├── runtime.py            # 配置加载与启动验证
└── logger.py             # 统一日志设置
```

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 创建配置文件
cp config/config.example.yaml config/config.yaml
# 编辑 config/config.yaml，填入 Grafana URL、API Key 等

# 3. 设置敏感信息（推荐通过 .env 或环境变量）
export COPILOT_ACCESS_TOKEN="ghu_xxx"
export SMTP_USER="your-smtp-user"
export SMTP_PASSWORD="your-smtp-password"

# 4. 运行
uv run python -m grafana_agent_langgraph.main
# 或使用 CLI 入口
uv run grafana-agent-langgraph
```

也可以使用 `.env` 文件：`uv run --env-file .env grafana-agent-langgraph`。

## 配置

支持 **YAML 配置文件 + 环境变量覆盖** 双层机制。环境变量优先级高于 YAML。

### 配置文件路径

按以下顺序查找配置文件（优先级从高到低）：

1. 环境变量 `GRAFANA_AGENT_CONFIG` / `APP_CONFIG_PATH` / `CONFIG_PATH` 指定的路径
2. `config/config.yaml`
3. `config/config.example.yaml`

### 环境变量

#### 必需

| 变量 | 说明 |
|------|------|
| `GRAFANA_URL` | Grafana 实例 URL |
| `GRAFANA_API_KEY` | Grafana API Key（Service Account Token） |
| `COPILOT_ACCESS_TOKEN` | GitHub Access Token（用于交换 Copilot Session Token） |

#### LLM 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `github_copilot` | LLM 提供者（仅支持 github_copilot） |
| `LLM_MODEL` | `claude-sonnet-4.6` | 模型名称 |
| `COPILOT_API_BASE` | `https://api.githubcopilot.com` | Copilot API 地址 |
| `COPILOT_TOKEN_URL` | `https://api.github.com/copilot_internal/v2/token` | Session Token 交换端点 |
| `COPILOT_EDITOR_VERSION` | `vscode/1.99.0` | Editor-Version 请求头 |
| `COPILOT_EDITOR_PLUGIN_VERSION` | `copilot-chat/0.26.7` | Editor-Plugin-Version 请求头 |
| `COPILOT_USER_AGENT` | `GitHubCopilotChat/0.26.7` | User-Agent 请求头 |
| `LLM_TEMPERATURE` | `0.1` | 生成温度 |
| `LLM_MAX_TOKENS` | `1000000` | 最大 Token 数 |
| `LLM_REQUEST_TIMEOUT` | `180` | Copilot 请求超时（秒） |
| `LLM_CHUNK_MAX_RETRIES` | `2` | 分片任务遇到瞬时错误时的重试次数 |
| `LLM_CHUNK_RETRY_BACKOFF_SECONDS` | `1.0` | 分片重试初始退避时间（秒） |
| `LLM_CHUNK_RETRY_MAX_BACKOFF_SECONDS` | `8.0` | 分片重试最大退避时间（秒） |

#### 通知配置（敏感信息推荐环境变量注入）

| 变量 | 说明 |
|------|------|
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | SMTP 端口（默认 587） |
| `SMTP_USER` | SMTP 用户名 |
| `SMTP_PASSWORD` | SMTP 密码 |
| `EMAIL_FROM` | 发件人地址 |
| `EMAIL_TO` | 收件人地址（逗号分隔） |
| `EMAIL_ENABLED` | 是否启用邮件通知 |
| `TEAMS_ENABLED` | 是否启用 Teams 通知 |
| `TEAMS_WEBHOOK_URL` | Teams Webhook URL |

#### 其他

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GRAFANA_TIMEOUT` | `30` | Grafana 请求超时（秒） |
| `GRAFANA_VERIFY_SSL` | `false` | 是否校验 Grafana TLS 证书 |
| `GRAFANA_CA_FILE` | _空_ | Grafana TLS 校验使用的自定义 CA 文件 |
| `GRAFANA_SLOW_QUERY_DASHBOARD_UIDS` | `aawp84s` | 慢查询专项诊断目标 Dashboard UID 列表（逗号分隔） |
| `GRAFANA_SLOW_QUERY_DASHBOARD_UID` | `aawp84s` | 兼容旧版的单 UID 配置（已废弃，建议迁移） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `TIMEZONE` | `UTC` | 时区 |
| `LOOKBACK_HOURS` | `24` | 巡检回溯时间（小时） |
| `LANGUAGE` | `zh` | 报告语言（`zh` / `en`） |

## 依赖

| 包 | 用途 |
|----|------|
| `aiohttp` | 异步 HTTP 客户端（Copilot API + Grafana API） |
| `aiosmtplib` | 异步 SMTP 邮件发送 |
| `langgraph` | StateGraph 工作流编排 |
| `langchain-core` | LangChain 基础框架 |
| `pydantic` / `pydantic-settings` | 配置数据验证 |
| `pyyaml` | YAML 配置解析 |
| `markdown` | Markdown → HTML 转换（邮件报告） |
| `python-dateutil` | 日期时间处理 |
| `email-validator` | 邮件地址验证 |

## 开发

```bash
# 安装开发依赖
uv sync --group dev

# Lint & Format
uv run ruff check src/
uv run ruff format src/

# 测试
uv run pytest
```

## 报告质量评估（自动化门禁）

已提供最小可用的报告质量自动评估能力，可用于 CI `test` stage 质量门禁：

1. 程序运行时可通过环境变量 `REPORT_EVAL_OUTPUT_DIR` 导出评估输入快照。
2. 使用 `grafana-agent-report-eval` 对报告进行结构、事实锚定、可行动性、不确定性处理、重启原因归因准确性（OOM vs 调度）打分。
3. 根据阈值返回退出码，直接作为 CI pass/fail 条件。

本地示例：

```bash
export REPORT_EVAL_OUTPUT_DIR=/tmp/report-eval
uv run grafana-agent-langgraph

uv run grafana-agent-report-eval \
  --report-file /tmp/report-eval/daily_report.txt \
  --dashboard-inspection-file /tmp/report-eval/dashboard_inspection.json \
  --alert-inspection-file /tmp/report-eval/alert_inspection.json \
  --output-file /tmp/report-eval/eval-result.json
```

详细方案见 [docs/report-quality-evaluation.zh-CN.md](docs/report-quality-evaluation.zh-CN.md)。

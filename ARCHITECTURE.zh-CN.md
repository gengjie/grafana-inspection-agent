# Grafana Inspection Agent 架构文档

## 1. 文档目标

本文档是独立于 README 的架构说明，聚焦系统的：

- 运行时组件划分
- 主流程编排（LangGraph）
- 关键调用时序
- 可靠性与扩展点

适用代码版本：当前仓库 `main` 工作区（2026-04-14）。

---

## 2. 系统概览

该系统是一个基于 LangGraph 的异步巡检代理，核心目标是对 Grafana 的 Dashboard 与 Alert 数据进行自动化采集、分析、报告生成与多渠道通知。

核心能力：

- 采集：并发采集 Dashboard/Panel 指标与 Alert 信息
- 分析：调用 GitHub Copilot 会话式接口生成巡检总结
- 报告：生成日报与 JVM 专项报告（含 Email HTML/Teams payload）
- 发送：通过 SMTP 与 Teams Webhook 分发

---

## 3. 代码模块映射

| 模块 | 主要职责 | 关键对象/函数 |
|---|---|---|
| `main.py` | 启动入口、预检查、驱动工作流 | `main()`, `_preflight_llm()` |
| `runtime.py` | 配置加载、路径解析、基础校验、日志初始化 | `load_config()`, `validate_*()` |
| `workflow.py` | 定义 LangGraph 状态图与节点逻辑 | `LangGraphDailyInspection`, `run_daily_langgraph()` |
| `grafana_client.py` | Grafana API 访问、面板指标查询、告警聚合 | `inspect_dashboards()`, `inspect_alerts()` |
| `llm_client.py` | Copilot token 交换、聊天补全、报告分析生成 | `preflight()`, `generate_*()` |
| `report_generator.py` | 报告格式化（文本/HTML/Teams） | `format_daily_report()`, `format_report_for_email()` |
| `notifier.py` | 发送邮件与 Teams 通知 | `send_report()`, `send_email()`, `send_teams()` |
| `config.py` | 配置模型定义、YAML + 环境变量覆盖 | `AppConfig`, `_ENV_OVERRIDES` |
| `logger.py` | 统一日志初始化与获取 | `setup_logger()`, `get_logger()` |

---

## 4. 运行流程图（Flowchart）

```mermaid
flowchart TD
    A[CLI 启动: main.cli] --> B[load_config + init_logger]
    B --> C[validate_base_config / validate_copilot_access_token]
    C --> D[LLM preflight]
    D -->|成功| E[run_daily_langgraph]
    D -->|失败| Z1[退出: return 1]

    E --> F[inspect 节点: Grafana 采集]
    F --> G[summarize 节点: 生成 Dashboard/Alert 总结]
    F --> H[jvm_report 节点: JVM 专项分析]
    G --> I[build_report 节点: 构建日报与邮件内容]
    H --> I
    I --> J[notify 节点: Email/Teams 发送]
    J --> K[日志输出 + return 0]
```

说明：

- `summarize` 与 `jvm_report` 从 `inspect` 后并行执行
- `build_report` 需要等待两条并行分支都完成
- `notify` 同时支持日报与 JVM 报告的二次发送

---

## 5. 组件图（Component Diagram）

```mermaid
flowchart LR
    subgraph App[Grafana Inspection Agent]
        M[main.py\nCLI Entry]
        R[runtime.py\nConfig & Validation]
        W[workflow.py\nLangGraph Orchestrator]
        GC[grafana_client.py\nGrafana Async Client]
        LC[llm_client.py\nCopilot LLM Client]
        RG[report_generator.py\nReport Formatter]
        N[notifier.py\nEmail/Teams Sender]
        C[config.py\nPydantic Settings]
        L[logger.py\nLogging]
    end

    M --> R
    R --> C
    M --> W
    W --> GC
    W --> LC
    W --> RG
    W --> N
    M --> L
    W --> L
    GC --> L
    LC --> L
    N --> L

    GC --> GRAFANA[(Grafana HTTP API)]
    LC --> COPILOT[(GitHub Copilot API)]
    N --> SMTP[(SMTP Server)]
    N --> TEAMS[(Microsoft Teams Webhook)]
```

边界划分：

- 编排层：`workflow.py`
- 领域适配层：`grafana_client.py`, `llm_client.py`, `notifier.py`
- 表达层：`report_generator.py`
- 基础设施层：`config.py`, `runtime.py`, `logger.py`

---

## 6. 核心时序图（Sequence Diagram）

### 6.1 日常巡检主链路

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant RT as runtime.py
    participant WF as workflow.py
    participant G as grafana_client.py
    participant LLM as llm_client.py
    participant RG as report_generator.py
    participant N as notifier.py
    participant ExtG as Grafana API
    participant ExtC as Copilot API
    participant SMTP as SMTP
    participant Teams as Teams Webhook

    CLI->>RT: load_config(), validate_*
    CLI->>LLM: preflight()
    LLM->>ExtC: token exchange + ping chat
    ExtC-->>LLM: session token + reply

    CLI->>WF: run_daily_langgraph(config)
    WF->>G: inspect_dashboards(lookback)
    G->>ExtG: /search, /dashboards/uid/*, /ds/query
    ExtG-->>G: dashboard/panel/metrics
    WF->>G: inspect_alerts(lookback)
    G->>ExtG: /ruler/*, /alertmanager/*
    ExtG-->>G: rules/instances/history

    par 并行分析
        WF->>LLM: generate_dashboard_summary(...)
        LLM->>ExtC: /chat/completions
        ExtC-->>LLM: dashboard summary
    and
        WF->>LLM: generate_alert_summary(...)
        LLM->>ExtC: /chat/completions
        ExtC-->>LLM: alert summary
    and
        WF->>LLM: generate_jvm_report(...)
        LLM->>ExtC: /chat/completions (chunked)
        ExtC-->>LLM: jvm report
    end

    WF->>RG: format_daily_report()
    WF->>RG: format_report_for_email()
    WF->>RG: format_jvm_report_for_email()

    WF->>N: send_report(daily)
    N->>SMTP: send_email(html + plain)
    N->>Teams: send_teams(payload)
    SMTP-->>N: result
    Teams-->>N: result

    opt jvm_report 非空
        WF->>N: send_report(jvm)
        N->>SMTP: send_email(jvm)
        N->>Teams: send_teams(jvm payload)
    end

    WF-->>CLI: return 0
```

### 6.2 Copilot Token 刷新时序

```mermaid
sequenceDiagram
    participant L as LLMClient
    participant GH as GitHub Token API
    participant CP as Copilot Chat API

    L->>L: _get_session_token(force_refresh=False)
    alt token 即将过期或不存在
        L->>GH: GET /copilot_internal/v2/token
        GH-->>L: token + expires_at/refresh_in
        L->>L: 缓存 token 与过期时间
    else token 有效
        L->>L: 复用缓存 token
    end

    L->>CP: POST /chat/completions
    alt 401/403
        L->>L: 强制刷新 token
        L->>GH: GET token
        GH-->>L: new token
        L->>CP: 重试一次
    end
```

---

## 7. 状态模型与数据流

`InspectionState`（`workflow.py`）在节点间传递，关键字段如下：

- 输入字段：`lookback_hours`
- 采集结果：`dashboard_inspection`, `alert_inspection`
- LLM结果：`dashboard_summary`, `alert_summary`, `jvm_report`
- 报告结果：`daily_report`, `email_subject`, `email_html`, `jvm_email_subject`, `jvm_email_html`
- 发送结果：`notify_results`

数据流可抽象为：

$$
S_{0}(lookback) \xrightarrow{inspect} S_{1}(dashboard, alert)
\xrightarrow{summarize \parallel jvm} S_{2}(summary, jvm)
\xrightarrow{build\_report} S_{3}(report, email)
\xrightarrow{notify} S_{4}(delivery\_result)
$$

---

## 8. 并发与性能设计

- Grafana 侧并发：
  - Dashboard 明细通过 `asyncio.gather` 并发拉取
  - Panel metrics 查询使用 `Semaphore(5)` 限流，避免压垮 Grafana
- LLM 侧并发：
  - DB/Kafka 与 JVM 分析均采用分块 + `Semaphore(3)` 并发请求
- 图编排并行：
  - `summarize` 与 `jvm_report` 为图级并行节点

---

## 9. 配置与启动约定

配置优先级（高到低）：

1. 环境变量 `GRAFANA_AGENT_CONFIG` / `APP_CONFIG_PATH` / `CONFIG_PATH` 指定路径
2. `config/config.yaml`
3. `config/config.example.yaml`

加载策略：

- 若 YAML 存在：`AppConfig.from_yaml()`，然后应用 `_ENV_OVERRIDES`
- 若 YAML 不存在：`AppConfig.from_env()`

这保证了“文件可读性 + 环境可覆盖”的部署弹性。

---

## 10. 容错与可观测性

- 启动前置校验：
  - Grafana URL/API Key 必填
  - Copilot Access Token 必填
  - Copilot preflight 失败则快速失败
- 网络调用容错：
  - Grafana/LLM/通知通道均在各自模块捕获异常并记录日志
  - LLM 在 401/403 场景自动刷新 token 并重试一次
- 日志：
  - 各模块通过统一 logger 记录节点执行、请求失败、发送结果

---

## 11. 扩展点

- 新分析分支：
  - 在 `workflow.py` 增加节点并接入 `build_report` 汇总
- 新通知渠道：
  - 在 `notifier.py` 增加 channel sender，并扩展 `send_report()` 返回值
- 新模型提供方：
  - 在 `llm_client.py` 抽象 provider adapter（当前仅 `github_copilot`）
- 持久记忆：
  - `config.py` 已定义 `MemoryConfig`，可在工作流中接入历史趋势总结

---

## 12. 架构小结

该项目采用“图编排 + 异步 I/O + 外部能力适配器”的设计：

- 用 LangGraph 清晰表达依赖与并行关系
- 用独立客户端隔离 Grafana/LLM/通知细节
- 用格式化层统一日报输出形态

整体结构清晰、可演进，适合继续向“多专项分析节点 + 多目标通知 + 长期趋势记忆”方向扩展。

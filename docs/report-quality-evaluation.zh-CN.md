# Report 质量评估自动化方案（最小可用 + 工业可扩展）

## 1. 目标与范围

本方案用于对 Grafana 巡检程序生成的日报做自动化质量评估，并在 GitLab CI 的 `test` stage 作为质量门禁执行。

当前落地版本目标：

1. 低接入成本：不依赖外部评估平台。
2. 可自动门禁：支持 CI 打分阈值失败即阻断。
3. 可审计：保留输入快照与评估结果 artifact。
4. 可扩展：后续可增加 LLM-as-a-Judge、多样本回归与趋势看板。

不包含：

1. 离线历史回放平台。
2. 复杂事实抽取模型。
3. 线上实时 A/B 分流评估。

## 2. 实现概览

已落地组件：

1. 运行时落盘评估输入
- 代码位置：[src/grafana_agent_langgraph/workflow.py](../src/grafana_agent_langgraph/workflow.py)
- 通过环境变量 `REPORT_EVAL_OUTPUT_DIR` 启用。
- 输出文件：
  - `daily_report.txt`
  - `jvm_report.txt`
  - `dashboard_inspection.json`
  - `alert_inspection.json`
  - `meta.json`

2. 评估器 CLI
- 代码位置：[src/grafana_agent_langgraph/report_quality_eval.py](../src/grafana_agent_langgraph/report_quality_eval.py)
- 命令：`grafana-agent-report-eval`
- 输出：评估 JSON（默认 `report-eval-result.json`）

3. GitLab CI 集成
- 配置位置：[.gitlab-ci.yml](../.gitlab-ci.yml)
- 在 `test` job 中：
  - 先执行主程序生成报告
  - 再执行评估 CLI 打分
  - 通过阈值控制 job 成功/失败
  - 上传评估 artifact 便于复盘

## 3. 评分模型（MVP）

当前采用确定性规则打分（可复现、可解释）：

1. 结构完整度（25%）
- 校验章节提示词（中英文自适应）
- 校验非空行数阈值

2. 事实锚定度（45%）
- 从输入 JSON 收集 dashboard/panel/alert 实体
- 统计报告中实体命中比例

3. 可行动性（20%）
- 建议类关键词命中（如 建议/排查/优化）
- 风险类关键词命中（如 严重/警告/风险）

4. 不确定性处理（10%）
- 统计源数据中缺失指标比例
- 当缺失比例高时，要求报告出现不确定性表达

总分公式：

$$
S = 0.25 S_{structure} + 0.45 S_{factual} + 0.20 S_{action} + 0.10 S_{uncertainty}
$$

默认门禁阈值：

1. `total >= 80`
2. `structure >= 85`
3. `factual_grounding >= 75`
4. `actionability >= 60`

## 4. CI 执行流程

`test` stage 手动触发时执行：

1. `grafana-agent-langgraph` 运行巡检并生成报告。
2. `workflow.py` 将评估输入落盘到 `${REPORT_EVAL_OUTPUT_DIR}`。
3. `grafana-agent-report-eval` 读取输入并打分。
4. 若任一门禁不达标，CLI 返回 `exit code 1`，job 失败。
5. 上传 `/tmp/report-eval/` 作为 artifact，供人工审查。

## 5. 本地运行示例

```bash
# 1) 运行主程序，并启用评估输入导出
export REPORT_EVAL_OUTPUT_DIR=/tmp/report-eval
uv run grafana-agent-langgraph

# 2) 执行质量评估
uv run grafana-agent-report-eval \
  --report-file /tmp/report-eval/daily_report.txt \
  --dashboard-inspection-file /tmp/report-eval/dashboard_inspection.json \
  --alert-inspection-file /tmp/report-eval/alert_inspection.json \
  --output-file /tmp/report-eval/eval-result.json
```

## 6. 结果解读

评估输出 JSON 关键字段：

1. `scores.total`：综合得分。
2. `scores.*`：分项得分。
3. `issues`：失败原因列表。
4. `failed_gates`：未通过门禁项。
5. `pass`：总体通过/失败。

建议审查顺序：

1. 先看 `failed_gates`。
2. 再看 `issues` 定位具体问题。
3. 必要时打开 artifact 中 `daily_report.txt` 与输入 JSON 复核。

## 7. 工业化扩展路线

### 阶段 A（当前）

1. 规则评估 + CI 门禁。
2. 单次运行质量评估。

### 阶段 B（建议 1-2 周）

1. 加入 LLM-as-a-Judge（二评模型，JSON 输出）。
2. 双通道打分：规则分 + 裁判分。
3. 启用多次采样中位数，降低评估抖动。

### 阶段 C（建议 2-4 周）

1. 固定回归样本集（20-50 个 case）。
2. 引入版本对比（当前分数 vs 基线分数）。
3. 在 MR 中展示分数变化与风险提示。

### 阶段 D（长期）

1. 接入 BI 看板（分数趋势、失败类型分布）。
2. 结合人工标注做阈值校准。
3. 建立质量 SLA（例如月度 P95 分数）。

## 8. 风险与对策

1. 规则误判
- 对策：引入裁判模型与人工抽检。

2. 关键词打分可被“模板化文本”投机
- 对策：增加实体覆盖率与一致性校验权重。

3. 模型输出波动
- 对策：关键 case 采用多次运行取中位数。

4. 数据缺失导致评分偏低
- 对策：不确定性维度单独建模并给容错。

## 9. 验收标准

满足以下条件可视为 MVP 验收通过：

1. `test` job 可手动触发并稳定执行。
2. 评估结果 JSON 可生成且字段完整。
3. 门禁阈值生效，低质量报告会失败。
4. artifact 可追溯输入与输出。

## 10. 变更清单

1. [src/grafana_agent_langgraph/workflow.py](../src/grafana_agent_langgraph/workflow.py)
- 新增 `_dump_report_eval_artifacts`。
- 在 workflow 结束阶段落盘评估输入。

2. [src/grafana_agent_langgraph/report_quality_eval.py](../src/grafana_agent_langgraph/report_quality_eval.py)
- 新增质量评估 CLI 实现。

3. [pyproject.toml](../pyproject.toml)
- 新增脚本入口 `grafana-agent-report-eval`。

4. [.gitlab-ci.yml](../.gitlab-ci.yml)
- `test` job 增加评估执行与 artifacts 上传。
- `needs` 使用 `optional` 兼容构建 job 未入图场景。

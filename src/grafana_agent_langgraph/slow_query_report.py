"""Slow-query SQL diagnosis service decoupled from transport client."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .logger import get_logger

logger = get_logger("slow_query_report")


class SlowQueryReport:
    """Generate dedicated slow-query SQL diagnosis via injected chat callable."""

    def __init__(
        self,
        *,
        language: str,
        max_tokens: int,
        chat_completion: Callable[..., Awaitable[str]],
        slow_query_dashboard_uids: list[str],
    ) -> None:
        self.language = language
        self.max_tokens = max_tokens
        self.chat_completion = chat_completion
        self.slow_query_dashboard_uids = [uid.strip() for uid in slow_query_dashboard_uids if uid.strip()]

    async def generate_slow_query_sql_summary(
        self,
        inspection_data: dict[str, Any],
        grafana_base_url: str | None = None,
    ) -> str:
        """Generate focused slow-query SQL diagnosis based on target dashboard data."""
        target_dashboard = self._extract_slow_query_dashboard(inspection_data)
        if not target_dashboard:
            uid_text = ", ".join(self.slow_query_dashboard_uids)
            return (
                f"未在本次巡检数据中找到慢查询诊断目标看板（UID 列表: {uid_text}），无法生成慢查询 SQL 专项诊断。"
                if self.language != "en"
                else f"Target slow-query dashboards (UIDs: {uid_text}) were not found in this inspection result."
            )

        dash_path = target_dashboard.get("url") or ""
        full_url = ""
        if dash_path and grafana_base_url:
            full_url = f"{grafana_base_url.rstrip('/')}{dash_path}"
        elif dash_path:
            full_url = dash_path

        panel_snapshot = self._format_slow_query_panels_for_prompt(target_dashboard)

        if self.language == "en":
            prompt = f"""Please generate a professional slow-query SQL diagnostic report for the dashboard below.

Target dashboard:
- UID: {target_dashboard.get('uid')}
- Title: {target_dashboard.get('title')}
- URL: {full_url or 'N/A'}

Inspection window:
- Start: {inspection_data['lookback_period']['start']}
- End: {inspection_data['lookback_period']['end']}

Dashboard panel evidence:
{panel_snapshot}

Requirements:
1. Focus on SQL slow-query diagnostics and probable root causes.
2. Include explicit evidence references from provided panels/metrics.
3. Prioritize findings with severity (P1/P2/P3).
4. Provide actionable optimization advice at SQL/index/schema/app/connection-pool levels.
5. Add a validation plan with measurable success criteria (latency/QPS/error-rate/rows-scanned etc.).
6. If evidence is insufficient, clearly state uncertainty and required additional data.

Output sections:
- Executive Summary
- Key Findings (with evidence)
- Root Cause Analysis
- Optimization Recommendations (prioritized)
- Verification Plan
"""
            system_prompt = (
                "You are a senior database performance engineer specializing in MySQL/PostgreSQL slow query analysis."
            )
        else:
            prompt = f"""请基于以下 dashboard 数据生成专业的慢查询 SQL 诊断报告。

目标 dashboard：
- UID: {target_dashboard.get('uid')}
- 标题: {target_dashboard.get('title')}
- URL: {full_url or 'N/A'}

巡检窗口：
- 开始: {inspection_data['lookback_period']['start']}
- 结束: {inspection_data['lookback_period']['end']}

看板面板证据：
{panel_snapshot}

要求：
1. 聚焦 SQL 慢查询诊断与根因推断。
2. 所有关键结论都要引用面板/指标证据。
3. 按优先级输出问题（P1/P2/P3）。
4. 给出可执行优化建议，至少覆盖 SQL、索引、表结构、应用侧、连接池/并发控制。
5. 给出验证计划与可量化验收指标（如 P95/P99 延迟、QPS、错误率、扫描行数等）。
6. 对证据不足之处明确标注不确定性，并提出补充采集建议。

输出结构：
- 一、诊断结论摘要
- 二、关键问题与证据
- 三、根因分析
- 四、优化建议（按优先级）
- 五、验证与回归方案
"""
            system_prompt = "你是一位资深数据库性能专家，擅长 MySQL/PostgreSQL 慢查询诊断与优化。"

        try:
            content = await self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            return content
        except Exception as e:
            logger.error("Failed to generate slow-query SQL summary: %s", e, exc_info=True)
            raise

    def _extract_slow_query_dashboard(self, inspection_data: dict[str, Any]) -> dict[str, Any] | None:
        dashboards = inspection_data.get("dashboards") or []
        uid_set = {uid.lower() for uid in self.slow_query_dashboard_uids}
        for dash in dashboards:
            if (dash.get("uid") or "").strip().lower() in uid_set:
                return dash

        keywords = ("slow", "慢查询", "database", "sql", "reader")
        for dash in dashboards:
            haystack = " ".join(
                [
                    str(dash.get("title") or ""),
                    str(dash.get("url") or ""),
                ]
            ).lower()
            if any(kw in haystack for kw in keywords):
                return dash
        return None

    def _format_slow_query_panels_for_prompt(self, dashboard: dict[str, Any]) -> str:
        panels = dashboard.get("panels") or []
        if not panels:
            return "No panel data found." if self.language == "en" else "未发现可用面板数据。"

        lines: list[str] = []
        for idx, panel in enumerate(panels[:40], start=1):
            title = panel.get("title") or "Untitled"
            panel_type = panel.get("type") or "unknown"
            semantic = panel.get("semantic_description") or ""
            metrics = panel.get("metrics") or {}
            metric_status = metrics.get("status") or "unknown"

            target_exprs: list[str] = []
            for target in panel.get("targets") or []:
                expr = target.get("rawSql") or target.get("query") or target.get("expr")
                if expr:
                    text = str(expr).replace("\n", " ").strip()
                    if text:
                        target_exprs.append(text[:200])
            targets_preview = "; ".join(target_exprs[:3]) if target_exprs else "N/A"

            top_series = []
            for ser in (metrics.get("series") or [])[:2]:
                stats = ser.get("stats") or {}
                top_series.append(
                    f"{ser.get('name') or ser.get('refId')}: latest={stats.get('latest')}, avg={stats.get('avg')}, max={stats.get('max')}"
                )
            series_preview = " | ".join(top_series) if top_series else "N/A"

            lines.append(
                f"{idx}. panel={title} (type={panel_type}, status={metric_status})\\n"
                f"   semantic={semantic}\\n"
                f"   targets={targets_preview}\\n"
                f"   series={series_preview}"
            )

        if len(panels) > 40:
            lines.append(
                f"... omitted {len(panels) - 40} additional panels"
                if self.language == "en"
                else f"... 其余 {len(panels) - 40} 个面板省略"
            )
        return "\\n".join(lines)

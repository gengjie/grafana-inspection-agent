"""Daily report LLM analysis service decoupled from transport client."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .logger import get_logger

logger = get_logger("daily_report")


class DailyReport:
    """Generate dashboard, alert and daily report summaries via injected chat callable."""

    def __init__(
        self,
        *,
        language: str,
        max_tokens: int,
        model: str,
        chat_completion: Callable[..., Awaitable[str]],
    ) -> None:
        self.language = language
        self.max_tokens = max_tokens
        self.model = model
        self.chat_completion = chat_completion

    async def generate_dashboard_summary(
        self,
        inspection_data: dict[str, Any],
        db_kafka_analysis: str | None = None,
    ) -> str:
        if db_kafka_analysis is None:
            db_kafka_analysis = (
                "DB/Kafka panel analysis not available."
                if self.language == "en"
                else "数据库/Kafka面板分析不可用。"
            )

        if self.language == "en":
            prompt = f"""Please generate a concise summary report based on the following Grafana Dashboard inspection data, and include the DB/Kafka panel health analysis below.

Inspection time range: {inspection_data['lookback_period']['start']} to {inspection_data['lookback_period']['end']}

Dashboard statistics:
- Total Dashboards: {inspection_data['summary']['total_dashboards']}
- Total Panels: {inspection_data['summary']['total_panels']}

Dashboard list:
{self._format_dashboards_for_prompt(inspection_data['dashboards'])}

Database & Kafka panel health analysis:
{db_kafka_analysis}

Requirements:
1. Use formal and rigorous language
2. Summarize concisely, avoid verbosity
3. Highlight key information and statistics
4. Output in English
5. Clear format with appropriate paragraph separation
6. Integrate the DB/Kafka panel health analysis into the summary

Please generate the Dashboard inspection summary:"""
            system_prompt = "You are a professional DevOps engineer, skilled in analyzing and summarizing monitoring system status."
        else:
            prompt = f"""请根据以下Grafana Dashboard巡检数据，生成一份简洁的总结报告，并包含数据库/Kafka主面板的健康分析。

巡检时间范围：{inspection_data['lookback_period']['start']} 至 {inspection_data['lookback_period']['end']}

Dashboard统计信息：
- 总Dashboard数量：{inspection_data['summary']['total_dashboards']}
- 总Panel数量：{inspection_data['summary']['total_panels']}

Dashboard列表：
{self._format_dashboards_for_prompt(inspection_data['dashboards'])}

数据库/Kafka面板健康分析：
{db_kafka_analysis}

要求：
1. 使用正式、严谨的语言
2. 总结性描述，避免冗长
3. 突出关键信息和统计数据
4. 使用中文输出
5. 格式清晰，使用适当的段落分隔
6. 将数据库/Kafka面板健康分析融合进总结

请生成Dashboard巡检总结："""
            system_prompt = "你是一位专业的运维工程师，擅长分析和总结监控系统的运行状态。"

        try:
            logger.debug("Generating dashboard summary with model: %s", self.model)
            content = await self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug("Dashboard summary generated, length: %s", len(content))
            return content
        except Exception as e:
            logger.error("Failed to generate dashboard summary: %s", e, exc_info=True)
            raise

    async def generate_alert_summary(self, inspection_data: dict[str, Any]) -> str:
        if self.language == "en":
            prompt = f"""Please generate a detailed alert summary report based on the following Grafana Alerting inspection data.

Inspection time range: {inspection_data['lookback_period']['start']} to {inspection_data['lookback_period']['end']}

Alert statistics:
- Total Alert Rules: {inspection_data['summary']['total_rules']}
- Current Active Alerts: {inspection_data['summary']['active_alerts_count']}
- Alerts Triggered During Period: {inspection_data['summary']['alerts_in_period']}
- Current Firing Alerts: {inspection_data['summary']['firing_alerts']}

Alert history details:
{self._format_alerts_for_prompt(inspection_data['alert_history'])}

Current active alerts:
{self._format_active_alerts_for_prompt(inspection_data['active_alerts'])}

Requirements:
1. Use formal and rigorous language
2. Provide detailed description for each alert, including alert name, trigger time, status, rule information, etc.
3. Specifically mark alerts in Firing state
4. Organize content by importance or chronological order
5. Output in English
6. Clear format with appropriate paragraphs and lists

Please generate the detailed alert inspection report:"""
            system_prompt = "You are a professional DevOps engineer, skilled in analyzing and summarizing alert system status, able to describe alert triggers and impacts in detail."
        else:
            prompt = f"""请根据以下Grafana Alerting巡检数据，生成一份详细的告警总结报告。

巡检时间范围：{inspection_data['lookback_period']['start']} 至 {inspection_data['lookback_period']['end']}

告警统计信息：
- 总告警规则数：{inspection_data['summary']['total_rules']}
- 当前活跃告警数：{inspection_data['summary']['active_alerts_count']}
- 巡检期间触发告警数：{inspection_data['summary']['alerts_in_period']}
- 当前Firing状态告警数：{inspection_data['summary']['firing_alerts']}

告警历史详情：
{self._format_alerts_for_prompt(inspection_data['alert_history'])}

当前活跃告警：
{self._format_active_alerts_for_prompt(inspection_data['active_alerts'])}

要求：
1. 使用正式、严谨的语言
2. 对每个告警进行明细性描述，包括告警名称、触发时间、状态、规则信息等
3. 对Firing状态的告警要特别标注
4. 按重要性或时间顺序组织内容
5. 使用中文输出
6. 格式清晰，使用适当的段落和列表

请生成告警巡检详细报告："""
            system_prompt = "你是一位专业的运维工程师，擅长分析和总结告警系统的运行状态，能够详细描述告警的触发原因和影响。"

        try:
            logger.debug("Generating alert summary with model: %s", self.model)
            content = await self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug("Alert summary generated, length: %s", len(content))
            return content
        except Exception as e:
            logger.error("Failed to generate alert summary: %s", e, exc_info=True)
            raise

    async def generate_daily_report(self, dashboard_summary: str, alert_summary: str) -> str:
        if self.language == "en":
            prompt = f"""Please generate a complete daily inspection report based on the following Dashboard and Alert summaries.

Dashboard inspection summary:
{dashboard_summary}

Alert inspection summary:
{alert_summary}

Requirements:
1. Use formal and professional report style
2. Include clear sections and concise conclusions
3. Highlight major risks and actionable recommendations
4. Output in English

Please generate the final daily report:"""
            system_prompt = "You are a professional DevOps engineer, skilled in writing formal technical reports and daily reports."
        else:
            prompt = f"""请基于以下Dashboard巡检总结和告警巡检总结，生成完整的日报。

Dashboard巡检总结：
{dashboard_summary}

告警巡检总结：
{alert_summary}

要求：
1. 使用正式、专业的报告风格
2. 结构清晰，结论简明
3. 突出主要风险和可执行建议
4. 使用中文输出

请生成最终日报："""
            system_prompt = "你是一位专业的运维工程师，擅长撰写正式技术报告和日报。"

        try:
            logger.debug("Generating daily report with model: %s", self.model)
            content = await self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug("Daily report generated, length: %s", len(content))
            return content
        except Exception as e:
            logger.error("Failed to generate daily report: %s", e, exc_info=True)
            raise

    def _format_dashboards_for_prompt(self, dashboards: list[dict[str, Any]]) -> str:
        lines = []
        for dash in dashboards[:50]:
            lines.append(
                f"- {dash.get('title', 'Unknown')} (UID: {dash.get('uid')}, "
                f"Panels: {dash.get('panel_count', 0)})"
            )
        if len(dashboards) > 50:
            lines.append(f"... 还有 {len(dashboards) - 50} 个Dashboard未列出")
        return "\n".join(lines)

    def _format_alerts_for_prompt(self, alert_history: list[dict[str, Any]]) -> str:
        if not alert_history:
            return "巡检期间无告警触发记录。"

        lines = []
        for alert in alert_history[:30]:
            instance = alert.get("instance", {})
            rule = alert.get("rule", {})
            labels = instance.get("labels", {})
            annotations = instance.get("annotations", {})

            lines.append(
                f"- 告警名称: {labels.get('alertname', 'Unknown')}\n"
                f"  触发时间: {alert.get('starts_at', 'Unknown')}\n"
                f"  状态: {alert.get('status', 'unknown')}\n"
                f"  规则: {rule.get('name', 'Unknown') if rule else 'Unknown'}\n"
                f"  描述: {annotations.get('description', annotations.get('summary', 'N/A'))}\n"
            )

        if len(alert_history) > 30:
            lines.append(f"\n... 还有 {len(alert_history) - 30} 条告警记录未列出")

        return "\n".join(lines)

    def _format_active_alerts_for_prompt(self, active_alerts: list[dict[str, Any]]) -> str:
        if not active_alerts:
            return "当前无活跃告警。"

        lines = []
        for alert in active_alerts[:30]:
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            status = alert.get("status", {})

            lines.append(
                f"- 告警名称: {labels.get('alertname', 'Unknown')}\n"
                f"  状态: {status.get('state', 'unknown')}\n"
                f"  开始时间: {alert.get('startsAt', 'Unknown')}\n"
                f"  描述: {annotations.get('description', annotations.get('summary', 'N/A'))}\n"
            )

        if len(active_alerts) > 30:
            lines.append(f"\n... 还有 {len(active_alerts) - 30} 个活跃告警未列出")

        return "\n".join(lines)

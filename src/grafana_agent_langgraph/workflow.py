"""LangGraph workflow implementation for daily Grafana inspection."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import AppConfig
from .grafana_client import GrafanaClient
from .llm_client import LLMClient
from .notifier import Notifier
from .report_generator import ReportGenerator


class InspectionState(TypedDict, total=False):
    """Mutable state that flows through LangGraph nodes."""

    lookback_hours: int
    dashboard_inspection: dict[str, Any]
    alert_inspection: dict[str, Any]
    dashboard_summary: str
    alert_summary: str
    jvm_report: str
    daily_report: str
    email_subject: str
    email_html: str
    jvm_email_subject: str
    jvm_email_html: str
    notify_results: dict[str, bool]


class LangGraphDailyInspection:
    """Daily inspection flow based on LangGraph StateGraph."""

    def __init__(self, config: AppConfig, logger):
        self.config = config
        self.logger = logger
        self.grafana_client = GrafanaClient(
            url=config.grafana.url,
            api_key=config.grafana.api_key,
            timeout=config.grafana.timeout,
        )
        self.llm_client = LLMClient(
            access_token=config.llm.access_token,
            model=config.llm.model,
            api_base=config.llm.api_base,
            token_url=config.llm.token_url,
            editor_version=config.llm.editor_version,
            editor_plugin_version=config.llm.editor_plugin_version,
            user_agent=config.llm.user_agent,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            language=config.language,
        )
        self.report_generator = ReportGenerator()
        self.notifier = Notifier(
            email_config=config.notification.email,
            teams_config=config.notification.teams,
        )

    async def inspect_node(self, state: InspectionState) -> InspectionState:
        """Fetch dashboards and alerts from Grafana."""
        self.logger.info("Running node: inspect")
        lookback = int(state.get("lookback_hours", self.config.lookback_hours))

        dashboards = await self.grafana_client.inspect_dashboards(lookback_hours=lookback)
        alerts = await self.grafana_client.inspect_alerts(lookback_hours=lookback)
        return {
            "dashboard_inspection": dashboards,
            "alert_inspection": alerts,
        }

    async def summarize_node(self, state: InspectionState) -> InspectionState:
        """Generate dashboard/alert summaries through LLM."""
        self.logger.info("Running node: summarize")
        dashboard_summary = await self.llm_client.generate_dashboard_summary(
            state["dashboard_inspection"]
        )
        alert_summary = await self.llm_client.generate_alert_summary(state["alert_inspection"])

        return {
            "dashboard_summary": dashboard_summary,
            "alert_summary": alert_summary,
        }

    async def jvm_report_node(self, state: InspectionState) -> InspectionState:
        """Generate JVM health analysis report from dashboard data."""
        self.logger.info("Running node: jvm_report")
        jvm_report = await self.llm_client.generate_jvm_report(state["dashboard_inspection"])
        return {"jvm_report": jvm_report}

    async def build_report_node(self, state: InspectionState) -> InspectionState:
        """Build final report and email HTML."""
        self.logger.info("Running node: build_report")

        inspection_time = state["dashboard_inspection"].get("inspection_time")
        daily_report = self.report_generator.format_daily_report(
            dashboard_summary=state["dashboard_summary"],
            alert_summary=state["alert_summary"],
            inspection_time=inspection_time,
            language=self.config.language,
        )
        email_subject, email_html = self.report_generator.format_report_for_email(
            daily_report,
            language=self.config.language,
        )

        # Build JVM report email content
        jvm_report = state.get("jvm_report", "")
        jvm_email_subject, jvm_email_html = self.report_generator.format_jvm_report_for_email(
            jvm_report=jvm_report,
            inspection_time=inspection_time,
            language=self.config.language,
        )

        return {
            "daily_report": daily_report,
            "email_subject": email_subject,
            "email_html": email_html,
            "jvm_email_subject": jvm_email_subject,
            "jvm_email_html": jvm_email_html,
        }

    async def notify_node(self, state: InspectionState) -> InspectionState:
        """Dispatch report to enabled channels."""
        self.logger.info("Running node: notify")

        if self.config.notification.email.enabled or self.config.notification.teams.enabled:
            notify_results = await self.notifier.send_report(
                report=state["daily_report"],
                dashboard_summary=state["dashboard_summary"],
                alert_summary=state["alert_summary"],
                email_subject=state["email_subject"],
                email_html=state["email_html"],
                grafana_url=self.config.grafana.url,
                language=self.config.language,
            )

            # Send JVM report as a separate email/teams message
            jvm_report = state.get("jvm_report", "")
            if jvm_report:
                jvm_results = await self.notifier.send_report(
                    report=jvm_report,
                    dashboard_summary=jvm_report,
                    alert_summary="",
                    email_subject=state.get("jvm_email_subject", ""),
                    email_html=state.get("jvm_email_html", ""),
                    grafana_url=self.config.grafana.url,
                    language=self.config.language,
                )
                notify_results["jvm_email"] = jvm_results.get("email", False)
                notify_results["jvm_teams"] = jvm_results.get("teams", False)
        else:
            notify_results = {"email": False, "teams": False}
            self.logger.info("No notification channel enabled, skip notify")

        return {"notify_results": notify_results}

    def compile(self):
        """Compile the graph topology."""
        graph = StateGraph(InspectionState)
        graph.add_node("inspect", self.inspect_node)
        graph.add_node("summarize", self.summarize_node)
        graph.add_node("jvm_report", self.jvm_report_node)
        graph.add_node("build_report", self.build_report_node)
        graph.add_node("notify", self.notify_node)

        graph.add_edge(START, "inspect")
        # After inspect, run summarize and jvm_report in parallel
        graph.add_edge("inspect", "summarize")
        graph.add_edge("inspect", "jvm_report")
        # Both must finish before build_report
        graph.add_edge("summarize", "build_report")
        graph.add_edge("jvm_report", "build_report")
        graph.add_edge("build_report", "notify")
        graph.add_edge("notify", END)
        return graph.compile()


async def run_daily_langgraph(config: AppConfig, logger) -> int:
    """Public entrypoint for LangGraph workflow mode."""
    app = LangGraphDailyInspection(config, logger).compile()
    result = await app.ainvoke({"lookback_hours": config.lookback_hours})

    report = result.get("daily_report", "")
    if report:
        logger.info("\n%s\n%s\n%s", "=" * 80, report, "=" * 80)

    jvm_report = result.get("jvm_report", "")
    if jvm_report:
        logger.info(
            "\n%s\nJVM Health Analysis Report\n%s\n%s\n%s",
            "=" * 80, "=" * 80, jvm_report, "=" * 80,
        )

    logger.info("Inspection completed successfully (LangGraph mode)")
    return 0

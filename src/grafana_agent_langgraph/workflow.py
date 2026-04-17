"""LangGraph workflow implementation for daily Grafana inspection."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

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
    db_kafka_chunk_jobs: list[dict[str, Any]]
    db_kafka_chunk_job: dict[str, Any]
    db_kafka_chunk_results: Annotated[list[str], operator.add]
    db_kafka_analysis: str
    jvm_chunk_jobs: list[dict[str, Any]]
    jvm_chunk_job: dict[str, Any]
    jvm_chunk_results: Annotated[list[str], operator.add]
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

    async def db_kafka_analysis_node(self, state: InspectionState) -> InspectionState:
        """Prepare DB/Kafka chunk jobs for graph-level map scheduling."""
        self.logger.info("Running node: db_kafka_prepare")
        jobs, fallback_text = self.llm_client.prepare_db_kafka_chunk_jobs(
            state["dashboard_inspection"]
        )
        if fallback_text is not None:
            return {
                "db_kafka_analysis": fallback_text,
                "db_kafka_chunk_jobs": [],
            }
        for idx, job in enumerate(jobs, start=1):
            job["chunk_index"] = idx
        return {
            "db_kafka_chunk_jobs": jobs,
            "db_kafka_chunk_results": [],
        }

    async def db_kafka_chunk_worker_node(self, state: InspectionState) -> InspectionState:
        """Execute one DB/Kafka chunk subagent."""
        job = state.get("db_kafka_chunk_job") or {}
        result = await self.llm_client.run_chunk_job(job)
        return {"db_kafka_chunk_results": [result]}

    async def db_kafka_collect_node(self, state: InspectionState) -> InspectionState:
        """Reduce DB/Kafka chunk outputs into a single analysis text."""
        if state.get("db_kafka_analysis"):
            return {"db_kafka_analysis": state["db_kafka_analysis"]}

        merged = "\n\n".join(r for r in (state.get("db_kafka_chunk_results") or []) if r)
        if not merged:
            merged = (
                "Failed to generate DB/Kafka panel analysis."
                if self.config.language == "en"
                else "数据库/Kafka 面板分析生成失败。"
            )
        return {"db_kafka_analysis": merged}

    async def dashboard_summary_node(self, state: InspectionState) -> InspectionState:
        """Generate dashboard summary, reusing DB/Kafka subagent analysis."""
        self.logger.info("Running node: dashboard_summary")
        dashboard_summary = await self.llm_client.generate_dashboard_summary(
            state["dashboard_inspection"],
            db_kafka_analysis=state.get("db_kafka_analysis"),
        )
        return {"dashboard_summary": dashboard_summary}

    async def alert_summary_node(self, state: InspectionState) -> InspectionState:
        """Generate alert summary through LLM."""
        self.logger.info("Running node: alert_summary")
        alert_summary = await self.llm_client.generate_alert_summary(state["alert_inspection"])

        return {"alert_summary": alert_summary}

    async def jvm_prepare_node(self, state: InspectionState) -> InspectionState:
        """Prepare JVM chunk jobs for graph-level map scheduling."""
        self.logger.info("Running node: jvm_prepare")
        jobs, fallback_text = self.llm_client.prepare_jvm_chunk_jobs(
            state["dashboard_inspection"]
        )
        if fallback_text is not None:
            return {
                "jvm_report": fallback_text,
                "jvm_chunk_jobs": [],
            }
        for idx, job in enumerate(jobs, start=1):
            job["chunk_index"] = idx
        return {
            "jvm_chunk_jobs": jobs,
            "jvm_chunk_results": [],
        }

    async def jvm_chunk_worker_node(self, state: InspectionState) -> InspectionState:
        """Execute one JVM chunk subagent."""
        job = state.get("jvm_chunk_job") or {}
        result = await self.llm_client.run_chunk_job(job)
        return {"jvm_chunk_results": [result]}

    async def jvm_collect_node(self, state: InspectionState) -> InspectionState:
        """Reduce JVM chunk outputs into a single JVM report."""
        if state.get("jvm_report"):
            return {"jvm_report": state["jvm_report"]}

        merged = await self.llm_client.reduce_jvm_chunk_results(
            state.get("jvm_chunk_results") or [],
            state.get("dashboard_inspection"),
        )
        return {"jvm_report": merged}

    def route_db_kafka_chunks(self, state: InspectionState):
        """Route DB/Kafka branch: direct pass-through or dynamic map fan-out."""
        if state.get("db_kafka_analysis"):
            return "db_kafka_collect"

        jobs = state.get("db_kafka_chunk_jobs") or []
        if not jobs:
            return "db_kafka_collect"

        return [Send("db_kafka_chunk_worker", {"db_kafka_chunk_job": job}) for job in jobs]

    def route_jvm_chunks(self, state: InspectionState):
        """Route JVM branch: direct pass-through or dynamic map fan-out."""
        if state.get("jvm_report"):
            return "jvm_collect"

        jobs = state.get("jvm_chunk_jobs") or []
        if not jobs:
            return "jvm_collect"

        return [Send("jvm_chunk_worker", {"jvm_chunk_job": job}) for job in jobs]

    async def build_report_node(self, state: InspectionState) -> InspectionState:
        """Build final report and email HTML."""
        self.logger.info("Running node: build_report")

        inspection_time = state["dashboard_inspection"].get("inspection_time")
        dashboard_summary = state.get("dashboard_summary") or (
            "Dashboard summary not available."
            if self.config.language == "en"
            else "Dashboard 摘要不可用。"
        )
        alert_summary = state.get("alert_summary") or (
            "Alert summary not available."
            if self.config.language == "en"
            else "告警摘要不可用。"
        )
        daily_report = self.report_generator.format_daily_report(
            dashboard_summary=dashboard_summary,
            alert_summary=alert_summary,
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
        graph.add_node("db_kafka_prepare", self.db_kafka_analysis_node)
        graph.add_node("db_kafka_chunk_worker", self.db_kafka_chunk_worker_node)
        graph.add_node("db_kafka_collect", self.db_kafka_collect_node)
        graph.add_node("dashboard_summary", self.dashboard_summary_node)
        graph.add_node("alert_summary", self.alert_summary_node)
        graph.add_node("jvm_prepare", self.jvm_prepare_node)
        graph.add_node("jvm_chunk_worker", self.jvm_chunk_worker_node)
        graph.add_node("jvm_collect", self.jvm_collect_node)
        graph.add_node("build_report", self.build_report_node)
        graph.add_node("notify", self.notify_node)

        graph.add_edge(START, "inspect")
        # After inspect, run branch analyses in parallel
        graph.add_edge("inspect", "db_kafka_prepare")
        graph.add_edge("inspect", "alert_summary")
        graph.add_edge("inspect", "jvm_prepare")

        graph.add_conditional_edges("db_kafka_prepare", self.route_db_kafka_chunks)
        graph.add_edge("db_kafka_chunk_worker", "db_kafka_collect")
        # Dashboard summary depends on DB/Kafka map-reduce branch
        graph.add_edge("db_kafka_collect", "dashboard_summary")

        graph.add_conditional_edges("jvm_prepare", self.route_jvm_chunks)
        graph.add_edge("jvm_chunk_worker", "jvm_collect")

        # build_report waits for all report ingredients
        graph.add_edge(["dashboard_summary", "alert_summary", "jvm_collect"], "build_report")
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

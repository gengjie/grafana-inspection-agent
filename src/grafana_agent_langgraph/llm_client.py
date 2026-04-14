"""LLM client module for report generation."""

import asyncio
import time
from typing import Any

import ssl

import aiohttp

from .logger import get_logger


def _no_verify_ssl() -> ssl.SSLContext:
    """Return an SSL context that skips certificate verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

logger = get_logger("llm_client")


class LLMClient:
    """Client for LLM API interactions."""

    _TOKEN_REFRESH_BUFFER_SECONDS = 60

    async def generate_db_kafka_panel_analysis(self, inspection_data: dict[str, Any]) -> str:
        """挑选数据库与Kafka相关的主面板并生成健康分析文本。"""
        dashboards = inspection_data.get("dashboards", [])
        lookback = inspection_data.get("lookback_period", {})
        start_ts = lookback.get("start", "")
        end_ts = lookback.get("end", "")

        # add additional keywords related to DB/Kafka panels based on common naming conventions and data source types
        # activity, tx, duration
        db_keywords = [
            "db", "database", "mysql", "mariadb", "postgres", "postgresql", "activity",
            "aurora", "rds", "sql", "query", "connection", "connections", "tx", "duration"
        ]
        kafka_keywords = [
            "kafka", "topic", "partition", "consumer", "producer", "broker",
            "connect", "ksql", "schema registry", "confluent",
        ]

        relevant = []
        max_panels = 30

        for dash in dashboards:
            dash_title = dash.get("title", "Unknown")
            dash_uid = dash.get("uid", "")
            for panel in dash.get("panels", []):
                if len(relevant) >= max_panels:
                    break
                panel_title = panel.get("title", "Unknown") or "Unknown"
                panel_type = panel.get("type", "") or ""
                # Combine searchable text
                target_texts = []
                for t in panel.get("targets", []) or []:
                    target_texts.append(
                        str(
                            t.get("expr")
                            or t.get("query")
                            or t.get("datasource")
                            or ""
                        )
                    )
                search_blob = " ".join([panel_title, panel_type] + target_texts).lower()
                if any(k in search_blob for k in db_keywords) or any(k in search_blob for k in kafka_keywords):
                    relevant.append(
                        {
                            "dashboard": dash_title,
                            "dashboard_uid": dash_uid,
                            "panel_id": panel.get("id"),
                            "panel_title": panel_title,
                            "panel_type": panel_type,
                            "targets": target_texts,
                            "metrics": panel.get("metrics") or [],
                        }
                    )

        if not relevant:
            return (
                "No database or Kafka related panels found for analysis."
                if self.language == "en"
                else "无数据库或Kafka相关的面板可供分析。"
            )

        lines = []
        for item in relevant:
            tgt = "; ".join(item["targets"]) if item["targets"] else "(无targets信息)"
            metrics_summary = self._format_metrics_summary(item.get("metrics"))
            if self.language == "en":
                lines.append(
                    f"- Dashboard: {item['dashboard']} (UID: {item['dashboard_uid']}) | "
                    f"Panel: {item['panel_title']} (ID: {item['panel_id']}, Type: {item['panel_type']}) | "
                    f"Targets: {tgt} | Metrics: {metrics_summary}"
                )
            else:
                lines.append(
                    f"- Dashboard: {item['dashboard']} (UID: {item['dashboard_uid']}) | "
                    f"面板: {item['panel_title']} (ID: {item['panel_id']}, 类型: {item['panel_type']}) | "
                    f"Targets: {tgt} | Metrics: {metrics_summary}"
                )

        # Chunk panel list to avoid token overflow; analyze chunks in parallel then merge
        chunk_size = 8
        panel_chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]

        if self.language == "en":
            template = (
                "You are given Grafana panels related to databases or Kafka. Based on generic operational knowledge, "
                "infer whether the services are likely healthy. Be explicit when data is missing and give best-effort reasoning.\n\n"
                f"Inspection time range: {start_ts} to {end_ts}\n\n"
                "Relevant panels:\n{panel_block}\n\n"
                "Requirements:\n"
                "1) Formal, concise language\n"
                "2) Output in English\n"
                "3) State health status (healthy / degraded / unknown) and reasoning for each panel\n"
                "4) Note missing data if applicable\n"
            )
            system_prompt = "You are a senior SRE with deep knowledge of DB and Kafka operations and common failure modes."
        else:
            template = (
                "以下是与数据库或Kafka相关的Grafana面板，请依据通用运维知识判断其健康状况。如缺少数据，请说明不确定性并给出最佳推断理由。\n\n"
                f"巡检时间范围：{start_ts} 至 {end_ts}\n\n"
                "相关面板：\n{panel_block}\n\n"
                "要求：\n"
                "1）使用正式、简洁的中文\n"
                "2）对每个面板给出健康结论（健康/异常/未知）及理由\n"
                "3）若数据缺失或仅有元数据，请明确说明推断依据\n"
            )
            system_prompt = "你是一名资深SRE，熟悉数据库与Kafka常见故障模式与运维要点。"

        async def run_chunk(chunk: list[str]) -> str:
            panel_block = "\n".join(chunk)
            prompt = template.format(panel_block=panel_block)
            try:
                logger.info(f"PANEL ANALYSIS PROMPT (chunk size={len(chunk)}): {prompt}")
                return await self._chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=min(self.max_tokens, 1200),
                )
            except Exception as e:
                logger.error("Failed to generate DB/Kafka panel analysis chunk: %s", e, exc_info=True)
                return ""

        # Limit parallelism to avoid flooding the LLM endpoint
        semaphore = asyncio.Semaphore(3)

        async def sem_run(chunk: list[str]) -> str:
            async with semaphore:
                return await run_chunk(chunk)

        chunk_results = await asyncio.gather(*[sem_run(c) for c in panel_chunks])
        merged = "\n\n".join(r for r in chunk_results if r)
        if not merged:
            return "数据库/Kafka 面板分析生成失败。"
        return merged

    def __init__(
        self,
        access_token: str,
        model: str = "claude-sonnet-4.6",
        api_base: str = "https://api.githubcopilot.com",
        token_url: str = "https://api.github.com/copilot_internal/v2/token",
        editor_version: str = "vscode/1.99.0",
        editor_plugin_version: str = "copilot-chat/0.26.7",
        user_agent: str = "GitHubCopilotChat/0.26.7",
        temperature: float = 0.3,
        max_tokens: int = 20000,
        language: str = "zh",
        request_timeout: int = 90,
    ):
        """Initialize GitHub Copilot LLM client.

        Args:
            access_token: GitHub access token
            model: Copilot model name
            api_base: Copilot API base URL
            token_url: Endpoint for exchanging session token
            editor_version: Editor version header
            editor_plugin_version: Editor plugin version header
            user_agent: User-Agent header
            temperature: Temperature for generation
            max_tokens: Maximum tokens for generation
            language: Output language ('zh' for Chinese, 'en' for English)
            request_timeout: Timeout for HTTP requests in seconds
        """
        self.provider = "github_copilot"
        self.access_token = access_token
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.token_url = token_url
        self.editor_version = editor_version
        self.editor_plugin_version = editor_plugin_version
        self.user_agent = user_agent
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.language = language
        self.request_timeout = request_timeout

        self._session_token = ""
        self._session_token_expires_at = 0.0

    async def _exchange_session_token(self) -> str:
        """Exchange GitHub access token for Copilot session token."""
        headers = {
            "Authorization": f"token {self.access_token}",
            "Accept": "application/json",
            "Editor-Version": self.editor_version,
            "Editor-Plugin-Version": self.editor_plugin_version,
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }

        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        connector = aiohttp.TCPConnector(ssl=_no_verify_ssl())
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.get(self.token_url, headers=headers) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {"raw": await response.text()}
                if response.status >= 400:
                    raise RuntimeError(
                        f"Copilot session token exchange failed ({response.status}): {payload}"
                    )

        token = payload.get("token")
        if not token:
            raise RuntimeError("Copilot session token exchange succeeded but token is missing")

        refresh_in = int(payload.get("refresh_in") or 1500)
        expires_at = payload.get("expires_at")
        now = time.time()
        if isinstance(expires_at, (int, float)):
            expiry = float(expires_at)
        elif isinstance(expires_at, str) and expires_at.isdigit():
            expiry = float(expires_at)
        else:
            expiry = now + refresh_in
        if expiry <= now:
            expiry = now + refresh_in

        api_endpoint = (payload.get("endpoints") or {}).get("api")
        if isinstance(api_endpoint, str) and api_endpoint.strip():
            self.api_base = api_endpoint.rstrip("/")

        self._session_token = token
        self._session_token_expires_at = expiry
        return token

    async def _get_session_token(self, force_refresh: bool = False) -> str:
        """Return a valid Copilot session token, refreshing if needed."""
        now = time.time()
        if (
            not force_refresh
            and self._session_token
            and now < (self._session_token_expires_at - self._TOKEN_REFRESH_BUFFER_SECONDS)
        ):
            return self._session_token
        return await self._exchange_session_token()

    def _extract_chat_content(self, response_payload: dict[str, Any]) -> str:
        """Extract assistant text from OpenAI-style response payload."""
        choices = response_payload.get("choices") or []
        if not choices:
            return ""

        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        return ""

    async def _chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        retry_on_auth_error: bool = True,
    ) -> str:
        """Call Copilot chat completion endpoint with session token."""
        session_token = await self._get_session_token()
        url = f"{self.api_base}/chat/completions"

        headers = {
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Editor-Version": self.editor_version,
            "Editor-Plugin-Version": self.editor_plugin_version,
            "User-Agent": self.user_agent,
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": False,
        }

        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        connector = aiohttp.TCPConnector(ssl=_no_verify_ssl())
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                try:
                    response_payload = await response.json(content_type=None)
                except Exception:
                    response_payload = {"raw": await response.text()}
                if response.status in {401, 403} and retry_on_auth_error:
                    logger.warning("Copilot session token expired or unauthorized, retrying after refresh")
                    await self._get_session_token(force_refresh=True)
                    return await self._chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        retry_on_auth_error=False,
                    )
                if response.status >= 400:
                    raise RuntimeError(
                        f"Copilot chat completion failed ({response.status}): {response_payload}"
                    )

        return self._extract_chat_content(response_payload)

    async def preflight(self) -> None:
        """Verify Copilot LLM connectivity: exchange token + send a tiny request."""
        logger.info("Preflight: exchanging Copilot session token...")
        await self._get_session_token(force_refresh=True)
        logger.info("Preflight: session token OK, testing chat completion...")
        reply = await self._chat_completion(
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        if not reply:
            raise RuntimeError("Preflight failed: Copilot returned empty response")
        logger.info("Preflight: Copilot LLM is operational (reply=%s)", reply[:40])

    async def generate_jvm_report(self, inspection_data: dict[str, Any]) -> str:
        """筛选 JVM 相关面板并生成 JVM 健康分析报告。"""
        dashboards = inspection_data.get("dashboards", [])
        lookback = inspection_data.get("lookback_period", {})
        start_ts = lookback.get("start", "")
        end_ts = lookback.get("end", "")

        jvm_keywords = [
            "jvm", "heap", "non-heap", "nonheap", "gc", "garbage",
            "eden", "survivor", "old gen", "tenured", "metaspace",
            "codecache", "code cache", "thread", "class loading",
            "jvm_memory", "jvm_gc", "jvm_threads", "jvm_buffer",
            "process_cpu", "hikari", "tomcat", "java_lang",
            "direct_buffer", "mapped_buffer", "g1", "young gen",
        ]

        relevant: list[dict[str, Any]] = []
        max_panels = 40

        for dash in dashboards:
            dash_title = dash.get("title", "Unknown")
            dash_uid = dash.get("uid", "")
            for panel in dash.get("panels", []):
                if len(relevant) >= max_panels:
                    break
                panel_title = panel.get("title", "Unknown") or "Unknown"
                panel_type = panel.get("type", "") or ""
                target_texts: list[str] = []
                for t in panel.get("targets", []) or []:
                    target_texts.append(
                        str(t.get("expr") or t.get("query") or t.get("datasource") or "")
                    )
                search_blob = " ".join([panel_title, panel_type] + target_texts).lower()
                if any(k in search_blob for k in jvm_keywords):
                    relevant.append({
                        "dashboard": dash_title,
                        "dashboard_uid": dash_uid,
                        "panel_id": panel.get("id"),
                        "panel_title": panel_title,
                        "panel_type": panel_type,
                        "targets": target_texts,
                        "metrics": panel.get("metrics") or [],
                    })

        if not relevant:
            return (
                "No JVM related panels found for analysis."
                if self.language == "en"
                else "无JVM相关的面板可供分析。"
            )

        lines: list[str] = []
        for item in relevant:
            tgt = "; ".join(item["targets"]) if item["targets"] else "(no targets)"
            metrics_summary = self._format_metrics_summary(item.get("metrics"))
            lines.append(
                f"- Dashboard: {item['dashboard']} (UID: {item['dashboard_uid']}) | "
                f"Panel: {item['panel_title']} (ID: {item['panel_id']}, Type: {item['panel_type']}) | "
                f"Targets: {tgt} | Metrics: {metrics_summary}"
            )

        chunk_size = 10
        panel_chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]

        if self.language == "en":
            system_prompt = (
                "You are a senior JVM performance engineer. Analyze these Grafana JVM panels "
                "and provide a professional health assessment covering heap/GC/threads/metaspace."
            )
            template = (
                "Analyze the following JVM-related Grafana panels. Provide a professional JVM health report.\n\n"
                f"Inspection time range: {start_ts} to {end_ts}\n\n"
                "Panels:\n{{panel_block}}\n\n"
                "Requirements:\n"
                "1) Assess heap memory, GC behavior, thread health, metaspace, and class loading\n"
                "2) Identify anomalies and provide severity ratings\n"
                "3) Provide tuning recommendations with concrete JVM flags\n"
                "4) Use formal, concise English\n"
            )
        else:
            system_prompt = (
                "你是一名资深JVM性能工程师，擅长JVM调优与故障排查。请基于Grafana面板数据"
                "给出专业的JVM健康分析报告，涵盖堆内存、GC行为、线程、Metaspace等维度。"
            )
            template = (
                "请分析以下JVM相关的Grafana面板数据，给出专业的JVM健康分析报告。\n\n"
                f"巡检时间范围：{start_ts} 至 {end_ts}\n\n"
                "面板数据：\n{{panel_block}}\n\n"
                "要求：\n"
                "1）分维度评估：堆内存、GC行为、线程健康、Metaspace、类加载\n"
                "2）识别异常指标，给出严重程度评级（🔴严重/🟡警告/🟢正常/⚪数据缺失）\n"
                "3）给出综合健康评级表格\n"
                "4）提供具体的JVM调优建议（含JVM参数）\n"
                "5）使用正式、简洁的中文\n"
            )

        async def run_chunk(chunk: list[str]) -> str:
            panel_block = "\n".join(chunk)
            prompt = template.replace("{panel_block}", panel_block)
            try:
                return await self._chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=min(self.max_tokens, 4000),
                )
            except Exception as e:
                logger.error("Failed to generate JVM report chunk: %s", e, exc_info=True)
                return ""

        semaphore = asyncio.Semaphore(3)

        async def sem_run(chunk: list[str]) -> str:
            async with semaphore:
                return await run_chunk(chunk)

        chunk_results = await asyncio.gather(*[sem_run(c) for c in panel_chunks])
        merged = "\n\n".join(r for r in chunk_results if r)
        if not merged:
            return "JVM健康分析报告生成失败。" if self.language != "en" else "Failed to generate JVM health report."
        return merged

    async def generate_dashboard_summary(
        self, inspection_data: dict[str, Any]
    ) -> str:
        """Generate summary for dashboard inspection.

        Args:
            inspection_data: Dashboard inspection data

        Returns:
            Generated summary text
        """
        db_kafka_analysis = await self.generate_db_kafka_panel_analysis(inspection_data)

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
            logger.debug(f"Generating dashboard summary with model: {self.model}")
            logger.info(f"Prompt: {prompt}")
            content = await self._chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug(f"Dashboard summary generated, length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"Failed to generate dashboard summary: {e}", exc_info=True)
            raise

    async def generate_alert_summary(self, inspection_data: dict[str, Any]) -> str:
        """Generate detailed summary for alert inspection.

        Args:
            inspection_data: Alert inspection data

        Returns:
            Generated alert summary text
        """
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
            logger.debug(f"Generating alert summary with model: {self.model}")
            content = await self._chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug(f"Alert summary generated, length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"Failed to generate alert summary: {e}", exc_info=True)
            raise

    async def generate_daily_report(
        self, dashboard_summary: str, alert_summary: str
    ) -> str:
        """Generate complete daily report.

        Args:
            dashboard_summary: Dashboard inspection summary
            alert_summary: Alert inspection summary

        Returns:
            Complete daily report
        """
        if self.language == "en":
            prompt = f"""Please integrate the following Dashboard inspection summary and alert inspection report into a complete daily report.

Dashboard Inspection Summary:
{dashboard_summary}

Alert Inspection Report:
{alert_summary}

Requirements:
1. Generate a formal and rigorous daily report
2. Include title, date, overview, Dashboard inspection section, and alert inspection section
3. Dashboard section should use summary language, concise and clear
4. Alert section should use detailed descriptions, comprehensive and complete
5. Output in English
6. Professional format suitable as a formal work report

Please generate the complete daily report:"""
            system_prompt = "You are a professional DevOps engineer, skilled in writing formal technical reports and daily reports."
        else:
            prompt = f"""请将以下Dashboard巡检总结和告警巡检报告整合成一份完整的日报。

Dashboard巡检总结：
{dashboard_summary}

告警巡检报告：
{alert_summary}

要求：
1. 生成一份格式正式、严谨的日报
2. 包含标题、日期、概述、Dashboard巡检部分、告警巡检部分
3. Dashboard部分使用总结性语言，简洁明了
4. 告警部分使用明细性描述，详细完整
5. 使用中文输出
6. 整体格式专业，适合作为正式的工作报告

请生成完整的日报："""
            system_prompt = "你是一位专业的运维工程师，擅长撰写正式的技术报告和日报。"

        try:
            logger.debug(f"Generating daily report with model: {self.model}")
            content = await self._chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
            )
            logger.debug(f"Daily report generated, length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"Failed to generate daily report: {e}", exc_info=True)
            raise

    def _format_dashboards_for_prompt(self, dashboards: list[dict[str, Any]]) -> str:
        """Format dashboard data for LLM prompt."""
        lines = []
        for dash in dashboards[:50]:  # Limit to first 50 to avoid token limits
            lines.append(
                f"- {dash.get('title', 'Unknown')} (UID: {dash.get('uid')}, "
                f"Panels: {dash.get('panel_count', 0)})"
            )
        if len(dashboards) > 50:
            lines.append(f"... 还有 {len(dashboards) - 50} 个Dashboard未列出")
        return "\n".join(lines)

    def _format_alerts_for_prompt(self, alert_history: list[dict[str, Any]]) -> str:
        """Format alert history for LLM prompt."""
        if not alert_history:
            return "巡检期间无告警触发记录。"

        lines = []
        for alert in alert_history[:30]:  # Limit to first 30
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

    def _format_active_alerts_for_prompt(
        self, active_alerts: list[dict[str, Any]]
    ) -> str:
        """Format active alerts for LLM prompt."""
        if not active_alerts:
            return "当前无活跃告警。"

        lines = []
        for alert in active_alerts[:30]:  # Limit to first 30
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

    def _format_metrics_summary(self, metrics: Any) -> str:
        """Return a concise metrics summary for prompt context."""
        if not metrics:
            return "No metrics data" if self.language == "en" else "无metrics数据"

        try:
            # Normalize list inputs by sampling a few entries to avoid token bloat
            if isinstance(metrics, list):
                parts = []
                for m in metrics[:5]:
                    if isinstance(m, dict):
                        name = m.get("name") or m.get("metric") or m.get("title") or "metric"
                        value = m.get("value") or m.get("current") or m.get("stat") or m.get("data")
                        unit = m.get("unit")
                        fragment = f"{name}: {value}" if value is not None else str(m)
                        if unit:
                            fragment = f"{fragment} {unit}"
                        parts.append(fragment)
                    else:
                        parts.append(str(m))
                if len(metrics) > 5:
                    more = len(metrics) - 5
                    parts.append(
                        f"... and {more} more metrics"
                        if self.language == "en"
                        else f"... 还有 {more} 条metrics未列出"
                    )
                return "; ".join(parts)

            if isinstance(metrics, dict):
                items = list(metrics.items())
                parts = [f"{k}: {v}" for k, v in items[:6]]
                if len(items) > 6:
                    parts.append(
                        f"... and {len(items) - 6} more metrics"
                        if self.language == "en"
                        else f"... 还有 {len(items) - 6} 条metrics未列出"
                    )
                return "; ".join(parts)

            # Fallback stringify for unexpected types
            return str(metrics)
        except Exception:
            return str(metrics)


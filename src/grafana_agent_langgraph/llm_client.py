"""LLM transport client module (Copilot token exchange + chat completion)."""

import asyncio
import time
from typing import Any, Callable

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
    """Client for low-level LLM API interactions."""

    _TOKEN_REFRESH_BUFFER_SECONDS = 60
    _CHUNK_FAILURE_PREFIX = "__CHUNK_FAILED__"

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
        jvm_keywords: list[str] | None = None,
        jvm_max_panels: int = 100,
        language: str = "zh",
        request_timeout: int = 180,
        chunk_max_retries: int = 2,
        chunk_retry_backoff_seconds: float = 1.0,
        chunk_retry_max_backoff_seconds: float = 8.0,
    ):
        """Initialize GitHub Copilot LLM client."""
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
        # Kept for backward compatible config passing; business logic is moved out.
        self.jvm_keywords = [str(kw).strip() for kw in (jvm_keywords or []) if str(kw).strip()]
        self.jvm_max_panels = max(1, int(jvm_max_panels))
        self.language = language
        self.request_timeout = request_timeout
        self.chunk_max_retries = max(0, int(chunk_max_retries))
        self.chunk_retry_backoff_seconds = max(0.1, float(chunk_retry_backoff_seconds))
        self.chunk_retry_max_backoff_seconds = max(
            self.chunk_retry_backoff_seconds,
            float(chunk_retry_max_backoff_seconds),
        )

        self._session_token = ""
        self._session_token_expires_at = 0.0

    async def _run_chunk_subagents(
        self,
        *,
        chunks: list[list[str]],
        system_prompt: str,
        prompt_builder: Callable[[str], str],
        max_tokens: int,
        task_name: str,
        concurrency: int = 3,
        log_prompt: bool = False,
    ) -> list[str]:
        """Run chunked LLM tasks with bounded concurrency."""
        if not chunks:
            return []

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_subagent(chunk_index: int, chunk: list[str]) -> str:
            panel_block = "\n".join(chunk)
            prompt = prompt_builder(panel_block)
            try:
                if log_prompt:
                    logger.info(
                        "%s subagent prompt (chunk=%s, size=%s): %s",
                        task_name,
                        chunk_index,
                        len(chunk),
                        prompt,
                    )
                return await self._chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.error(
                    "%s subagent failed (chunk=%s): %s",
                    task_name,
                    chunk_index,
                    e,
                    exc_info=True,
                )
                return ""

        async def sem_run(chunk_index: int, chunk: list[str]) -> str:
            async with semaphore:
                return await run_subagent(chunk_index, chunk)

        tasks = [sem_run(idx, chunk) for idx, chunk in enumerate(chunks, start=1)]
        return await asyncio.gather(*tasks)

    def prepare_db_kafka_chunk_jobs(
        self,
        inspection_data: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Prepare DB/Kafka chunk jobs for graph-level subagent scheduling."""
        dashboards = inspection_data.get("dashboards", [])
        lookback = inspection_data.get("lookback_period", {})
        start_ts = lookback.get("start", "")
        end_ts = lookback.get("end", "")

        db_keywords = [
            "db",
            "database",
            "mysql",
            "mariadb",
            "postgres",
            "postgresql",
            "activity",
            "aurora",
            "rds",
            "sql",
            "query",
            "connection",
            "connections",
            "tx",
            "duration",
        ]
        kafka_keywords = [
            "kafka",
            "topic",
            "partition",
            "consumer",
            "producer",
            "broker",
            "connect",
            "ksql",
            "schema registry",
            "confluent",
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
                target_texts = []
                for t in panel.get("targets", []) or []:
                    target_texts.append(
                        str(t.get("expr") or t.get("query") or t.get("datasource") or "")
                    )
                search_blob = " ".join([panel_title, panel_type] + target_texts).lower()
                if any(k in search_blob for k in db_keywords) or any(
                    k in search_blob for k in kafka_keywords
                ):
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
                [],
                "No database or Kafka related panels found for analysis."
                if self.language == "en"
                else "无数据库或Kafka相关的面板可供分析。",
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
            system_prompt = (
                "You are a senior SRE with deep knowledge of DB and Kafka operations and common failure modes."
            )
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

        jobs = []
        for chunk in panel_chunks:
            panel_block = "\n".join(chunk)
            jobs.append(
                {
                    "task_name": "db_kafka_analysis",
                    "system_prompt": system_prompt,
                    "prompt": template.format(panel_block=panel_block),
                    "max_tokens": min(self.max_tokens, 1200),
                    "log_prompt": True,
                }
            )

        return jobs, None

    async def run_chunk_job(self, job: dict[str, Any]) -> str:
        """Execute one chunk job as a subagent unit."""
        task_name = str(job.get("task_name") or "chunk_task")
        prompt = str(job.get("prompt") or "")
        system_prompt = str(job.get("system_prompt") or "")
        max_tokens = int(job.get("max_tokens") or self.max_tokens)
        log_prompt = bool(job.get("log_prompt", False))
        chunk_index = job.get("chunk_index")
        return_failure_marker = bool(job.get("return_failure_marker", False))
        request_timeout = int(job.get("request_timeout") or self.request_timeout)
        max_retries = int(job.get("max_retries", self.chunk_max_retries))
        max_retries = max(0, max_retries)
        retry_backoff_seconds = float(
            job.get("retry_backoff_seconds", self.chunk_retry_backoff_seconds)
        )
        retry_backoff_seconds = max(0.1, retry_backoff_seconds)
        retry_max_backoff_seconds = float(
            job.get("retry_max_backoff_seconds", self.chunk_retry_max_backoff_seconds)
        )
        retry_max_backoff_seconds = max(retry_backoff_seconds, retry_max_backoff_seconds)

        if log_prompt:
            logger.info(
                "%s subagent prompt (chunk=%s): %s",
                task_name,
                chunk_index,
                prompt,
            )

        total_attempts = max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                return await self._chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                )
            except Exception as e:
                is_retryable = self._is_retryable_chunk_error(e)
                should_retry = is_retryable and attempt < total_attempts
                if should_retry:
                    sleep_seconds = min(
                        retry_max_backoff_seconds,
                        retry_backoff_seconds * (2 ** (attempt - 1)),
                    )
                    logger.warning(
                        "%s subagent transient failure (chunk=%s, attempt=%s/%s), "
                        "retry in %.1fs: %s",
                        task_name,
                        chunk_index,
                        attempt,
                        total_attempts,
                        sleep_seconds,
                        e,
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue

                logger.error(
                    "%s subagent failed (chunk=%s, attempt=%s/%s): %s",
                    task_name,
                    chunk_index,
                    attempt,
                    total_attempts,
                    e,
                    exc_info=True,
                )
                if return_failure_marker and chunk_index is not None:
                    return f"{self._CHUNK_FAILURE_PREFIX}:{chunk_index}"
                return ""

        if return_failure_marker and chunk_index is not None:
            return f"{self._CHUNK_FAILURE_PREFIX}:{chunk_index}"
        return ""

    async def generate_db_kafka_panel_analysis(self, inspection_data: dict[str, Any]) -> str:
        """Generate DB/Kafka panel analysis text with chunk jobs."""
        jobs, fallback_text = self.prepare_db_kafka_chunk_jobs(inspection_data)
        if fallback_text is not None:
            return fallback_text

        chunk_results = await asyncio.gather(*[self.run_chunk_job(job) for job in jobs])
        merged = "\n\n".join(r for r in chunk_results if r)
        if not merged:
            return "数据库/Kafka 面板分析生成失败。"
        return merged

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
        request_timeout: int | None = None,
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

        timeout_seconds = max(1, int(request_timeout if request_timeout is not None else self.request_timeout))
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
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

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        request_timeout: int | None = None,
    ) -> str:
        """Public wrapper for chat completion used by domain services."""
        return await self._chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )

    def _is_retryable_chunk_error(self, exc: Exception) -> bool:
        """Return True for transient transport/rate-limit/server-side failures."""
        if isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError)):
            return True

        text = str(exc).lower()
        retryable_markers = [
            "timeout",
            "timed out",
            "too many requests",
            "rate limit",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection closed",
            "502",
            "503",
            "504",
        ]
        return any(marker in text for marker in retryable_markers)

    def _format_metrics_summary(self, metrics: Any) -> str:
        """Return a concise metrics summary for prompt context."""
        if not metrics:
            return "No metrics data" if self.language == "en" else "无metrics数据"

        try:
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

            return str(metrics)
        except Exception:
            return str(metrics)

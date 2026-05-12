"""JVM report analysis service decoupled from raw LLM client transport."""

from __future__ import annotations

from typing import Any, Awaitable, Callable
import re

from .logger import get_logger

logger = get_logger("jvm_report")

_SERVICE_LABEL_PATTERNS = [
    re.compile(r'(?:service|app|application|job|component|workload|k8s_app|pod|deployment|statefulset)=~?"([^"]+)"', re.IGNORECASE),
    re.compile(r"(?:service|app|application|job|component|workload|k8s_app|pod|deployment|statefulset)\\s*=\\s*'([^']+)'", re.IGNORECASE),
]

_DEFAULT_JVM_KEYWORDS = [
    "jvm",
    "heap",
    "non-heap",
    "nonheap",
    "gc",
    "garbage",
    "eden",
    "survivor",
    "old gen",
    "tenured",
    "metaspace",
    "codecache",
    "code cache",
    "thread",
    "class loading",
    "jvm_memory",
    "jvm_gc",
    "jvm_threads",
    "jvm_buffer",
    "process_cpu",
    "hikari",
    "tomcat",
    "java_lang",
    "direct_buffer",
    "mapped_buffer",
    "g1",
    "young gen",
]


class JVMReport:
    """JVM chunk planning and reduce aggregation service."""

    def __init__(
        self,
        *,
        language: str,
        max_tokens: int,
        chat_completion: Callable[..., Awaitable[str]],
        jvm_keywords: list[str] | None = None,
        jvm_max_panels: int = 100,
        chunk_failure_prefix: str = "__CHUNK_FAILED__",
    ) -> None:
        self.language = language
        self.max_tokens = max_tokens
        self.chat_completion = chat_completion
        self.jvm_keywords = [
            str(kw).strip() for kw in (jvm_keywords or _DEFAULT_JVM_KEYWORDS) if str(kw).strip()
        ]
        self.jvm_max_panels = max(1, int(jvm_max_panels))
        self.chunk_failure_prefix = chunk_failure_prefix

    def prepare_jvm_chunk_jobs(
        self,
        inspection_data: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        dashboards = inspection_data.get("dashboards", [])
        lookback = inspection_data.get("lookback_period", {})
        start_ts = lookback.get("start", "")
        end_ts = lookback.get("end", "")

        jvm_keywords = [kw.lower() for kw in self.jvm_keywords]

        relevant: list[dict[str, Any]] = []

        for dash in dashboards:
            dash_title = dash.get("title", "Unknown")
            dash_uid = dash.get("uid", "")
            for panel in dash.get("panels", []):
                if len(relevant) >= self.jvm_max_panels:
                    break
                panel_title = panel.get("title", "Unknown") or "Unknown"
                panel_description = str(panel.get("description") or "")
                panel_type = panel.get("type", "") or ""
                target_texts: list[str] = []
                for t in panel.get("targets", []) or []:
                    target_texts.append(
                        str(t.get("expr") or t.get("query") or t.get("datasource") or "")
                    )
                search_blob = " ".join(
                    [panel_title, panel_description, panel_type] + target_texts
                ).lower()
                if any(k in search_blob for k in jvm_keywords):
                    semantic_description = str(
                        panel.get("semantic_description")
                        or self._build_panel_semantic_description(
                            panel_title,
                            panel_description,
                            panel_type,
                            target_texts,
                        )
                    )
                    service_key = self._extract_service_key(
                        panel_title,
                        panel_description,
                        target_texts,
                    )
                    relevant.append(
                        {
                            "dashboard": dash_title,
                            "dashboard_uid": dash_uid,
                            "panel_id": panel.get("id"),
                            "panel_title": panel_title,
                            "panel_description": panel_description,
                            "panel_type": panel_type,
                            "targets": target_texts,
                            "service_key": service_key,
                            "semantic_description": semantic_description,
                            "metrics": panel.get("metrics") or [],
                        }
                    )

        if not relevant:
            return (
                [],
                "No JVM related panels found for analysis."
                if self.language == "en"
                else "无JVM相关的面板可供分析。",
            )

        lines: list[str] = []
        for item in sorted(
            relevant,
            key=lambda x: (
                str(x.get("service_key") or ""),
                str(x.get("dashboard") or ""),
                str(x.get("panel_title") or ""),
            ),
        ):
            tgt = "; ".join(item["targets"]) if item["targets"] else "(no targets)"
            metrics_summary = self._format_metrics_summary(item.get("metrics"))
            service_key = item.get("service_key") or "unknown_service"
            semantic_description = item.get("semantic_description") or ""
            lines.append(
                f"- Dashboard: {item['dashboard']} (UID: {item['dashboard_uid']}) | "
                f"Panel: {item['panel_title']} (ID: {item['panel_id']}, Type: {item['panel_type']}) | "
                f"ServiceKey: {service_key} | Semantic: {semantic_description} | "
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
                "Analyze the following JVM-related Grafana panels. Provide a professional JVM health report.\\n\\n"
                f"Inspection time range: {start_ts} to {end_ts}\\n\\n"
                "Panels:\\n{{panel_block}}\\n\\n"
                "Requirements:\\n"
                "1) Assess heap memory, GC behavior, thread health, metaspace, and class loading\\n"
                "2) Identify anomalies and provide severity ratings\\n"
                "3) Restart-cause diagnosis must follow evidence strictly:\\n"
                "   - Only call it an OOM restart when explicit OOM signals exist (e.g., OOMKilled, container_oom_events_total, reason=OOMKilled)\\n"
                "   - If scheduling/eviction/node-operation signals appear (e.g., FailedScheduling, Evicted, preempt, node drain), classify as non-OOM scheduling-related restart\\n"
                "   - If restart counts rise but no explicit reason signal exists, mark cause as unknown and list required observability evidence\\n"
                "4) Provide tuning recommendations with concrete JVM flags\\n"
                "5) Aggregate findings by ServiceKey first, then summarize panel-level observations\\n"
                "6) Use formal, concise English\\n"
            )
        else:
            system_prompt = (
                "你是一名资深JVM性能工程师，擅长JVM调优与故障排查。请基于Grafana面板数据"
                "给出专业的JVM健康分析报告，涵盖堆内存、GC行为、线程、Metaspace等维度。"
            )
            template = (
                "请分析以下JVM相关的Grafana面板数据，给出专业的JVM健康分析报告。\\n\\n"
                f"巡检时间范围：{start_ts} 至 {end_ts}\\n\\n"
                "面板数据：\\n{{panel_block}}\\n\\n"
                "要求：\\n"
                "1）分维度评估：堆内存、GC行为、线程健康、Metaspace、类加载\\n"
                "2）识别异常指标，给出严重程度评级（🔴严重/🟡警告/🟢正常/⚪数据缺失）\\n"
                "3）重启原因诊断必须严格基于证据：\\n"
                "   - 仅在出现明确OOM信号（如 OOMKilled、container_oom_events_total、reason=OOMKilled）时，才能判定为OOM重启\\n"
                "   - 若出现调度/驱逐/节点操作信号（如 FailedScheduling、Evicted、preempt、node drain），应判定为非OOM的K8s调度类重启\\n"
                "   - 仅有重启次数上升而无明确原因信号时，必须标记为原因未知，并说明还需哪些观测证据\\n"
                "4）优先按ServiceKey聚合同一服务的Panel，再做服务级结论\\n"
                "5）给出综合健康评级\\n"
                "6）提供具体的JVM调优建议（含JVM参数）\\n"
                "7）使用正式、简洁的中文\\n"
            )

        jobs = []
        for chunk in panel_chunks:
            panel_block = "\\n".join(chunk)
            jobs.append(
                {
                    "task_name": "jvm_report",
                    "system_prompt": system_prompt,
                    "prompt": template.replace("{panel_block}", panel_block),
                    "max_tokens": min(self.max_tokens, 4000),
                    "log_prompt": False,
                    "return_failure_marker": True,
                }
            )

        return jobs, None

    async def reduce_jvm_chunk_results(
        self,
        chunk_results: list[str],
        inspection_data: dict[str, Any] | None = None,
    ) -> str:
        failed_chunks: list[int] = []
        valid_results: list[str] = []
        for item in chunk_results:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            if text.startswith(f"{self.chunk_failure_prefix}:"):
                _, _, raw_idx = text.partition(":")
                try:
                    failed_chunks.append(int(raw_idx))
                except ValueError:
                    pass
                continue
            valid_results.append(text)

        if not valid_results:
            failed_note = self._format_failed_chunk_note(failed_chunks)
            return (
                "Failed to generate JVM health report."
                if self.language == "en"
                else "JVM健康分析报告生成失败。"
            ) + failed_note

        if len(valid_results) == 1:
            return valid_results[0] + self._format_failed_chunk_note(failed_chunks)

        lookback = (inspection_data or {}).get("lookback_period", {})
        start_ts = lookback.get("start", "")
        end_ts = lookback.get("end", "")
        chunks_text = "\\n\\n".join(
            f"[Chunk {idx}]\\n{content}" for idx, content in enumerate(valid_results, start=1)
        )
        max_reduce_input_chars = 18000
        if len(chunks_text) > max_reduce_input_chars:
            chunks_text = chunks_text[:max_reduce_input_chars] + "\\n\\n...(truncated)"

        if self.language == "en":
            system_prompt = (
                "You are a principal JVM performance engineer. "
                "Merge multiple sub-reports into one consistent final JVM health report."
            )
            prompt = (
                "Merge the following JVM chunk analysis outputs into one final report.\\n\\n"
                f"Inspection time range: {start_ts} to {end_ts}\\n\\n"
                "Chunk outputs:\\n"
                f"{chunks_text}\\n\\n"
                "Requirements:\\n"
                "1) Deduplicate repeated findings and resolve conflicts with explicit rationale\\n"
                "2) Keep dimensions: heap, GC, threads, metaspace, class loading\\n"
                "3) Restart-cause conclusions must be evidence-driven:\\n"
                "   - OOM restart requires explicit OOM signal evidence\\n"
                "   - Scheduling/eviction/node-operation evidence must be labeled as non-OOM restart\\n"
                "   - If only restart count changes without cause evidence, mark as unknown cause\\n"
                "4) Include severity labels and an overall health rating\\n"
                "5) Provide actionable tuning recommendations with concrete JVM flags\\n"
                "6) Output concise, formal English\\n"
                "7) Use only simple Markdown (headings + bullets), avoid tables and code blocks\\n"
            )
        else:
            system_prompt = (
                "你是一名首席JVM性能工程师。"
                "请将多个分片子报告聚合为一份一致、可执行的最终JVM健康报告。"
            )
            prompt = (
                "请将以下JVM分片分析结果聚合为一份最终报告。\\n\\n"
                f"巡检时间范围：{start_ts} 至 {end_ts}\\n\\n"
                "分片输出：\\n"
                f"{chunks_text}\\n\\n"
                "要求：\\n"
                "1）去重并合并重复结论，若结论冲突请给出取舍依据\\n"
                "2）保持维度完整：堆内存、GC、线程、Metaspace、类加载\\n"
                "3）重启原因结论必须基于证据：\\n"
                "   - 只有出现明确OOM证据时，才能写为OOM重启\\n"
                "   - 出现调度/驱逐/节点操作证据时，必须写为非OOM重启\\n"
                "   - 仅有重启次数变化而无原因证据时，必须写为原因未知\\n"
                "4）给出严重程度标注与总体健康评级\\n"
                "5）提供可执行调优建议，包含具体JVM参数\\n"
                "6）使用正式、简洁中文输出\\n"
                "7）仅使用简单 Markdown（标题与列表），不要使用表格和代码块\\n"
            )

        try:
            merged = await self.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=min(self.max_tokens, 6000),
            )
            return merged + self._format_failed_chunk_note(failed_chunks)
        except Exception as e:
            logger.error("Failed to reduce JVM chunk outputs: %s", e, exc_info=True)
            fallback = self._build_compact_jvm_fallback(valid_results)
            return fallback + self._format_failed_chunk_note(failed_chunks)

    def _build_panel_semantic_description(
        self,
        panel_title: Any,
        panel_description: Any,
        panel_type: Any,
        target_texts: list[str],
    ) -> str:
        parts: list[str] = []
        title = str(panel_title or "").strip()
        description = str(panel_description or "").strip()
        p_type = str(panel_type or "").strip()

        if title:
            parts.append(f"title={title}")
        if description:
            parts.append(f"description={description}")
        if p_type:
            parts.append(f"type={p_type}")
        if target_texts:
            parts.append(f"targets={' | '.join(target_texts[:2])}")

        return "; ".join(parts)

    def _extract_service_key(
        self,
        panel_title: Any,
        panel_description: Any,
        target_texts: list[str],
    ) -> str:
        title = str(panel_title or "").strip()
        description = str(panel_description or "").strip()

        for raw in target_texts:
            text = str(raw or "")
            for pattern in _SERVICE_LABEL_PATTERNS:
                match = pattern.search(text)
                if match:
                    candidate = self._normalize_service_key(match.group(1))
                    if candidate:
                        return candidate

        for candidate in [description, title]:
            extracted = self._normalize_service_key(candidate)
            if extracted:
                return extracted

        return "unknown_service"

    def _normalize_service_key(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"\$\{[^}]+\}", "", text)
        text = text.replace(".*", "")
        text = re.sub(r"[^a-z0-9._\-]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-._")
        if not text:
            return ""
        if text in {"all", "unknown", "none"}:
            return ""
        return text

    def _format_metrics_summary(self, metrics: Any) -> str:
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

    def _build_compact_jvm_fallback(self, valid_results: list[str]) -> str:
        if self.language == "en":
            title = "JVM Health Analysis (Fallback Summary)"
            intro = "Reduce-stage timeout occurred. The following key points are aggregated from chunk results:"
        else:
            title = "JVM 健康分析（降级汇总）"
            intro = "reduce 阶段调用超时，以下为基于分片结果的关键结论汇总："

        bullets: list[str] = []
        for chunk in valid_results:
            for line in chunk.splitlines():
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                if text.startswith("- ") or text.startswith("* "):
                    candidate = text[2:].strip()
                else:
                    candidate = text
                if len(candidate) < 8:
                    continue
                bullets.append(candidate)
                if len(bullets) >= 30:
                    break
            if len(bullets) >= 30:
                break

        if not bullets:
            bullets = valid_results[:3]

        lines = [f"# {title}", "", intro, ""]
        lines.extend([f"- {item}" for item in bullets[:20]])
        return "\n".join(lines)

    def _format_failed_chunk_note(self, failed_chunks: list[int]) -> str:
        if not failed_chunks:
            return ""
        uniq = sorted(set(failed_chunks))
        idx_text = ", ".join(str(i) for i in uniq)
        if self.language == "en":
            return (
                "\n\n---\n"
                "Missing chunk outputs detected due to subagent failures. "
                f"Missing chunk indexes: {idx_text}."
            )
        return (
            "\n\n---\n"
            "检测到分片子任务失败，最终报告存在缺失内容。"
            f"缺失分片序号：{idx_text}。"
        )

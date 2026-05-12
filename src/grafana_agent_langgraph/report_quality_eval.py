"""Automated quality evaluation for generated daily reports.

This evaluator is deterministic and can be used as a CI quality gate.
It scores report quality across structure, factual grounding, and actionability.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ZH_SECTION_HINTS = [
    "一、dashboard",
    "二、告警",
    "生成时间",
    "巡检周期",
]

EN_SECTION_HINTS = [
    "i. dashboard inspection summary",
    "ii. alert inspection report",
    "generated time",
    "inspection period",
]

ACTION_KEYWORDS = [
    "建议",
    "排查",
    "优化",
    "调优",
    "修复",
    "recommend",
    "action",
    "mitigate",
    "tune",
    "investigate",
    "fix",
]

RISK_KEYWORDS = [
    "严重",
    "警告",
    "风险",
    "异常",
    "critical",
    "warning",
    "risk",
    "degraded",
    "incident",
    "anomaly",
]

OOM_REPORT_KEYWORDS = [
    "oom",
    "oomkilled",
    "out of memory",
    "内存溢出",
    "oom重启",
]

SCHEDULING_REPORT_KEYWORDS = [
    "调度",
    "驱逐",
    "evicted",
    "failedscheduling",
    "unschedulable",
    "node drain",
    "preempt",
    "非oom",
    "not oom",
]

OOM_SIGNAL_KEYWORDS = [
    "oomkilled",
    "outofmemory",
    "out of memory",
    "container_oom_events_total",
    "reason=oomkilled",
]

SCHEDULING_SIGNAL_KEYWORDS = [
    "failedscheduling",
    "unschedulable",
    "evicted",
    "preempt",
    "node drain",
    "nodedrain",
    "taint",
    "node notready",
    "reschedule",
]

UNCERTAINTY_KEYWORDS = [
    "数据缺失",
    "未知",
    "不确定",
    "缺少",
    "no data",
    "unknown",
    "insufficient",
    "missing",
    "uncertain",
]


@dataclass
class ScoreBreakdown:
    structure: float
    factual_grounding: float
    actionability: float
    uncertainty_handling: float
    restart_cause_diagnosis: float

    @property
    def total(self) -> float:
        # Weighted total for CI gate
        return round(
            self.structure * 0.20
            + self.factual_grounding * 0.35
            + self.actionability * 0.15
            + self.uncertainty_handling * 0.10
            + self.restart_cause_diagnosis * 0.20,
            2,
        )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _detect_language(report_text: str) -> str:
    # Simple heuristic: presence of CJK characters
    if re.search(r"[\u4e00-\u9fff]", report_text):
        return "zh"
    return "en"


def _score_structure(report_text: str, language: str) -> tuple[float, list[str]]:
    issues: list[str] = []
    text = _normalize_text(report_text)
    hints = ZH_SECTION_HINTS if language == "zh" else EN_SECTION_HINTS

    matched = sum(1 for hint in hints if hint in text)
    if matched < len(hints):
        issues.append(f"Missing required section hints: {matched}/{len(hints)} matched")

    line_count = len([ln for ln in report_text.splitlines() if ln.strip()])
    if line_count < 12:
        issues.append("Report is too short (<12 non-empty lines)")

    score = 100.0
    if matched < len(hints):
        score -= (len(hints) - matched) * 15
    if line_count < 12:
        score -= 20

    return max(0.0, round(score, 2)), issues


def _collect_known_entities(
    dashboard_inspection: dict[str, Any],
    alert_inspection: dict[str, Any],
) -> tuple[set[str], set[str]]:
    dashboard_entities: set[str] = set()
    alert_entities: set[str] = set()

    dashboards = (dashboard_inspection.get("dashboards") or []) if isinstance(dashboard_inspection, dict) else []
    for dash in dashboards:
        title = str(dash.get("title") or "").strip()
        if title:
            dashboard_entities.add(title)
        for panel in dash.get("panels", []) or []:
            panel_title = str(panel.get("title") or "").strip()
            if panel_title:
                dashboard_entities.add(panel_title)

    active_alerts = (alert_inspection.get("active_alerts") or []) if isinstance(alert_inspection, dict) else []
    history_alerts = (alert_inspection.get("alert_history") or []) if isinstance(alert_inspection, dict) else []

    for a in active_alerts:
        labels = a.get("labels", {}) if isinstance(a, dict) else {}
        name = str(labels.get("alertname") or "").strip()
        if name:
            alert_entities.add(name)

    for a in history_alerts:
        inst = a.get("instance", {}) if isinstance(a, dict) else {}
        labels = inst.get("labels", {}) if isinstance(inst, dict) else {}
        name = str(labels.get("alertname") or "").strip()
        if name:
            alert_entities.add(name)

    return dashboard_entities, alert_entities


def _score_factual_grounding(
    report_text: str,
    dashboard_inspection: dict[str, Any],
    alert_inspection: dict[str, Any],
) -> tuple[float, list[str], dict[str, Any]]:
    issues: list[str] = []
    report_lower = report_text.lower()

    dashboard_entities, alert_entities = _collect_known_entities(dashboard_inspection, alert_inspection)

    # Use up to top-N entities to avoid over-penalizing ultra-large reports.
    dashboard_samples = sorted(dashboard_entities)[:20]
    alert_samples = sorted(alert_entities)[:20]

    dashboard_hits = sum(1 for e in dashboard_samples if e.lower() in report_lower)
    alert_hits = sum(1 for e in alert_samples if e.lower() in report_lower)

    dashboard_ratio = 1.0 if not dashboard_samples else dashboard_hits / len(dashboard_samples)
    alert_ratio = 1.0 if not alert_samples else alert_hits / len(alert_samples)

    if dashboard_ratio < 0.2 and dashboard_samples:
        issues.append(
            f"Low dashboard grounding ratio: {dashboard_hits}/{len(dashboard_samples)}"
        )
    if alert_ratio < 0.2 and alert_samples:
        issues.append(f"Low alert grounding ratio: {alert_hits}/{len(alert_samples)}")

    score = round((dashboard_ratio * 60 + alert_ratio * 40) * 100 / 100, 2)

    details = {
        "dashboard_samples": len(dashboard_samples),
        "dashboard_hits": dashboard_hits,
        "alert_samples": len(alert_samples),
        "alert_hits": alert_hits,
        "dashboard_ratio": round(dashboard_ratio, 4),
        "alert_ratio": round(alert_ratio, 4),
    }
    return max(0.0, min(100.0, score)), issues, details


def _score_actionability(report_text: str) -> tuple[float, list[str], dict[str, int]]:
    issues: list[str] = []
    text = report_text.lower()

    action_hits = sum(1 for kw in ACTION_KEYWORDS if kw in text)
    risk_hits = sum(1 for kw in RISK_KEYWORDS if kw in text)

    if action_hits < 2:
        issues.append("Too few action-oriented statements")
    if risk_hits < 2:
        issues.append("Too few risk/severity signals")

    score = min(100.0, action_hits * 20 + risk_hits * 15)
    details = {"action_keyword_hits": action_hits, "risk_keyword_hits": risk_hits}
    return max(0.0, round(score, 2)), issues, details


def _count_missing_data_panels(dashboard_inspection: dict[str, Any]) -> tuple[int, int]:
    total = 0
    missing = 0

    for dash in dashboard_inspection.get("dashboards", []) or []:
        for panel in dash.get("panels", []) or []:
            total += 1
            metrics = panel.get("metrics") or {}
            if isinstance(metrics, dict):
                status = str(metrics.get("status") or "").lower()
                if status in {"empty", "error", "skipped"}:
                    missing += 1
            elif not metrics:
                missing += 1

    return total, missing


def _score_uncertainty_handling(
    report_text: str,
    dashboard_inspection: dict[str, Any],
) -> tuple[float, list[str], dict[str, Any]]:
    issues: list[str] = []
    report_lower = report_text.lower()
    total_panels, missing_panels = _count_missing_data_panels(dashboard_inspection)

    missing_ratio = 0.0 if total_panels == 0 else missing_panels / total_panels
    uncertainty_hits = sum(1 for kw in UNCERTAINTY_KEYWORDS if kw in report_lower)

    expect_uncertainty = missing_ratio >= 0.15
    if expect_uncertainty and uncertainty_hits == 0:
        issues.append(
            "Missing uncertainty statement while source data has significant missing metrics"
        )

    if not expect_uncertainty:
        score = 100.0
    else:
        score = min(100.0, 30.0 + uncertainty_hits * 20.0)

    details = {
        "total_panels": total_panels,
        "missing_panels": missing_panels,
        "missing_ratio": round(missing_ratio, 4),
        "uncertainty_keyword_hits": uncertainty_hits,
        "expect_uncertainty": expect_uncertainty,
    }
    return round(score, 2), issues, details


def _collect_restart_evidence_text(dashboard_inspection: dict[str, Any]) -> str:
    fragments: list[str] = []
    for dash in dashboard_inspection.get("dashboards", []) or []:
        fragments.append(str(dash.get("title") or ""))
        for panel in dash.get("panels", []) or []:
            fragments.append(str(panel.get("title") or ""))
            fragments.append(str(panel.get("description") or ""))
            for target in panel.get("targets", []) or []:
                if isinstance(target, dict):
                    fragments.append(str(target.get("expr") or target.get("query") or ""))
                else:
                    fragments.append(str(target))
            metrics = panel.get("metrics") or {}
            if isinstance(metrics, dict):
                for key in ("status", "reason", "error"):
                    val = metrics.get(key)
                    if val is not None:
                        fragments.append(str(val))
                for ser in metrics.get("series", []) or []:
                    if not isinstance(ser, dict):
                        continue
                    fragments.append(str(ser.get("name") or ""))
                    fragments.append(str(ser.get("refId") or ""))
            elif isinstance(metrics, list):
                for item in metrics:
                    fragments.append(str(item))
    return " ".join(fragments).lower()


def _score_restart_cause_diagnosis(
    report_text: str,
    dashboard_inspection: dict[str, Any],
) -> tuple[float, list[str], dict[str, Any]]:
    issues: list[str] = []
    report_lower = report_text.lower()
    evidence_text = _collect_restart_evidence_text(dashboard_inspection)

    report_mentions_oom = any(kw in report_lower for kw in OOM_REPORT_KEYWORDS)
    report_mentions_scheduling = any(kw in report_lower for kw in SCHEDULING_REPORT_KEYWORDS)

    has_oom_signal = any(kw in evidence_text for kw in OOM_SIGNAL_KEYWORDS)
    has_scheduling_signal = any(kw in evidence_text for kw in SCHEDULING_SIGNAL_KEYWORDS)

    score = 100.0
    if report_mentions_oom and not has_oom_signal:
        issues.append(
            "Report attributes restart to OOM without explicit OOM evidence in source metrics"
        )
        score = 20.0 if has_scheduling_signal else 40.0

    if report_mentions_oom and has_scheduling_signal and not report_mentions_scheduling:
        issues.append(
            "Report mentions OOM but misses scheduling/eviction signals that indicate non-OOM restart"
        )
        score = min(score, 35.0)

    if has_scheduling_signal and not report_mentions_scheduling and not report_mentions_oom:
        score = min(score, 75.0)
        issues.append(
            "Scheduling/eviction evidence exists but restart cause classification is missing"
        )

    details = {
        "report_mentions_oom": report_mentions_oom,
        "report_mentions_scheduling": report_mentions_scheduling,
        "has_oom_signal": has_oom_signal,
        "has_scheduling_signal": has_scheduling_signal,
    }
    return round(score, 2), issues, details


def evaluate_report(
    report_text: str,
    dashboard_inspection: dict[str, Any],
    alert_inspection: dict[str, Any],
) -> dict[str, Any]:
    language = _detect_language(report_text)

    structure_score, structure_issues = _score_structure(report_text, language)
    factual_score, factual_issues, factual_details = _score_factual_grounding(
        report_text,
        dashboard_inspection,
        alert_inspection,
    )
    action_score, action_issues, action_details = _score_actionability(report_text)
    uncertainty_score, uncertainty_issues, uncertainty_details = _score_uncertainty_handling(
        report_text,
        dashboard_inspection,
    )
    restart_score, restart_issues, restart_details = _score_restart_cause_diagnosis(
        report_text,
        dashboard_inspection,
    )

    breakdown = ScoreBreakdown(
        structure=structure_score,
        factual_grounding=factual_score,
        actionability=action_score,
        uncertainty_handling=uncertainty_score,
        restart_cause_diagnosis=restart_score,
    )

    issues = (
        structure_issues
        + factual_issues
        + action_issues
        + uncertainty_issues
        + restart_issues
    )

    return {
        "language": language,
        "scores": {
            "total": breakdown.total,
            "structure": structure_score,
            "factual_grounding": factual_score,
            "actionability": action_score,
            "uncertainty_handling": uncertainty_score,
            "restart_cause_diagnosis": restart_score,
        },
        "details": {
            "factual": factual_details,
            "actionability": action_details,
            "uncertainty": uncertainty_details,
            "restart_cause_diagnosis": restart_details,
        },
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated Grafana daily report quality and apply CI gates.",
    )
    parser.add_argument("--report-file", required=True, help="Path to report text file")
    parser.add_argument(
        "--dashboard-inspection-file",
        required=True,
        help="Path to dashboard inspection JSON",
    )
    parser.add_argument(
        "--alert-inspection-file",
        required=True,
        help="Path to alert inspection JSON",
    )
    parser.add_argument(
        "--output-file",
        default="report-eval-result.json",
        help="Evaluation output JSON path",
    )
    parser.add_argument("--min-total-score", type=float, default=80.0)
    parser.add_argument("--min-structure-score", type=float, default=85.0)
    parser.add_argument("--min-factual-score", type=float, default=75.0)
    parser.add_argument("--min-actionability-score", type=float, default=60.0)
    parser.add_argument("--min-restart-cause-score", type=float, default=70.0)
    return parser.parse_args()


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    args = parse_args()

    report_file = Path(args.report_file)
    dashboard_file = Path(args.dashboard_inspection_file)
    alert_file = Path(args.alert_inspection_file)
    output_file = Path(args.output_file)

    report_text = _load_text(report_file)
    dashboard_inspection = _load_json(dashboard_file)
    alert_inspection = _load_json(alert_file)

    result = evaluate_report(report_text, dashboard_inspection, alert_inspection)

    gates = {
        "min_total_score": args.min_total_score,
        "min_structure_score": args.min_structure_score,
        "min_factual_score": args.min_factual_score,
        "min_actionability_score": args.min_actionability_score,
        "min_restart_cause_score": args.min_restart_cause_score,
    }
    scores = result["scores"]

    failed_gates = []
    if scores["total"] < gates["min_total_score"]:
        failed_gates.append("total")
    if scores["structure"] < gates["min_structure_score"]:
        failed_gates.append("structure")
    if scores["factual_grounding"] < gates["min_factual_score"]:
        failed_gates.append("factual_grounding")
    if scores["actionability"] < gates["min_actionability_score"]:
        failed_gates.append("actionability")
    if scores["restart_cause_diagnosis"] < gates["min_restart_cause_score"]:
        failed_gates.append("restart_cause_diagnosis")

    result["gates"] = gates
    result["failed_gates"] = failed_gates
    result["pass"] = len(failed_gates) == 0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

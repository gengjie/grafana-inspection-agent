"""Report generator module."""

from datetime import datetime
import re
from typing import Any

from dateutil.tz import tzutc
from markdown import markdown


class ReportGenerator:
    """Generate formatted inspection reports."""

    @staticmethod
    def _sanitize_markdown_for_email(content: str, language: str = "zh", max_chars: int = 12000) -> str:
        """Sanitize markdown into a renderer-friendly subset for downstream mail/html pipelines."""
        text = (content or "").strip()
        if not text:
            return ""

        # Drop fenced code block markers, keep plain text content.
        text = text.replace("```markdown", "").replace("```md", "").replace("```", "")

        # Convert markdown table rows to plain bullet lines to avoid broken rendering.
        sanitized_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line:
                sanitized_lines.append("")
                continue
            if line.strip().startswith("|") and line.strip().endswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells and not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    sanitized_lines.append("- " + " | ".join(cells))
                continue
            sanitized_lines.append(line)

        text = "\n".join(sanitized_lines)

        # Remove raw HTML tags occasionally emitted by LLM.
        text = re.sub(r"<[^>]+>", "", text)

        if len(text) > max_chars:
            suffix = "\n\n...(content truncated)" if language == "en" else "\n\n...(内容已截断)"
            text = text[:max_chars] + suffix

        return text.strip()

    @staticmethod
    def format_daily_report(
        dashboard_summary: str,
        alert_summary: str,
        inspection_time: str | None = None,
        language: str = "zh",
        long_term_summary: str | None = None,
    ) -> str:
        """Format complete daily report.

        Args:
            dashboard_summary: Dashboard inspection summary from LLM
            alert_summary: Alert inspection summary from LLM
            inspection_time: Inspection timestamp
            language: Report language ('zh' for Chinese, 'en' for English)

        Returns:
            Formatted daily report
        """
        if inspection_time is None:
            inspection_time = datetime.now(tzutc()).strftime("%Y-%m-%d %H:%M:%S UTC")

        if language == "en":
            long_term_block = ""
            if long_term_summary is not None:
                summary_text = long_term_summary or "No long-term summary available."
                long_term_block = f"""
{'─' * 80}
III. Long-Term Inspection Summary
{'─' * 80}

{summary_text}
"""

            report = f"""
{'=' * 80}
Grafana Daily Inspection Report
{'=' * 80}

Generated Time: {inspection_time}
Inspection Period: Last 24 hours

{'─' * 80}
I. Dashboard Inspection Summary
{'─' * 80}

{dashboard_summary}

{'─' * 80}
II. Alert Inspection Report
{'─' * 80}

{alert_summary}
{long_term_block}

{'─' * 80}
End of Report
{'─' * 80}
"""
        else:
            long_term_block = ""
            if long_term_summary is not None:
                summary_text = long_term_summary or "暂无长期巡检总结。"
                long_term_block = f"""
{'─' * 80}
三、长期巡检总结
{'─' * 80}

{summary_text}
"""

            report = f"""
{'=' * 80}
Grafana 巡检日报
{'=' * 80}

生成时间: {inspection_time}
巡检周期: 过去24小时

{'─' * 80}
一、Dashboard巡检总结
{'─' * 80}

{dashboard_summary}

{'─' * 80}
二、告警巡检报告
{'─' * 80}

{alert_summary}
{long_term_block}

{'─' * 80}
报告结束
{'─' * 80}
"""
        return report.strip()

    @staticmethod
    def format_report_for_email(report: str, language: str = "zh") -> tuple[str, str]:
        """Format report for email sending.

        Args:
            report: Plain text report
            language: Report language ('zh' for Chinese, 'en' for English)

        Returns:
            Tuple of (subject, html_body)
        """
        if language == "en":
            inspection_date = datetime.now(tzutc()).strftime("%B %d, %Y")
            subject = f"Grafana Daily Inspection Report - {inspection_date}"
        else:
            inspection_date = datetime.now(tzutc()).strftime("%Y年%m月%d日")
            subject = f"Grafana巡检日报 - {inspection_date}"

        # Remove optional YAML front matter and cross-line separators for a cleaner email
        cleaned_report = report.strip() if report else ""
        if cleaned_report.startswith("---"):
            parts = cleaned_report.split("---", 2)
            if len(parts) == 3:
                cleaned_report = parts[2].strip()

        # Strip long separator lines made of repeated characters (e.g., '─', '=', '-')
        def _is_separator_line(line: str) -> bool:
            l = line.strip()
            if not l:
                return False
            return all(ch in "=-─_ " for ch in l) and len(l) >= 6

        cleaned_lines = [ln for ln in cleaned_report.splitlines() if not _is_separator_line(ln)]
        cleaned_report = "\n".join(cleaned_lines).strip()
        cleaned_report = ReportGenerator._sanitize_markdown_for_email(
            cleaned_report,
            language=language,
            max_chars=14000,
        )

        html_content = markdown(
            cleaned_report or ("No report content" if language == "en" else "暂无报告内容"),
            extensions=["extra", "sane_lists", "smarty"],
            output_format="html5",
        )

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        /* Minimal, unified 12px font and simplified layout */
        body {{
            font-family: 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
            line-height: 1.5;
            color: #333;
            background-color: #ffffff;
            padding: 16px;
            font-size: 12px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 0;
        }}
        h1, h2, h3, p, ul, ol, li, code, pre, table, th, td {{
            font-size: 12px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            font-weight: 600;
        }}
        .meta {{
            color: #666;
            margin: 4px 0 10px;
        }}
        .report-body {{
            margin-top: 10px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 8px 0;
        }}
        th, td {{
            border: 1px solid #e6e6e6;
            padding: 6px 8px;
            text-align: left;
        }}
        code {{
            background-color: #f5f5f5;
            border: 1px solid #e6e6e6;
            border-radius: 3px;
            padding: 1px 3px;
        }}
        pre {{
            background-color: #fafafa;
            border: 1px solid #e6e6e6;
            border-radius: 3px;
            padding: 8px;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .footer {{
            margin-top: 14px;
            color: #888;
        }}
    </style>
    <title>{"Grafana Daily Inspection Report" if language == "en" else "Grafana巡检日报"}</title>
</head>
<body>
    <div class="container">
        <h1>{"Grafana Daily Inspection Report" if language == "en" else "Grafana巡检日报"}</h1>
        <p class="meta"><strong>{"Generated Time" if language == "en" else "生成时间"}:</strong> {datetime.now(tzutc()).strftime("%Y-%m-%d %H:%M:%S UTC")}</p>
        <p class="meta"><strong>{"Inspection Period" if language == "en" else "巡检周期"}:</strong> {"Last 24 hours" if language == "en" else "过去24小时"}</p>

        <div class="report-body">{html_content}</div>

        <div class="footer">
            <p>{"This report is automatically generated by Grafana Inspection Agent" if language == "en" else "本报告由Grafana Inspection Agent自动生成"}</p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html_body

    @staticmethod
    def format_jvm_report_for_email(
        jvm_report: str,
        inspection_time: str | None = None,
        language: str = "zh",
    ) -> tuple[str, str]:
        """Format JVM health analysis report for email sending.

        Args:
            jvm_report: JVM analysis report text from LLM
            inspection_time: Inspection timestamp
            language: Report language ('zh' for Chinese, 'en' for English)

        Returns:
            Tuple of (subject, html_body)
        """
        if language == "en":
            inspection_date = datetime.now(tzutc()).strftime("%B %d, %Y")
            subject = f"JVM Health Analysis Report - {inspection_date}"
            title = "JVM Health Analysis Report"
            gen_label = "Generated Time"
            gen_time = inspection_time or datetime.now(tzutc()).strftime("%Y-%m-%d %H:%M:%S UTC")
            footer = "This report is automatically generated by Grafana Inspection Agent"
        else:
            inspection_date = datetime.now(tzutc()).strftime("%Y年%m月%d日")
            subject = f"JVM 健康分析报告 - {inspection_date}"
            title = "JVM 健康分析报告"
            gen_label = "生成时间"
            gen_time = inspection_time or datetime.now(tzutc()).strftime("%Y-%m-%d %H:%M:%S UTC")
            footer = "本报告由Grafana Inspection Agent自动生成"

        cleaned_report = jvm_report.strip() if jvm_report else ""
        if cleaned_report.startswith("---"):
            parts = cleaned_report.split("---", 2)
            if len(parts) == 3:
                cleaned_report = parts[2].strip()

        def _is_separator_line(line: str) -> bool:
            l = line.strip()
            if not l:
                return False
            return all(ch in "=-─_ " for ch in l) and len(l) >= 6

        cleaned_lines = [ln for ln in cleaned_report.splitlines() if not _is_separator_line(ln)]
        cleaned_report = "\n".join(cleaned_lines).strip()
        cleaned_report = ReportGenerator._sanitize_markdown_for_email(
            cleaned_report,
            language=language,
            max_chars=10000,
        )

        html_content = markdown(
            cleaned_report or ("No JVM report content" if language == "en" else "暂无JVM报告内容"),
            extensions=["extra", "sane_lists", "smarty"],
            output_format="html5",
        )

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
            line-height: 1.5;
            color: #333;
            background-color: #ffffff;
            padding: 16px;
            font-size: 12px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 0;
        }}
        h1, h2, h3, p, ul, ol, li, code, pre, table, th, td {{
            font-size: 12px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            font-weight: 600;
        }}
        .meta {{
            color: #666;
            margin: 4px 0 10px;
        }}
        .report-body {{
            margin-top: 10px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 8px 0;
        }}
        th, td {{
            border: 1px solid #e6e6e6;
            padding: 6px 8px;
            text-align: left;
        }}
        code {{
            background-color: #f5f5f5;
            border: 1px solid #e6e6e6;
            border-radius: 3px;
            padding: 1px 3px;
        }}
        pre {{
            background-color: #fafafa;
            border: 1px solid #e6e6e6;
            border-radius: 3px;
            padding: 8px;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .footer {{
            margin-top: 14px;
            color: #888;
        }}
    </style>
    <title>{title}</title>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <p class="meta"><strong>{gen_label}:</strong> {gen_time}</p>

        <div class="report-body">{html_content}</div>

        <div class="footer">
            <p>{footer}</p>
        </div>
    </div>
</body>
</html>
"""
        return subject, html_body

    @staticmethod
    def format_report_for_teams(
        dashboard_summary: str,
        alert_summary: str,
        grafana_url: str | None = None,
        language: str = "zh",
    ) -> dict[str, Any]:
        """Format report for Teams webhook.

        Args:
            dashboard_summary: Dashboard inspection summary from LLM
            alert_summary: Alert inspection summary from LLM
            grafana_url: Grafana instance URL for "View Report" link
            language: Report language ('zh' for Chinese, 'en' for English)

        Returns:
            Teams webhook payload
        """
        if language == "en":
            inspection_date = datetime.now(tzutc()).strftime("%B %d, %Y")
            title = f"Grafana Daily Inspection Report - {inspection_date}"
            dashboard_title = "📊 Dashboard Inspection Summary"
            alert_title = "🚨 Alert Inspection Report"
            no_dashboard_text = "No Dashboard inspection data"
            no_alert_text = "No alert data"
            truncate_text = "\n\n...(content truncated)"
            view_grafana_text = "View Grafana"
        else:
            inspection_date = datetime.now(tzutc()).strftime("%Y年%m月%d日")
            title = f"Grafana巡检日报 - {inspection_date}"
            dashboard_title = "📊 Dashboard巡检总结"
            alert_title = "🚨 告警巡检报告"
            no_dashboard_text = "无Dashboard巡检数据"
            no_alert_text = "无告警数据"
            truncate_text = "\n\n...(内容已截断)"
            view_grafana_text = "查看Grafana"

        # Clean and truncate content for Teams
        # Teams has a limit of ~8000 characters per message, so we limit each section
        max_length = 3000

        # Clean dashboard summary
        dashboard_text = dashboard_summary.strip() if dashboard_summary else ""
        if len(dashboard_text) > max_length:
            dashboard_text = dashboard_text[:max_length] + truncate_text
        if not dashboard_text:
            dashboard_text = no_dashboard_text

        # Clean alert summary
        alert_text = alert_summary.strip() if alert_summary else ""
        if len(alert_text) > max_length:
            alert_text = alert_text[:max_length] + truncate_text
        if not alert_text:
            alert_text = no_alert_text

        # Build Teams message card
        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "themeColor": "0078D4",
            "title": title,
            "sections": [
                {
                    "activityTitle": dashboard_title,
                    "text": dashboard_text,
                    "markdown": True,
                },
                {
                    "activityTitle": alert_title,
                    "text": alert_text,
                    "markdown": True,
                },
            ],
        }

        # Add "View Report" action if Grafana URL is provided
        if grafana_url:
            payload["potentialAction"] = [
                {
                    "@type": "OpenUri",
                    "name": view_grafana_text,
                    "targets": [
                        {
                            "os": "default",
                            "uri": grafana_url,
                        }
                    ],
                }
            ]

        return payload


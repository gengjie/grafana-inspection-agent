from grafana_agent_langgraph.report_generator import ReportGenerator


def test_sanitize_markdown_keeps_comparison_operators_and_removes_html_tags() -> None:
    content = """
### 2. GC 行为
- GC 累计停顿 < 15 ms，运行优秀。
- 建立告警规则：线程数 > 500 时告警。
<p>temporary html tag</p>
"""

    sanitized = ReportGenerator._sanitize_markdown_for_email(content)

    assert "< 15 ms" in sanitized
    assert "> 500" in sanitized
    assert "temporary html tag" in sanitized
    assert "<p>" not in sanitized
    assert "</p>" not in sanitized

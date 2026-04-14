"""Grafana API client module."""

import asyncio
from datetime import datetime, timedelta
from typing import Any
import math
import re

import aiohttp
from dateutil import parser as date_parser
from dateutil.tz import tzutc
import json

from .logger import get_logger

logger = get_logger("grafana_client")


class GrafanaClient:
    """Async client for Grafana API."""

    def __init__(self, url: str, api_key: str, timeout: int = 300):
        """Initialize Grafana client.

        Args:
            url: Grafana instance URL
            api_key: Grafana API key
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # limit concurrent metric queries to avoid overload
        self._metrics_semaphore = asyncio.Semaphore(5)
        self._ds_cache: dict[str, dict[str, Any]] = {}

    async def _request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> dict[str, Any] | list[Any]:
        """Make async HTTP request to Grafana API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional arguments for aiohttp request

        Returns:
            Response JSON data

        Raises:
            aiohttp.ClientError: If request fails
        """
        url = f"{self.url}/api/{endpoint.lstrip('/')}"
        logger.debug(f"Making {method} request to {url}")
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(method, url, headers=self.headers, **kwargs) as response:
                    response.raise_for_status()
                    data = await response.json()
                    logger.debug(f"Request successful: {method} {endpoint}")
                    return data
        except aiohttp.ClientError as e:
            logger.error(f"Request failed: {method} {endpoint} - {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during request: {method} {endpoint} - {e}")
            raise

    async def get_dashboards(self) -> list[dict[str, Any]]:
        """Get all dashboards.

        Returns:
            List of dashboard metadata
        """
        data = await self._request("GET", "/search?type=dash-db")
        return data

    async def get_dashboard(self, uid: str) -> dict[str, Any]:
        """Get dashboard by UID.

        Args:
            uid: Dashboard UID

        Returns:
            Dashboard data
        """
        data = await self._request("GET", f"/dashboards/uid/{uid}")
        return data.get("dashboard", {})

    async def get_all_dashboards_with_panels(self) -> list[dict[str, Any]]:
        """Get all dashboards with their panels.

        Returns:
            List of dashboards with panel information
        """
        dashboards_meta = await self.get_dashboards()
        self._debug_dashboards_meta(dashboards_meta)

        tasks = [self.get_dashboard(dash["uid"]) for dash in dashboards_meta]
        dashboards = await asyncio.gather(*tasks, return_exceptions=True)

        self._debug_dashboards_payload(dashboards)
        result = []
        for dash, meta in zip(dashboards, dashboards_meta):
            if isinstance(dash, Exception):
                logger.warning(f"Failed to get dashboard {meta.get('uid', 'unknown')}: {dash}")
                continue
            flattened_panels = self._extract_panels(dash)
            result.append(
                {
                    "uid": meta.get("uid"),
                    "title": meta.get("title"),
                    "url": meta.get("url"),
                    "dashboard": dash,
                    "panels": flattened_panels,
                    "raw_panel_count": len(dash.get("panels", []) or []),
                }
            )
        logger.debug(f"Successfully retrieved {len(result)} dashboards")
        return result

    def _debug_dashboards_meta(self, dashboards_meta: list[dict[str, Any]]) -> None:
        """Log the shape of dashboards metadata returned by /search."""

        try:
            logger.info(
                "Dashboards meta count=%s sample_keys=%s",
                len(dashboards_meta),
                list(dashboards_meta[0].keys()) if dashboards_meta else [],
            )
            for item in dashboards_meta[:3]:
                logger.debug(
                    "Meta item uid=%s title=%s url=%s tags=%s folderTitle=%s type=%s",
                    item.get("uid"),
                    item.get("title"),
                    item.get("url"),
                    item.get("tags"),
                    item.get("folderTitle"),
                    item.get("type"),
                )
        except Exception:
            pass

    def _debug_dashboards_payload(self, dashboards: list[Any]) -> None:
        """Log the shape of dashboards payloads returned by /dashboards/uid/{uid}."""

        try:
            logger.info("Dashboards payload count=%s", len(dashboards))
            for dash in dashboards[:2]:
                if isinstance(dash, Exception):
                    logger.warning("Dashboard payload is exception: %s", dash)
                    continue

                keys = list(dash.keys()) if isinstance(dash, dict) else []
                panels = dash.get("panels", []) if isinstance(dash, dict) else []
                rows = dash.get("rows", []) if isinstance(dash, dict) else []

                logger.debug(
                    "Dashboard title=%s uid=%s keys=%s panels_len=%s rows_len=%s",
                    dash.get("title"),
                    dash.get("uid"),
                    keys,
                    len(panels) if panels else 0,
                    len(rows) if rows else 0,
                )

                if panels:
                    panel_keys = list(panels[0].keys()) if isinstance(panels[0], dict) else []
                    logger.debug(
                        "Panel[0] keys=%s type=%s title=%s",
                        panel_keys,
                        panels[0].get("type") if isinstance(panels[0], dict) else None,
                        panels[0].get("title") if isinstance(panels[0], dict) else None,
                    )
                if rows:
                    row_keys = list(rows[0].keys()) if isinstance(rows[0], dict) else []
                    logger.debug("Row[0] keys=%s", row_keys)
        except Exception:
            pass

    def _extract_panels(self, dashboard: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten panels, including those under rows."""

        flat: list[dict[str, Any]] = []

        def walk(panels: list[dict[str, Any]] | None) -> None:
            if not panels:
                return
            for panel in panels:
                if not isinstance(panel, dict):
                    continue
                # Row panels are containers, not queryable metrics panels.
                if panel.get("type") == "row":
                    walk(panel.get("panels"))
                    continue
                # rows may contain nested panels
                children = panel.get("panels")
                if children:
                    walk(children)
                    continue
                flat.append(panel)

        walk(dashboard.get("panels"))
        walk(dashboard.get("rows"))
        return flat

    async def _get_datasource_by_uid(self, uid: str) -> dict[str, Any] | None:
        if not uid:
            return None
        if uid in self._ds_cache:
            return self._ds_cache[uid]
        try:
            ds = await self._request("GET", f"/datasources/uid/{uid}")
            if isinstance(ds, dict):
                norm = {"uid": ds.get("uid"), "type": ds.get("type"), "name": ds.get("name")}
                self._ds_cache[uid] = norm
                return norm
        except Exception as e:
            logger.warning("Failed to resolve datasource uid=%s: %s", uid, e)
        return None

    async def _normalize_datasource(self, ds: Any) -> dict[str, Any] | None:
        if not ds:
            return None
        if isinstance(ds, dict):
            uid = ds.get("uid")
            typ = ds.get("type")
            if uid and typ:
                return {"uid": uid, "type": typ}
            if uid:
                fetched = await self._get_datasource_by_uid(uid)
                return fetched
            name = ds.get("name")
            if name:
                fetched = await self._request("GET", f"/datasources/name/{name}")
                if isinstance(fetched, dict):
                    norm = {"uid": fetched.get("uid"), "type": fetched.get("type"), "name": fetched.get("name")}
                    self._ds_cache[norm.get("uid") or name] = norm
                    return norm
            return None
        if isinstance(ds, str):
            fetched = await self._get_datasource_by_uid(ds)
            if fetched:
                return fetched
            try:
                fetched = await self._request("GET", f"/datasources/name/{ds}")
                if isinstance(fetched, dict):
                    norm = {"uid": fetched.get("uid"), "type": fetched.get("type"), "name": fetched.get("name")}
                    self._ds_cache[norm.get("uid") or ds] = norm
                    return norm
            except Exception:
                pass
        return None

    def _basic_stats(self, points: list[list[float]]) -> dict[str, Any]:
        if not points:
            return {"count": 0}
        values = [p[0] for p in points if p and len(p) >= 1 and p[0] is not None and not math.isnan(p[0])]
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "latest": values[-1],
        }

    async def _query_panel_metrics(
        self,
        panel: dict[str, Any],
        start_ms: int,
        end_ms: int,
        template_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        panel_ds = await self._normalize_datasource(panel.get("datasource"))
        targets = panel.get("targets") or []

        queries = []
        for t in targets:
            # Skip hidden targets to avoid unnecessary metrics queries
            if bool(t.get("hide")) or bool(t.get("hidden")):
                continue
            q = dict(t)
            q["refId"] = q.get("refId") or q.get("ref_id") or "A"
            target_ds = await self._normalize_datasource(q.get("datasource")) or panel_ds
            if target_ds:
                q["datasource"] = target_ds
            else:
                continue
            q.setdefault("intervalMs", 60000)
            q.setdefault("maxDataPoints", 500)
            expr_val = q.get("expr") or q.get("query") or q.get("rawSql")
            if template_vars:
                expr_val = self._substitute_template_vars(expr_val, template_vars)
                if "expr" in q:
                    q["expr"] = expr_val
                if "query" in q:
                    q["query"] = expr_val
                if "rawSql" in q:
                    q["rawSql"] = expr_val
            if not expr_val:
                continue
            queries.append(q)

        if not queries:
            return {"status": "skipped", "reason": "no_query_expr_or_ds"}

        payload = {
            "queries": queries,
            # Grafana expects epoch milliseconds for from/to in /ds/query
            "from": str(start_ms),
            "to": str(end_ms),
            # keep range for plugins that still read ISO
            "range": {
                "from": datetime.fromtimestamp(start_ms / 1000, tzutc()).isoformat(),
                "to": datetime.fromtimestamp(end_ms / 1000, tzutc()).isoformat(),
            },
            "timezone": "UTC",
        }

        async with self._metrics_semaphore:
            try:
                # logger.info("DS query payload : %s", payload)
                response = await self._request("POST", "/ds/query", json=payload)
                # logger.info("Query Metrics Response: %s", response)
            except Exception as e:
                logger.error("Failed to fetch metrics for panel %s (%s): %s, payload: %s", panel.get("title"), panel.get("id"), e, payload)
                return {"status": "error", "error": str(e)}

        results = {}
        if isinstance(response, dict):
            raw_results = response.get("results") or {}
            # Grafana new dataframe format
            for ref_id, payload in raw_results.items():
                frames = payload.get("frames") or []
                series_list = []
                for frame in frames:
                    data = frame.get("data") or {}
                    values = data.get("values") or []
                    if len(values) < 2:
                        continue
                    times = values[0] or []
                    vals = values[1] or []
                    # zip value and timestamp so _basic_stats can work on value index 0
                    points = [[v, t] for v, t in zip(vals, times)]
                    schema = frame.get("schema") or {}
                    fields = schema.get("fields") or []
                    name = None
                    if len(fields) >= 2:
                        name = fields[1].get("config", {}).get("displayNameFromDS") or fields[1].get("name")
                        series_list.append({"name": name, "points": points})
            # keep compatibility with later parsing loop
            results[ref_id] = {"series": series_list}
        if not results:
            logger.warning("Empty response for metrics: %s .", payload)
            return {"status": "empty"}

        parsed_series = []
        for ref_id, series in results.items():
            for ser in series.get("series", []) or []:
                points = ser.get("points") or []
                stats = self._basic_stats(points)
                parsed_series.append(
                    {
                        "refId": ref_id,
                        "name": ser.get("name"),
                        "points_count": len(points),
                        "stats": stats,
                    }
                )

        status = "ok" if parsed_series else "empty"
        return {"status": status, "series": parsed_series}

    async def get_alert_rules(self) -> list[dict[str, Any]]:
        """Get all alerting rules.

        Returns:
            List of alerting rules
        """
        data = await self._request("GET", "/ruler/grafana/api/v1/rules")
        # Flatten the nested structure
        rules = []
        for namespace, groups in data.items():
            for group in groups:
                for rule in group.get("rules", []):
                    rule["namespace"] = namespace
                    rule["group"] = group.get("name")
                    rules.append(rule)
        return rules

    async def get_alert_instances(self) -> list[dict[str, Any]]:
        """Get all active alert instances.

        Returns:
            List of active alert instances
        """
        data = await self._request("GET", "/alertmanager/grafana/api/v2/alerts")
        return data

    async def get_alert_history(
        self, lookback_hours: int = 24
    ) -> list[dict[str, Any]]:
        """Get alert history for the specified lookback period.

        Args:
            lookback_hours: Hours to look back

        Returns:
            List of alert history entries
        """
        # Get all alert rules
        rules = await self.get_alert_rules()
        instances = await self.get_alert_instances()

        # Calculate time range
        now = datetime.now(tzutc())
        lookback_time = now - timedelta(hours=lookback_hours)

        # Filter alerts within the time range
        alert_history = []
        for instance in instances:
            starts_at = date_parser.parse(instance.get("startsAt", ""))
            if starts_at and starts_at >= lookback_time:
                # Find corresponding rule
                rule_name = instance.get("labels", {}).get("alertname")
                rule = next((r for r in rules if r.get("name") == rule_name), None)

                alert_history.append(
                    {
                        "instance": instance,
                        "rule": rule,
                        "starts_at": starts_at.isoformat(),
                        "status": instance.get("status", {}).get("state", "unknown"),
                    }
                )

        return sorted(alert_history, key=lambda x: x["starts_at"], reverse=True)

    async def get_dashboard_panel_metrics(
        self, dashboard_uid: str, panel_id: int, time_range: tuple[datetime, datetime]
    ) -> dict[str, Any]:
        """Get panel metrics for a specific time range.

        Args:
            dashboard_uid: Dashboard UID
            panel_id: Panel ID
            time_range: Tuple of (start_time, end_time)

        Returns:
            Panel metrics data
        """
        start_time = int(time_range[0].timestamp() * 1000)
        end_time = int(time_range[1].timestamp() * 1000)

        # This would require querying the actual data source
        # For now, we'll return panel metadata
        dashboard = await self.get_dashboard(dashboard_uid)
        panel = next((p for p in dashboard.get("panels", []) if p.get("id") == panel_id), None)

        return {
            "panel": panel,
            "time_range": {
                "start": time_range[0].isoformat(),
                "end": time_range[1].isoformat(),
            },
        }

    async def inspect_dashboards(
        self, lookback_hours: int = 24
    ) -> dict[str, Any]:
        """Inspect all dashboards for the specified lookback period.

        Args:
            lookback_hours: Hours to look back

        Returns:
            Inspection results
        """
        dashboards = await self.get_all_dashboards_with_panels()
        now = datetime.now(tzutc())
        lookback_time = now - timedelta(hours=lookback_hours)

        inspection_results = {
            "inspection_time": now.isoformat(),
            "lookback_period": {
                "start": lookback_time.isoformat(),
                "end": now.isoformat(),
                "hours": lookback_hours,
            },
            "dashboards": [],
            "summary": {
                "total_dashboards": len(dashboards),
                "total_panels": 0,
                "dashboards_with_issues": 0,
            },
        }

        for dash_info in dashboards:
            panels = dash_info.get("panels", [])
            panel_count = len(panels)

            inspection_results["summary"]["total_panels"] += panel_count

            template_vars = self._build_template_vars(dash_info.get("dashboard", {}))

            async def process_panel(panel: dict[str, Any]) -> dict[str, Any]:
                metrics = await self._query_panel_metrics(
                    panel,
                    start_ms=int(lookback_time.timestamp() * 1000),
                    end_ms=int(now.timestamp() * 1000),
                    template_vars=template_vars,
                )
                logger.info(f"Panel Id: {panel.get('id')}, \t\t Metrics: {metrics}")
                return {
                    "id": panel.get("id"),
                    "title": panel.get("title"),
                    "type": panel.get("type"),
                    "targets": panel.get("targets", []),
                    "metrics": metrics,
                }

            panel_results = await asyncio.gather(*[process_panel(p) for p in panels])

            dashboard_result = {
                "uid": dash_info.get("uid"),
                "title": dash_info.get("title"),
                "url": dash_info.get("url"),
                "panel_count": panel_count,
                "panels": panel_results,
            }
            inspection_results["dashboards"].append(dashboard_result)

        return inspection_results

    async def inspect_alerts(self, lookback_hours: int = 24) -> dict[str, Any]:
        """Inspect all alerts for the specified lookback period.

        Args:
            lookback_hours: Hours to look back

        Returns:
            Alert inspection results
        """
        alert_history = await self.get_alert_history(lookback_hours)
        rules = await self.get_alert_rules()
        active_instances = await self.get_alert_instances()

        now = datetime.now(tzutc())
        lookback_time = now - timedelta(hours=lookback_hours)

        inspection_results = {
            "inspection_time": now.isoformat(),
            "lookback_period": {
                "start": lookback_time.isoformat(),
                "end": now.isoformat(),
                "hours": lookback_hours,
            },
            "alert_history": alert_history,
            "active_alerts": active_instances,
            "all_rules": rules,
            "summary": {
                "total_rules": len(rules),
                "active_alerts_count": len(active_instances),
                "alerts_in_period": len(alert_history),
                "firing_alerts": len([a for a in active_instances if a.get("status", {}).get("state") == "active"]),
            },
        }

        return inspection_results

    def _build_template_vars(self, dashboard: dict[str, Any]) -> dict[str, Any]:
        """Collect dashboard variable defaults for template substitution."""
        templating = dashboard.get("templating", {}) or {}
        var_list = templating.get("list", []) or []
        vars_map: dict[str, Any] = {}

        for item in var_list:
            name = item.get("name")
            if not name:
                continue
            current = item.get("current", {}) or {}
            val = current.get("value")

            if (val is None or val == "") and item.get("options"):
                selected = next((o for o in item.get("options", []) if o.get("selected")), None)
                val = selected.get("value") if selected else item.get("options", [{}])[0].get("value")

            if isinstance(val, str) and val.lower() in {"$__all", "__all", "all"}:
                val = ".*"

            if isinstance(val, list):
                val = [".*" if (isinstance(v, str) and v.lower() in {"$__all", "__all", "all"}) else v for v in val]

            vars_map[name] = val if val is not None else ""
            # Grafana often references variables with a var- prefix in scopedVars
            vars_map[f"var-{name}"] = vars_map[name]

        return vars_map

    def _substitute_template_vars(self, expr: Any, vars_map: dict[str, Any]) -> Any:
        """Replace Grafana template variables in query expressions using default values."""
        if not isinstance(expr, str):
            return expr

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in vars_map:
                return match.group(0)
            val = vars_map[key]
            if isinstance(val, list):
                joined = "|".join(str(v) for v in val if v is not None)
                return joined
            return str(val)

        return re.sub(r"\$\{?([A-Za-z0-9_\-]+)\}?", replace, expr)


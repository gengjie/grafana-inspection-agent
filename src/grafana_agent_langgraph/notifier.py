"""Notification module for sending reports via Email and Teams."""

import json
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiohttp
from aiosmtplib import SMTP

from .logger import get_logger

logger = get_logger("notifier")


class Notifier:
    """Notification sender for Email and Teams."""

    def __init__(
        self,
        email_config: Any | None = None,
        teams_config: Any | None = None,
    ):
        """Initialize notifier.

        Args:
            email_config: Email configuration
            teams_config: Teams configuration
        """
        self.email_config = email_config
        self.teams_config = teams_config

    async def send_email(
        self, subject: str, html_body: str, plain_text: str | None = None
    ) -> bool:
        """Send email notification.

        Args:
            subject: Email subject
            html_body: HTML email body
            plain_text: Plain text email body (optional)

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.email_config or not self.email_config.enabled:
            return False

        try:
            # Create message
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self.email_config.from_address
            message["To"] = ", ".join(self.email_config.to_addresses)

            # Add plain text part if provided
            if plain_text:
                part1 = MIMEText(plain_text, "plain", "utf-8")
                message.attach(part1)

            # Add HTML part
            part2 = MIMEText(html_body, "html", "utf-8")
            message.attach(part2)

            # Send email
            tls_context = None
            if self.email_config.use_tls:
                tls_context = ssl.create_default_context()
                tls_context.check_hostname = False
                tls_context.verify_mode = ssl.CERT_NONE
            async with SMTP(
                hostname=self.email_config.smtp_host,
                port=self.email_config.smtp_port,
                use_tls=self.email_config.use_tls,
                tls_context=tls_context,
            ) as smtp:
                await smtp.login(
                    self.email_config.smtp_user, self.email_config.smtp_password
                )
                await smtp.send_message(message)

            logger.info(f"Email sent successfully to {', '.join(self.email_config.to_addresses)}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False

    async def send_teams(self, payload: dict[str, Any]) -> bool:
        """Send Teams webhook notification.

        Args:
            payload: Teams webhook payload

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.teams_config or not self.teams_config.enabled:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.teams_config.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    response.raise_for_status()
                    logger.info("Teams notification sent successfully")
                    return True
        except Exception as e:
            logger.error(f"Failed to send Teams notification: {e}", exc_info=True)
            return False

    async def send_report(
        self,
        report: str,
        dashboard_summary: str,
        alert_summary: str,
        email_subject: str | None = None,
        email_html: str | None = None,
        grafana_url: str | None = None,
        language: str = "zh",
    ) -> dict[str, bool]:
        """Send report via all enabled notification channels.

        Args:
            report: Plain text report
            dashboard_summary: Dashboard inspection summary
            alert_summary: Alert inspection summary
            email_subject: Email subject (optional)
            email_html: Email HTML body (optional)
            grafana_url: Grafana instance URL for Teams link (optional)
            language: Report language ('zh' for Chinese, 'en' for English)

        Returns:
            Dictionary with notification results
        """
        results = {"email": False, "teams": False}

        # Send email if enabled
        if self.email_config and self.email_config.enabled:
            if email_subject and email_html:
                results["email"] = await self.send_email(
                    email_subject, email_html, plain_text=report
                )

        # Send Teams notification if enabled
        if self.teams_config and self.teams_config.enabled:
            # Import here to avoid circular dependency
            from .report_generator import ReportGenerator

            teams_payload = ReportGenerator.format_report_for_teams(
                dashboard_summary=dashboard_summary,
                alert_summary=alert_summary,
                grafana_url=grafana_url,
                language=language,
            )
            results["teams"] = await self.send_teams(teams_payload)

        return results


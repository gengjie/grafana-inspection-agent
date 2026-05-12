"""CLI entrypoint for LangGraph-based Grafana inspection agent."""

from __future__ import annotations

import asyncio
import sys

from .llm_client import LLMClient
from .logger import setup_logger
from .runtime import (
    init_logger_from_config,
    load_config,
    validate_base_config,
    validate_copilot_access_token,
)
from .workflow import run_daily_langgraph


async def _preflight_llm(config, logger) -> None:
    """Pre-flight check: verify Copilot LLM is reachable before heavy work."""
    client = LLMClient(
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
        request_timeout=config.llm.request_timeout,
    )
    await client.preflight()


async def main() -> int:
    """Main async entrypoint."""
    logger = setup_logger(level="INFO")

    try:
        config = load_config()
        logger = init_logger_from_config(config)

        validate_base_config(config)
        validate_copilot_access_token(config)

        # Preflight: verify Copilot LLM works before collecting Grafana data
        logger.info("Running Copilot LLM preflight check...")
        try:
            await _preflight_llm(config, logger)
        except Exception as exc:
            logger.error("Copilot LLM preflight failed: %s", exc)
            logger.error("Exiting — fix LLM connectivity before retrying.")
            return 1

        logger.info("Starting Grafana inspection (LangGraph mode)...")
        logger.info("Lookback period: %s hours", config.lookback_hours)
        logger.debug("Grafana URL: %s", config.grafana.url)
        logger.debug("LLM Provider: github_copilot, Model: %s", config.llm.model)

        return await run_daily_langgraph(config, logger)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error during inspection (LangGraph mode): %s", exc)
        return 1


def cli() -> None:
    """Sync CLI entrypoint."""
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


if __name__ == "__main__":
    cli()

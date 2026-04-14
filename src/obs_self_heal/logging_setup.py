from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    """Configure structlog + stdlib for JSON lines on stdout."""

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_format:
        shared.append(structlog.processors.JSONRenderer())
    else:
        shared.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=shared,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")


def get_logger(name: str = "obs_self_heal") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)

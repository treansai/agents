import logging
import sys
from contextvars import ContextVar

import structlog

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


def configure_logging(level: str = "INFO") -> None:
    """Configure JSON logs once. Secret values are never intentionally logged."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper(), force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):  # type: ignore[no-untyped-def]
    return structlog.get_logger(name)

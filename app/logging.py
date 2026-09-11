import logging
import re
import traceback
from typing import Any

import structlog


def error_details(exc: BaseException) -> dict[str, Any]:
    """Preserve diagnostic origin and class without exception bodies/SQL parameters."""
    frames = traceback.extract_tb(exc.__traceback__)[-6:]
    original = getattr(exc, "orig", None)
    response = getattr(exc, "response", None)
    return {
        "error_type": type(exc).__name__,
        "cause_type": type(original or exc.__cause__).__name__
        if original or exc.__cause__
        else None,
        "sqlstate": getattr(original, "sqlstate", None),
        "http_status": getattr(response, "status_code", None),
        "origin": [
            f"{frame.filename.rsplit('/', 2)[-1]}:{frame.lineno}:{frame.name}" for frame in frames
        ],
    }


class SafeFrameworkLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Third-party libraries may put URLs (including the Bot token) into error messages.
        if record.levelno >= logging.WARNING:
            record.msg = f"{record.name}: {record.levelname}" + (
                f" ({record.exc_info[0].__name__})"
                if record.exc_info and record.exc_info[0]
                else ""
            )
            record.args, record.exc_info, record.exc_text = (), None, None
        else:
            record.msg = re.sub(r"\d{5,}:[A-Za-z0-9_-]{20,}", "[REDACTED]", record.getMessage())
            record.args = ()
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.addFilter(SafeFrameworkLogs())
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

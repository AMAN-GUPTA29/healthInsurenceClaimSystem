"""
Structured application logging.

Establishes the logging foundation for the claims system.

Every log record carries:
  - timestamp (ISO-8601)
  - level
  - component (which agent/service/API handler produced the log)
  - request_id (HTTP-level correlation, set by middleware)
  - claim_id (business-level correlation, set by pipeline)
  - message
  - optional extra fields

In Phase 0 this uses Python's standard `logging` with a custom formatter.
In later phases this will be extended into the full claim trace/observability system.

Usage:
    from app.tracing.logging import get_logger

    logger = get_logger(component="ValidationAgent")
    logger.info("Claim validated", claim_id="CLM-ABC123", extra_field=42)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class _StructuredFormatter(logging.Formatter):
    """
    Formats log records as JSON lines when log_json=True, or
    as readable human lines when log_json=False.
    """

    def __init__(self, json_mode: bool = False) -> None:
        super().__init__()
        self._json_mode = json_mode

    def format(self, record: logging.LogRecord) -> str:
        # Core fields always present
        data: Dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "message": record.getMessage(),
        }

        # Optional correlation fields (set by middleware / pipeline)
        for field in ("request_id", "claim_id"):
            val = getattr(record, field, None)
            if val:
                data[field] = val

        # Include any extra keys passed via logger.info(extra={...})
        skip_keys = {
            "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno",
            "funcName", "created", "msecs", "relativeCreated", "thread",
            "threadName", "processName", "process", "name", "message",
            "component", "request_id", "claim_id",
        }
        for key, val in record.__dict__.items():
            if key not in skip_keys:
                data[key] = val

        # Exception info
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)

        if self._json_mode:
            return json.dumps(data, default=str)

        # Human-readable format
        prefix = f"[{data['timestamp']}] [{data['level']:8s}] [{data['component']}]"
        suffix_parts = []
        if "request_id" in data:
            suffix_parts.append(f"req={data['request_id']}")
        if "claim_id" in data:
            suffix_parts.append(f"claim={data['claim_id']}")
        suffix = " " + " ".join(suffix_parts) if suffix_parts else ""
        line = f"{prefix}{suffix} {data['message']}"
        if "exception" in data:
            line += f"\n{data['exception']}"
        return line


class _ComponentLogger:
    """
    Thin wrapper around a standard Logger that automatically injects
    the `component` field into every record.
    """

    def __init__(self, logger: logging.Logger, component: str) -> None:
        self._logger = logger
        self._component = component

    def _log(
        self,
        level: int,
        msg: str,
        *,
        claim_id: Optional[str] = None,
        request_id: Optional[str] = None,
        exc_info: Any = None,
        **extra: Any,
    ) -> None:
        record_extra: Dict[str, Any] = {"component": self._component, **extra}
        if claim_id:
            record_extra["claim_id"] = claim_id
        if request_id:
            record_extra["request_id"] = request_id
        self._logger.log(level, msg, extra=record_extra, exc_info=exc_info)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, exc_info=True, **kwargs)


_configured = False


def configure_logging(level: str = "INFO", json_mode: bool = False) -> None:
    """
    Configure root logging. Call once at application startup.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_mode: If True, emit JSON lines suitable for log aggregators.
    """
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter(json_mode=json_mode))

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # Reduce noise from third-party libraries
    for noisy in ("httpx", "httpcore", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(component: str) -> _ComponentLogger:
    """
    Get a component-scoped logger.

    Args:
        component: Name of the agent/service/handler (e.g. "ValidationAgent").

    Returns:
        _ComponentLogger that prepends component info to every record.
    """
    return _ComponentLogger(
        logging.getLogger(f"claims.{component}"),
        component=component,
    )

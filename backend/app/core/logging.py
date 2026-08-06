"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import Any

import orjson


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_record: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        # Merge any extra fields attached to the record
        for key, value in record.__dict__.items():
            if key not in (
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "id",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
                "taskName",
            ):
                log_record[key] = value
        return orjson.dumps(log_record).decode()


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure root logger.  Called once at application startup."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

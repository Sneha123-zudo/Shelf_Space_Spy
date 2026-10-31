"""Structured logging with secrets scrubbed.

Every record passes through :class:`RedactingFilter`, which masks API keys, bearer and JWT tokens,
passwords and key-like query parameters in the message and in structured fields. Request logging
records method, path (never the query string or body), status, duration and a request id.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REDACTED = "[redacted]"

_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\"?\s*[:=]\s*\"?)(?:bearer\s+)?[^\s&\",;}]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"),  # JWT
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
)
_SENSITIVE_KEYS = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|cookie)")

# Attributes every LogRecord has; anything else was passed through ``extra=`` and is a structured field.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    for pattern in _PATTERNS[1:]:
        text = pattern.sub(REDACTED, text)
    return text


def _scrub(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        for name in set(vars(record)) - _STANDARD:
            setattr(record, name, _scrub(getattr(record, name), name))
        if record.exc_info:  # exception text can embed URLs with keys
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_text:
            entry["exception"] = record.exc_text
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {k: v for k, v in vars(record).items() if k not in _STANDARD}
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        line = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        line = f"{line} {extra}" if extra else line
        return f"{line}\n{record.exc_text}" if record.exc_text else line


def configure_logging(level: str = "INFO", fmt: str = "text", stream: Any = None) -> None:
    """Idempotent: replaces the app handler on the root logger."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(RedactingFilter())
    handler.set_name("shelf-space-spy")
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.get_name() != "shelf-space-spy"]
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full request URLs, keys included


class RequestLogMiddleware:
    """Pure ASGI middleware: one log line per request, plus an ``X-Request-ID`` response header."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.logger = logging.getLogger("app.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self.logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

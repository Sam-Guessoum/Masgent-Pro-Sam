"""Structured application log (``workspace/app.log``) with rotation and secret redaction."""
from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from core.settings import redact

_configured: set[str] = set()


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg, record.args = redact(msg), ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(workspace: Path, name: str = "masgent_ui") -> logging.Logger:
    log = logging.getLogger(name)
    key = f"{name}:{workspace}"
    if key not in _configured:
        workspace.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            workspace / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True
        )
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactingFilter())
        for h in list(log.handlers):
            log.removeHandler(h)
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        _configured.add(key)
    return log

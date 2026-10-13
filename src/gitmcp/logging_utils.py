"""Secret-safe logging. All logs go to stderr; stdout is reserved for MCP traffic."""

from __future__ import annotations

import logging
import re
import sys

_PAT_FINE = re.compile(r"github_pat_[A-Za-z0-9_]{10,}")
_PAT_CLASSIC = re.compile(r"gh[pousr]_[A-Za-z0-9]{10,}")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|token)?\s*\S+")
_REDACTED = "***REDACTED***"


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove known secrets and anything token-shaped from ``text``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    text = _PAT_FINE.sub(_REDACTED, text)
    text = _PAT_CLASSIC.sub(_REDACTED, text)
    return _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)


class RedactingFilter(logging.Filter):
    """Scrubs tokens from the rendered message and exception text."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(
                logging.Formatter().formatException(record.exc_info), self._secrets
            )
            record.exc_info = None
        return True


def configure_logging(level: str = "INFO", secrets: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO; keep quiet unless debugging.
    logging.getLogger("httpx").setLevel(logging.DEBUG if level == "DEBUG" else logging.WARNING)

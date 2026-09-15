"""Structured JSON logging via structlog, with stdlib interception.

Every log line is JSON in Production and CI (``LOG_JSON=true``) or a
human-readable console line in local dev (``LOG_JSON=false``). Structlog and
stdlib are bridged with ``ProcessorFormatter`` so our loggers and any library
using stdlib ``logging`` (uvicorn, SQLAlchemy, httpx) share one pipeline.

Request-scoped fields (``request_id``, ``event_id``, ``delivery_id``,
``attempt_id``) are injected via ``structlog.contextvars``.

Redaction is defense in depth: sensitive-keyed values in the event dict are
replaced with ``[REDACTED]`` before rendering. Never rely on this alone -
code must still avoid logging secrets in the first place.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import IO, Any

import structlog
from structlog.typing import FilteringBoundLogger

from app.config import Settings, get_settings

# Normalized key name that must never be rendered in plaintext. Matching is
# case-insensitive and treats ``-`` and ``_`` as equivalent, so
# ``Authorization``, ``X-Api-Key``, ``set-cookie``, etc. all match.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "token",
        "tokens",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "api_key",
        "apikey",
        "api_keys",
        "authorization",
        "auth",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "credit_card",
        "card_number",
        "cvv",
        "cvc",
        "ssn",
        "private_key",
        "x_api_key",
        "x_auth_token",
        "x_webhook_signature",
    }
)


_REDACTED = "[REDACTED]"


def _normalize_key(key: str) -> str:
    """Lowercase and unify ``-`` to ``_`` so header names match the set."""
    return key.lower().replace("-", "_")


def _censor_sensitive_keys(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor: recursively redact values under sensitive keys.

    Applied to the event dict at the top level and inside nested dicts.
    Lists of dicts are walked as well. Values that are themselves strings
    containing the key name are not inspected - this is key-based defense,
    not a content scanner.
    """
    return _redact_mapping(event_dict)


def _redact_mapping(mapping: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(mapping.keys()):
        if _normalize_key(key) in _SENSITIVE_KEYS:
            mapping[key] = _REDACTED
        else:
            mapping[key] = _redact_value(mapping[key])
    return mapping


def _redact_value(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _level_to_int(level_name: str) -> int:
    """Convert a level name to the numeric level stdlib logging expects."""
    value = logging.getLevelName(level_name.upper())
    if isinstance(value, int):
        return value
    raise ValueError(f"Unknown log level: {level_name!r}")


def setup_logging(
    settings: Settings | None = None,
    stream: IO[str] | None = None,
) -> None:
    """Configure structlog + stdlib logging.

    Idempotent: safe to call repeatedly. Tests pass a ``StringIO`` via
    ``stream`` to capture output in memory; production leaves it ``None``
    to write to stdout.
    """

    cfg = settings or get_settings()
    level = _level_to_int(cfg.log_level)
    out: IO[str] = stream if stream is not None else sys.stdout

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Redaction runs before exception/stack renderers so any future
        # processor that adds a sensitive-named field is still covered.
        _censor_sensitive_keys,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Reset cached lazy loggers so re-configuration (e.g. different level in
    # tests) takes effect immediately.
    structlog.reset_defaults()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    if cfg.log_json:
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # The formatter that runs on the root handler. It sees both structlog
    # events (already wrapped) and foreign stdlib records, and renders both
    # as JSON.

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(out)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Return a structlog logger. Typed for mypy strict."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]

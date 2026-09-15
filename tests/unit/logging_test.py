"""Tests for app.core.logging.setup_logging."""

import json
import logging
from io import StringIO
from typing import Any

import structlog

from app.config import Settings
from app.core.logging import get_logger, setup_logging


def _parse_lines(buf: StringIO) -> list[dict[str, Any]]:
    """Parse every non-empty line of the buffer as JSON."""
    out: list[dict[str, Any]] = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def test_setup_logging_is_idempotent() -> None:
    """setup_logging can be called repeatedly without raising."""
    buf = StringIO()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    setup_logging(settings, stream=buf)
    setup_logging(settings, stream=buf)


def test_log_line_is_valid_json() -> None:
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    log.info("hello", key="value")

    lines = _parse_lines(buf)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["event"] == "hello"
    assert entry["key"] == "value"
    assert entry["level"] == "info"
    assert entry["logger"] == "test"
    assert "timestamp" in entry


def test_request_id_propagates_via_contextvars() -> None:
    """Request-scoped fields appear on every log line within the context."""
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc")

    log.info("first")
    log.info("second")

    structlog.contextvars.clear_contextvars()

    lines = _parse_lines(buf)
    assert len(lines) == 2
    assert all(line["request_id"] == "req-abc" for line in lines)


def test_log_level_filtering() -> None:
    """Setting level=WARNING suppresses INFO lines."""
    buf = StringIO()
    setup_logging(
        Settings(log_level="WARNING", _env_file=None),  # type: ignore[call-arg]
        stream=buf,
    )
    log = get_logger("test")
    log.info("info-line")
    log.warning("warn-line")

    lines = _parse_lines(buf)
    events = [line["event"] for line in lines]
    assert "warn-line" in events
    assert "info-line" not in events


def test_stdlib_logging_is_json() -> None:
    """Stdlib loggers are bridged through structlog's JSON pipeline."""
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    logging.getLogger("thirdparty").warning("from-stdlib")

    lines = _parse_lines(buf)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["event"] == "from-stdlib"
    assert entry["level"] == "warning"
    assert entry["logger"] == "thirdparty"
    assert "timestamp" in entry


def test_sensitive_keys_redacted_top_level() -> None:
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    log.info(
        "login",
        user="alice",
        password="hunter2",  # pragma: allowlist secret
        api_key="sk_live_x",  # pragma: allowlist secret
    )

    lines = _parse_lines(buf)
    assert lines[0]["user"] == "alice"
    assert lines[0]["password"] == "[REDACTED]"
    assert lines[0]["api_key"] == "[REDACTED]"


def test_sensitive_keys_redacted_nested() -> None:
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    log.info(
        "nested",
        request={"headers": {"Authorization": "Bearer abc", "Accept": "*/*"}},
    )

    lines = _parse_lines(buf)
    nested = lines[0]["request"]["headers"]
    assert nested["Authorization"] == "[REDACTED]"
    assert nested["Accept"] == "*/*"


def test_sensitive_keys_case_and_dash_insensitive() -> None:
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    log.info("hdr", **{"X-Api-Key": "sk_live_y", "set-cookie": "sess=1"})

    lines = _parse_lines(buf)
    assert lines[0]["X-Api-Key"] == "[REDACTED]"
    assert lines[0]["set-cookie"] == "[REDACTED]"


def test_console_renderer_when_log_json_false() -> None:
    """LOG_JSON=false produces human-readable output, not JSON."""
    buf = StringIO()
    setup_logging(
        Settings(log_json=False, _env_file=None),  # type: ignore[call-arg]
        stream=buf,
    )

    log = get_logger("text")
    log.info("plain text line")

    output = buf.getvalue()
    assert "plain text line" in output
    # Console renderer output is not valid JSON.
    assert not output.strip().startswith("{")


def test_sensitive_keys_redacted_on_exception_log() -> None:
    """Exception-path log lines pass through redaction.

    Asserts the pipeline still redacts on the exception path, where
    StackInfoRenderer and format_exc_info run after our redaction processor.
    """
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("db-error", password="p")

    lines = _parse_lines(buf)
    assert lines[0]["password"] == "[REDACTED]"
    assert lines[0]["event"] == "db-error"


def test_mapping_and_list_nesting_redacted() -> None:
    """Nested MutableMapping and list-of-mappings are both walked."""
    buf = StringIO()
    setup_logging(Settings(_env_file=None), stream=buf)  # type: ignore[call-arg]

    log = get_logger("test")
    log.info(
        "nested",
        headers=[{"Authorization": "Bearer x"}, {"Cookie": "s=1"}],
        meta={"inner": {"api_key": "sk_live"}},  # pragma: allowlist secret
    )

    lines = _parse_lines(buf)
    assert lines[0]["headers"][0]["Authorization"] == "[REDACTED]"
    assert lines[0]["headers"][1]["Cookie"] == "[REDACTED]"
    assert lines[0]["meta"]["inner"]["api_key"] == "[REDACTED]"

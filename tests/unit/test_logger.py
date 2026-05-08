"""Tests unitarios para bot.infra.logger."""

import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestGetLogger:
    def test_returns_logger_instance(self):
        from bot.infra.logger import get_logger
        log = get_logger("test.module")
        assert isinstance(log, logging.Logger)

    def test_logger_name_preserved(self):
        from bot.infra.logger import get_logger
        log = get_logger("my.custom.name")
        assert log.name == "my.custom.name"

    def test_same_name_returns_same_instance(self):
        from bot.infra.logger import get_logger
        a = get_logger("shared.name")
        b = get_logger("shared.name")
        assert a is b

    def test_handlers_not_duplicated_on_second_call(self):
        from bot.infra.logger import get_logger
        log = get_logger("dedup.test")
        initial_count = len(log.handlers)
        get_logger("dedup.test")
        assert len(log.handlers) == initial_count

    def test_logger_has_at_least_two_handlers(self):
        from bot.infra.logger import get_logger
        log = get_logger("handlers.test")
        assert len(log.handlers) >= 2

    def test_logs_dir_created(self):
        from bot.infra.logger import _LOGS_DIR
        assert _LOGS_DIR.exists()


class TestJsonFormatter:
    def test_json_output_has_required_fields(self):
        from bot.infra.logger import _JsonFormatter
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "timestamp" in data
        assert "level" in data
        assert "module" in data
        assert "message" in data
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"

    def test_json_output_includes_context(self):
        from bot.infra.logger import _JsonFormatter
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="ctx test", args=(), exc_info=None,
        )
        record.context = {"symbol": "EURUSD"}
        output = formatter.format(record)
        data = json.loads(output)
        assert data["context"] == {"symbol": "EURUSD"}

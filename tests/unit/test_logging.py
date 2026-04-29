"""Tests for logging configuration."""

import json
import logging

from nadia_ai.logging_config import StructuredFormatter, setup_logging


class TestStructuredFormatter:
    def test_human_readable(self):
        formatter = StructuredFormatter(structured=False)
        record = logging.LogRecord(
            name="nadia_ai.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "nadia_ai.test" in output
        assert "Test message" in output
        assert "INFO" in output

    def test_structured_json(self):
        formatter = StructuredFormatter(structured=True)
        record = logging.LogRecord(
            name="nadia_ai.test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "ERROR"
        assert parsed["logger"] == "nadia_ai.test"
        assert parsed["msg"] == "Error occurred"
        assert "ts" in parsed


class TestSetupLogging:
    def test_setup_creates_handler(self):
        # Clean up any existing handlers
        root = logging.getLogger("nadia_ai")
        root.handlers.clear()

        setup_logging(structured=False)
        assert len(root.handlers) == 1
        assert root.level == logging.INFO

    def test_no_duplicate_handlers(self):
        root = logging.getLogger("nadia_ai")
        root.handlers.clear()

        setup_logging()
        setup_logging()
        assert len(root.handlers) == 1

"""Tests for RotatingFileHandler replacing TimedRotatingFileHandler in logger."""

import logging
import threading
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler


class TestLoggerRotation:
    def test_file_handler_is_rotating_not_timed(self):
        """Logger must use RotatingFileHandler, not TimedRotatingFileHandler."""
        from bot.infra.logger import get_logger
        log = get_logger("rotation.type.check")
        file_handlers = [h for h in log.handlers if isinstance(h, logging.FileHandler)]

        assert file_handlers, "Logger must have at least one FileHandler"
        for h in file_handlers:
            assert not isinstance(h, TimedRotatingFileHandler), (
                "TimedRotatingFileHandler causes PermissionError on Windows — must not be used"
            )
            assert isinstance(h, RotatingFileHandler)

    def test_rotating_handler_max_bytes_is_10mb(self):
        """RotatingFileHandler maxBytes must be 10 MB."""
        from bot.infra.logger import get_logger
        log = get_logger("rotation.maxbytes")
        handlers = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
        assert handlers
        assert handlers[0].maxBytes == 10 * 1024 * 1024

    def test_rotating_handler_backup_count_is_30(self):
        """RotatingFileHandler backupCount must be 30."""
        from bot.infra.logger import get_logger
        log = get_logger("rotation.backupcount")
        handlers = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
        assert handlers
        assert handlers[0].backupCount == 30

    def test_no_permission_error_from_multiple_threads(self):
        """Logging from 10 threads simultaneously must not raise PermissionError."""
        from bot.infra.logger import get_logger
        log = get_logger("rotation.threads")
        errors: list[Exception] = []

        def log_many() -> None:
            try:
                for i in range(50):
                    log.info(f"thread log entry {i}")
            except PermissionError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=log_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"PermissionError raised across threads: {errors}"

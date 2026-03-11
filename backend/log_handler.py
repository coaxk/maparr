"""
log_handler.py — In-memory log capture for MapArr.

Captures Python log records into a ring buffer so the frontend can display
them in real-time. This gives users visibility into what MapArr is doing
without needing terminal access — critical for Docker deployments where
stdout isn't easily reachable.

THREE-TIER LOGGING STRATEGY:
  1. Ring buffer (this module) — last 500 entries, fetchable via /api/logs
  2. SSE stream — live push to the frontend log panel
  3. Toast surface — WARN/ERROR entries pushed as toast notifications

The ring buffer uses collections.deque for O(1) append/pop and automatic
size limiting. Thread-safe via logging's built-in lock mechanism.
"""

import logging
from collections import deque
from dataclasses import dataclass
from typing import List, Optional


# Maximum log entries to keep in memory. 500 is enough for a full
# analysis session without consuming significant RAM (~50KB).
MAX_LOG_ENTRIES = 500


@dataclass
class LogEntry:
    """A captured log record."""
    timestamp: float           # Unix timestamp
    level: str                 # "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    logger_name: str           # e.g. "maparr.analyzer"
    message: str               # Formatted message
    module: str = ""           # Source module name
    func: str = ""             # Source function name

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "module": self.module,
            "func": self.func,
        }


class MemoryLogHandler(logging.Handler):
    """
    Logging handler that captures records to an in-memory ring buffer.

    Attach to the root logger (or "maparr" logger) to capture all
    application logs. The buffer auto-evicts oldest entries when full.
    """

    def __init__(self, max_entries: int = MAX_LOG_ENTRIES):
        super().__init__()
        self._buffer: deque = deque(maxlen=max_entries)
        self._listeners: list = []  # SSE listener callbacks

    def emit(self, record: logging.LogRecord) -> None:
        """Called by the logging framework for each log record."""
        try:
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record),
                module=record.module or "",
                func=record.funcName or "",
            )
            self._buffer.append(entry)

            # Notify SSE listeners for live streaming
            for callback in self._listeners:
                try:
                    callback(entry)
                except Exception:
                    pass  # Don't let listener errors break logging
        except Exception:
            self.handleError(record)

    def get_entries(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        since: Optional[float] = None,
    ) -> List[LogEntry]:
        """
        Fetch log entries from the buffer.

        Args:
            limit: Maximum entries to return (newest first)
            level: Filter by minimum level ("DEBUG", "INFO", "WARNING", "ERROR")
            since: Only return entries after this Unix timestamp
        """
        level_num = getattr(logging, level.upper(), 0) if level else 0
        entries = list(self._buffer)

        if since:
            entries = [e for e in entries if e.timestamp > since]

        if level_num > 0:
            entries = [e for e in entries if getattr(logging, e.level, 0) >= level_num]

        # Return newest first, limited
        return list(reversed(entries))[:limit]

    def add_listener(self, callback) -> None:
        """Register a callback for live log streaming (SSE)."""
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        """Unregister a live log listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def clear(self) -> None:
        """Clear all buffered entries."""
        self._buffer.clear()

    @property
    def count(self) -> int:
        return len(self._buffer)


# ─── Singleton Instance ───
# Created once, attached to the root maparr logger in main.py

_handler: Optional[MemoryLogHandler] = None


def get_log_handler() -> MemoryLogHandler:
    """Get or create the singleton MemoryLogHandler."""
    global _handler
    if _handler is None:
        _handler = MemoryLogHandler()
        _handler.setFormatter(logging.Formatter("%(message)s"))
        _handler.setLevel(logging.DEBUG)
    return _handler


def install_log_handler() -> MemoryLogHandler:
    """Install the memory handler on the maparr logger tree."""
    handler = get_log_handler()
    # Attach to root logger to capture everything
    root = logging.getLogger()
    # Avoid duplicate handlers on reload
    if handler not in root.handlers:
        root.addHandler(handler)
    return handler

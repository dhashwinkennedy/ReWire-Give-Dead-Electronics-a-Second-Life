"""
Advanced Logging Subsystem for E-Waste Repurposing Platform.

Features:
- ContextVar-based request correlation (request ID, client IP, route tracking)
- Dual formatter pipeline:
    1. Human-friendly colorized console formatter
    2. Strict ISO-8601 structured JSON Lines log formatter (app.json.log)
- Multi-target rotating file handlers (app.log, app.json.log, error.log, unrecognized.log, client.log)
- Thread-safe in-memory circular ring buffer for real-time telemetry and API queryability
- Client-side log ingestion sink for frontend telemetry aggregation
"""

from __future__ import annotations

import collections
import contextvars
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import traceback
from typing import Any

from app.config import LOG_DIR

# Context variables for request tracing across asynchronous call stacks
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
client_ip_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("client_ip", default="-")
method_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("method", default="-")
path_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("path", default="-")


def set_request_context(
    request_id: str = "-",
    client_ip: str = "-",
    method: str = "-",
    path: str = "-",
) -> None:
    """Bind incoming HTTP request metadata to the current async context."""
    request_id_ctx.set(request_id)
    client_ip_ctx.set(client_ip)
    method_ctx.set(method)
    path_ctx.set(path)


def clear_request_context() -> None:
    """Reset request context variables."""
    request_id_ctx.set("-")
    client_ip_ctx.set("-")
    method_ctx.set("-")
    path_ctx.set("-")


class RequestContextFilter(logging.Filter):
    """Injects contextual request metadata into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", None) or request_id_ctx.get()
        record.client_ip = getattr(record, "client_ip", None) or client_ip_ctx.get()
        record.http_method = getattr(record, "http_method", None) or method_ctx.get()
        record.http_path = getattr(record, "http_path", None) or path_ctx.get()
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Outputs machine-readable, schema-consistent JSON Lines."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        log_entry: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "client_ip": getattr(record, "client_ip", "-"),
            "method": getattr(record, "http_method", "-"),
            "path": getattr(record, "http_path", "-"),
            "module": record.module,
            "line": record.lineno,
            "process": record.process,
            "thread": record.threadName,
        }

        # Include custom duration if supplied
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = round(float(record.duration_ms), 2)

        # Include exception payload with structured traceback
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            log_entry["exception"] = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": str(exc_val),
                "traceback": "".join(traceback.format_exception(exc_type, exc_val, exc_tb)).strip(),
            }

        # Include extra keyword fields if provided
        extra_keys = {
            k: v for k, v in record.__dict__.items()
            if k not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
                "id", "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
                "name", "pathname", "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "request_id", "client_ip", "http_method", "http_path",
                "duration_ms",
            }
        }
        if extra_keys:
            try:
                log_entry["extra"] = json.loads(json.dumps(extra_keys, default=str))
            except Exception:
                log_entry["extra"] = {k: str(v) for k, v in extra_keys.items()}

        return json.dumps(log_entry, ensure_ascii=False)


class ColoredConsoleFormatter(logging.Formatter):
    """ANSI-colorized console formatter for enhanced local developer experience."""

    # ANSI escape sequences
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"

    LEVEL_COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: MAGENTA,
    }

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__("%(asctime)s | %(levelname)-7s | [%(name)s] %(message)s")
        self.use_colors = use_colors and hasattr(sys.stderr, "isatty") and (sys.stderr.isatty() or sys.platform == "win32")

    def format(self, record: logging.LogRecord) -> str:
        req_id = getattr(record, "request_id", "-")
        req_prefix = f" [req:{req_id}]" if req_id and req_id != "-" else ""

        asctime = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        levelname = record.levelname

        if self.use_colors:
            color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
            level_str = f"{color}{levelname:<7}{self.RESET}"
            time_str = f"{self.DIM}{asctime}{self.RESET}"
            name_str = f"{self.CYAN}[{record.name}]{self.RESET}"
            msg_str = record.getMessage()
            if req_prefix:
                req_prefix = f"{self.DIM}{req_prefix}{self.RESET}"
            formatted = f"{time_str} | {level_str} | {name_str}{req_prefix} {msg_str}"
        else:
            formatted = f"{asctime} | {levelname:<7} | [{record.name}]{req_prefix} {record.getMessage()}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                formatted = f"{formatted}\n{record.exc_text}"
        return formatted


class InMemoryLogBuffer:
    """Thread-safe circular ring buffer retaining recent structured log records."""

    def __init__(self, capacity: int = 300) -> None:
        self.capacity = capacity
        self.buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.start_time = datetime.now(timezone.utc)
        self.counts = collections.defaultdict(int)

    def append(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.buffer.append(entry)
            self.counts[entry.get("level", "INFO")] += 1
            self.counts["TOTAL"] += 1

    def get_logs(
        self,
        level: str | None = None,
        limit: int = 50,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.lock:
            items = list(self.buffer)

        if level:
            target_level = level.upper()
            items = [item for item in items if item.get("level") == target_level]

        if search:
            query = search.lower()
            items = [
                item for item in items
                if query in item.get("message", "").lower()
                or query in item.get("logger", "").lower()
                or query in str(item.get("request_id", "")).lower()
            ]

        # Return latest first
        items.reverse()
        return items[: max(1, min(limit, 200))]

    def get_stats(self) -> dict[str, Any]:
        with self.lock:
            uptime_seconds = round((datetime.now(timezone.utc) - self.start_time).total_seconds(), 1)
            return {
                "total_records": self.counts["TOTAL"],
                "by_level": {k: v for k, v in self.counts.items() if k != "TOTAL"},
                "buffer_capacity": self.capacity,
                "current_buffer_size": len(self.buffer),
                "uptime_seconds": uptime_seconds,
                "started_at": self.start_time.isoformat(),
            }

    def clear(self) -> None:
        with self.lock:
            self.buffer.clear()
            self.counts.clear()


# Global in-memory log buffer instance
log_buffer = InMemoryLogBuffer(capacity=300)


class BufferLogHandler(logging.Handler):
    """Pushes log events directly into the in-memory telemetry buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", "-"),
                "client_ip": getattr(record, "client_ip", "-"),
                "method": getattr(record, "http_method", "-"),
                "path": getattr(record, "http_path", "-"),
            }
            if record.exc_info:
                entry["exception"] = str(record.exc_info[1])
            if hasattr(record, "duration_ms"):
                entry["duration_ms"] = round(float(record.duration_ms), 2)
            log_buffer.append(entry)
        except Exception:
            self.handleError(record)


def configure_advanced_logging(target_dir: Path | None = None) -> None:
    """Configure comprehensive structured and rotating file/console logging."""
    dir_path = target_dir or LOG_DIR
    dir_path.mkdir(parents=True, exist_ok=True)

    context_filter = RequestContextFilter()
    json_formatter = StructuredJsonFormatter()
    console_formatter = ColoredConsoleFormatter()
    plain_formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | [%(name)s] [req:%(request_id)s] %(message)s")

    root_ewaste = logging.getLogger("ewaste")
    root_ewaste.setLevel(logging.INFO)

    # 1. Rotating Human-Readable Log (app.log)
    app_log_path = (dir_path / "app.log").resolve()
    has_app_log = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == app_log_path
        for h in root_ewaste.handlers
    )
    if not has_app_log:
        rfh = RotatingFileHandler(app_log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
        rfh.setLevel(logging.INFO)
        rfh.setFormatter(plain_formatter)
        rfh.addFilter(context_filter)
        root_ewaste.addHandler(rfh)

    # 2. Rotating Structured JSON Log (app.json.log)
    json_log_path = (dir_path / "app.json.log").resolve()
    has_json_log = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == json_log_path
        for h in root_ewaste.handlers
    )
    if not has_json_log:
        jfh = RotatingFileHandler(json_log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
        jfh.setLevel(logging.INFO)
        jfh.setFormatter(json_formatter)
        jfh.addFilter(context_filter)
        root_ewaste.addHandler(jfh)

    # 3. Dedicated Error Log (error.log for WARNING / ERROR / CRITICAL)
    error_log_path = (dir_path / "error.log").resolve()
    has_error_log = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == error_log_path
        for h in root_ewaste.handlers
    )
    if not has_error_log:
        efh = RotatingFileHandler(error_log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        efh.setLevel(logging.WARNING)
        efh.setFormatter(json_formatter)
        efh.addFilter(context_filter)
        root_ewaste.addHandler(efh)

    # 4. Colorized Console Stream Handler
    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root_ewaste.handlers
    )
    if not has_console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(console_formatter)
        sh.addFilter(context_filter)
        root_ewaste.addHandler(sh)

    # 5. In-Memory Telemetry Buffer Handler
    has_buffer = any(isinstance(h, BufferLogHandler) for h in root_ewaste.handlers)
    if not has_buffer:
        bh = BufferLogHandler()
        bh.setLevel(logging.INFO)
        bh.addFilter(context_filter)
        root_ewaste.addHandler(bh)

    # 6. Dedicated Unrecognized Items Audit Logger
    unknown_logger = logging.getLogger("ewaste.unrecognized")
    unknown_logger.setLevel(logging.WARNING)
    unknown_path = (dir_path / "unrecognized.log").resolve()
    has_unknown = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == unknown_path
        for h in unknown_logger.handlers
    )
    if not has_unknown:
        ufh = RotatingFileHandler(unknown_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        ufh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        unknown_logger.addHandler(ufh)
        unknown_logger.propagate = False

    # 7. Dedicated Client-Side Telemetry Logger (for frontend beacon / error logs)
    client_logger = logging.getLogger("ewaste.client")
    client_logger.setLevel(logging.INFO)
    client_path = (dir_path / "client.log").resolve()
    has_client = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")).resolve() == client_path
        for h in client_logger.handlers
    )
    if not has_client:
        cfh = RotatingFileHandler(client_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        cfh.setFormatter(json_formatter)
        client_logger.addHandler(cfh)


def shutdown_logging() -> None:
    """Flush all active logging handlers cleanly."""
    for name in ("ewaste", "ewaste.unrecognized", "ewaste.client"):
        lg = logging.getLogger(name)
        for handler in lg.handlers:
            try:
                handler.flush()
            except Exception:
                pass

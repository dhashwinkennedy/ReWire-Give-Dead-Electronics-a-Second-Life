import json
from pathlib import Path
from fastapi.testclient import TestClient

from app.config import LOG_DIR
from app.logger import (
    configure_advanced_logging,
    log_buffer,
    set_request_context,
    clear_request_context,
    StructuredJsonFormatter,
    RequestContextFilter,
)
from app.main import app
import logging


def test_logger_endpoints_and_telemetry():
    with TestClient(app) as client:
        # Trigger an API call
        res = client.get("/api/v1/logs/stats")
        assert res.status_code == 200
        stats = res.json()
        assert stats["status"] == "healthy"
        assert "telemetry" in stats
        assert "total_records" in stats["telemetry"]

        # Post client-side logs
        client_payload = {
            "logs": [
                {
                    "level": "INFO",
                    "module": "UI",
                    "message": "User searched catalog",
                    "data": {"query": "resistor"},
                },
                {
                    "level": "ERROR",
                    "module": "Network",
                    "message": "Failed to reach endpoint",
                    "data": {"code": 500},
                },
            ]
        }
        res_post = client.post("/api/v1/logs/client", json=client_payload)
        assert res_post.status_code == 200
        assert res_post.json()["ingested"] == 2

        # Query recent logs endpoint
        res_logs = client.get("/api/v1/logs?limit=20")
        assert res_logs.status_code == 200
        data = res_logs.json()
        assert data["status"] == "success"
        assert isinstance(data["logs"], list)
        assert len(data["logs"]) > 0


def test_context_filter_and_json_formatter():
    set_request_context(request_id="req-test-1234", client_ip="127.0.0.1", method="GET", path="/test")
    try:
        record = logging.LogRecord(
            name="ewaste.unit_test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Testing structured log message",
            args=(),
            exc_info=None,
        )
        filt = RequestContextFilter()
        assert filt.filter(record) is True
        assert record.request_id == "req-test-1234"
        assert record.client_ip == "127.0.0.1"

        formatter = StructuredJsonFormatter()
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        assert parsed["logger"] == "ewaste.unit_test"
        assert parsed["message"] == "Testing structured log message"
        assert parsed["request_id"] == "req-test-1234"
    finally:
        clear_request_context()


def test_log_files_created_and_written():
    configure_advanced_logging()
    app_log = LOG_DIR / "app.log"
    json_log = LOG_DIR / "app.json.log"
    assert app_log.exists()
    assert json_log.exists()

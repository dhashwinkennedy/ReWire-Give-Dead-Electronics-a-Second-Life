from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.exceptions import (
    EWasteBaseException,
    CatalogError,
    CatalogNotFoundError,
    CatalogCorruptedError,
    CatalogValidationError,
    VisionDetectionError,
    UnsupportedImageFormatError,
    EmptyImageFileError,
    CorruptedImageError,
    ModelNotLoadedError,
    InferenceRuntimeError,
    AISearchError,
    AIConfigMissingError,
    AIProviderUnavailableError,
    AIInvalidOutputError,
)
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_exception_hierarchy_and_to_dict():
    """Verify domain exception classes, error codes, and serialization."""
    base_err = EWasteBaseException("Base error message", status_code=500, error_code="TEST_BASE", details={"k": "v"})
    assert base_err.to_dict() == {"detail": "Base error message", "error_code": "TEST_BASE", "details": {"k": "v"}}

    not_found = CatalogNotFoundError("test_cat", "data/test.json")
    assert not_found.status_code == 500
    assert not_found.error_code == "CATALOG_NOT_FOUND"
    assert "not found" in not_found.message

    corrupt = CatalogCorruptedError("test_cat", "invalid syntax")
    assert corrupt.error_code == "CATALOG_CORRUPTED"

    val_err = CatalogValidationError(["Error 1", "Error 2"])
    assert val_err.error_code == "CATALOG_VALIDATION_ERROR"
    assert val_err.details["error_count"] == 2

    format_err = UnsupportedImageFormatError(".gif", [".jpg", ".png"])
    assert format_err.status_code == 400
    assert format_err.error_code == "UNSUPPORTED_IMAGE_FORMAT"
    assert "Unsupported file type" in format_err.message

    model_err = ModelNotLoadedError()
    assert model_err.status_code == 503
    assert model_err.error_code == "MODEL_NOT_LOADED"

    ai_conf_err = AIConfigMissingError("TAVILY_API_KEY")
    assert ai_conf_err.status_code == 503
    assert ai_conf_err.error_code == "AI_CONFIG_MISSING"

    ai_unavail = AIProviderUnavailableError("Tavily")
    assert ai_unavail.status_code == 502
    assert ai_unavail.error_code == "AI_TAVILY_UNAVAILABLE"

    ai_invalid = AIInvalidOutputError()
    assert ai_invalid.status_code == 502
    assert ai_invalid.error_code == "AI_INVALID_OUTPUT"


def test_error_response_envelope_structure(client: TestClient):
    """Verify that error responses contain standard envelope fields."""
    files = {"image": ("bad.gif", b"fake", "image/gif")}
    response = client.post("/detect", files=files)
    assert response.status_code == 400
    body = response.json()
    assert "detail" in body
    assert "error_code" in body
    assert body["error_code"] == "UNSUPPORTED_IMAGE_FORMAT"
    assert "request_id" in body
    assert body["request_id"].startswith("req-")
    assert "timestamp" in body
    assert "path" in body
    assert body["path"] == "/detect"
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_custom_correlation_id_propagation(client: TestClient):
    """Verify incoming X-Request-ID is preserved in response and error body."""
    custom_id = "trace-req-xyz-987"
    files = {"image": ("bad.gif", b"fake", "image/gif")}
    response = client.post("/detect", files=files, headers={"X-Request-ID": custom_id})
    assert response.status_code == 400
    body = response.json()
    assert body["request_id"] == custom_id
    assert response.headers["X-Request-ID"] == custom_id


def test_validation_error_response_envelope(client: TestClient):
    """Verify validation 422 errors also adopt the standardized error envelope."""
    response = client.post("/api/v1/repurpose", json={"items": [{"name": "   "}]})
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "request_id" in body
    assert "timestamp" in body
    assert "path" in body
    assert isinstance(body["detail"], list)


def test_image_size_limit_exceeded(client: TestClient):
    """Verify upload exceeding 15MB raises IMAGE_SIZE_LIMIT_EXCEEDED (HTTP 413)."""
    oversized = b"0" * (16 * 1024 * 1024)
    files = {"image": ("huge.jpg", oversized, "image/jpeg")}
    response = client.post("/detect", files=files)
    assert response.status_code == 413
    body = response.json()
    assert body["error_code"] == "IMAGE_SIZE_LIMIT_EXCEEDED"
    assert "exceeds maximum allowed upload size" in body["detail"]
    assert "details" in body
    assert body["details"]["file_size_bytes"] == 16 * 1024 * 1024


def test_new_domain_exceptions_instantiation():
    """Verify instantiation of new domain exception classes."""
    from app.exceptions import (
        AIDependencyMissingError,
        ImageSizeLimitExceededError,
        PayloadEmptyError,
        ItemNotFoundError,
    )

    dep_err = AIDependencyMissingError("langchain-ollama", "Ollama Reranking")
    assert dep_err.status_code == 503
    assert dep_err.error_code == "AI_DEPENDENCY_MISSING"
    assert dep_err.details["missing_package"] == "langchain-ollama"

    size_err = ImageSizeLimitExceededError(20 * 1024 * 1024)
    assert size_err.status_code == 413
    assert size_err.error_code == "IMAGE_SIZE_LIMIT_EXCEEDED"

    empty_err = PayloadEmptyError()
    assert empty_err.status_code == 422
    assert empty_err.error_code == "PAYLOAD_EMPTY"

    not_found = ItemNotFoundError("resistor_xyz")
    assert not_found.status_code == 404
    assert not_found.error_code == "ITEM_NOT_FOUND"


def test_remediation_and_title_in_error_envelope(client: TestClient):
    """Verify error responses include remediation advice and human-readable title."""
    files = {"image": ("bad.gif", b"fake", "image/gif")}
    response = client.post("/detect", files=files)
    assert response.status_code == 400
    body = response.json()
    assert "title" in body
    assert "remediation" in body
    assert "Convert the image to JPEG" in body["remediation"]


def test_circuit_breaker_trips_and_recovers():
    """Verify CircuitBreaker trips to OPEN after threshold and recovers after timeout."""
    import time
    from app.circuit_breaker import CircuitBreaker, CircuitState
    from app.exceptions import CircuitBreakerOpenError

    cb = CircuitBreaker("mock_service", failure_threshold=2, recovery_timeout=0.1)
    assert cb.state == CircuitState.CLOSED

    def failing():
        raise RuntimeError("Service failure")

    # First failure
    with pytest.raises(RuntimeError):
        cb.call(failing)
    assert cb.state == CircuitState.CLOSED

    # Second failure trips breaker
    with pytest.raises(RuntimeError):
        cb.call(failing)
    assert cb.state == CircuitState.OPEN

    # While OPEN, immediate fast fail with CircuitBreakerOpenError
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        cb.call(lambda: "should not run")
    assert exc_info.value.service_name == "mock_service"

    # Wait for cooldown
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # Probe success recovers to CLOSED
    result = cb.call(lambda: "recovered")
    assert result == "recovered"
    assert cb.state == CircuitState.CLOSED


def test_sliding_window_rate_limiter():
    """Verify SlidingWindowRateLimiter blocks clients when request quota is exceeded."""
    from app.rate_limiter import SlidingWindowRateLimiter
    from app.exceptions import RateLimitExceededError

    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10)
    client_ip = "203.0.113.42"

    limiter.check(client_ip, "/test")
    limiter.check(client_ip, "/test")

    with pytest.raises(RateLimitExceededError) as exc_info:
        limiter.check(client_ip, "/test")
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after > 0


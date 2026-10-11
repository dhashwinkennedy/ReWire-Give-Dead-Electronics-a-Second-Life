"""Centralized domain exception hierarchy for the E-Waste Repurposing Platform."""
from __future__ import annotations

from typing import Any


class EWasteBaseException(Exception):
    """Base exception for all domain-specific application errors with RFC 7807 problem details."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "INTERNAL_ERROR",
        details: Any = None,
        title: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details
        self.title = title
        self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "detail": self.message,
            "error_code": self.error_code,
        }
        if self.title is not None:
            result["title"] = self.title
        if self.remediation is not None:
            result["remediation"] = self.remediation
        if self.details is not None:
            result["details"] = self.details
        return result


# ---------------------------------------------------------------------------
# Catalog Errors
# ---------------------------------------------------------------------------
class CatalogError(EWasteBaseException):
    """Base exception for catalog loading, indexing, or parsing failures."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "CATALOG_ERROR",
        details: Any = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            details=details,
            remediation=remediation or "Check catalog JSON syntax and directory permissions in data/.",
        )


class CatalogNotFoundError(CatalogError):
    """Raised when an essential catalog file is missing from data/."""

    def __init__(self, catalog_name: str, path: str) -> None:
        super().__init__(
            f"Catalog '{catalog_name}' not found at {path}.",
            status_code=500,
            error_code="CATALOG_NOT_FOUND",
            details={"catalog_name": catalog_name, "path": path},
            remediation="Ensure the required catalog dataset files are present in the data/ directory.",
        )


class CatalogCorruptedError(CatalogError):
    """Raised when a catalog JSON file fails to parse or is malformed."""

    def __init__(self, catalog_name: str, reason: str) -> None:
        super().__init__(
            f"Catalog '{catalog_name}' is corrupted or invalid JSON: {reason}.",
            status_code=500,
            error_code="CATALOG_CORRUPTED",
            details={"catalog_name": catalog_name, "reason": reason},
            remediation="Validate the catalog JSON syntax with a JSON validator before starting the server.",
        )


class CatalogValidationError(CatalogError):
    """Raised when catalogs fail referential integrity or schema checks in strict mode."""

    def __init__(self, errors: list[str]) -> None:
        preview = "; ".join(errors[:5])
        super().__init__(
            f"Catalog validation failed with {len(errors)} error(s): {preview}",
            status_code=500,
            error_code="CATALOG_VALIDATION_ERROR",
            details={"error_count": len(errors), "errors": errors},
            remediation="Resolve component reference errors or start with CATALOG_STRICT=false.",
        )
        self.errors = errors


# ---------------------------------------------------------------------------
# AI Vision Detection Errors
# ---------------------------------------------------------------------------
class VisionDetectionError(EWasteBaseException):
    """Base exception for computer vision and image inference failures."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "VISION_ERROR",
        details: Any = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            details=details,
            remediation=remediation or "Verify the uploaded image format, dimensions, and clarity.",
        )


class UnsupportedImageFormatError(VisionDetectionError):
    """Raised when an uploaded image format is not in the allowed extensions."""

    def __init__(self, extension: str, allowed: set[str] | list[str]) -> None:
        allowed_list = sorted(allowed)
        super().__init__(
            f"Unsupported file type '{extension}'. Allowed formats: {', '.join(ext.upper().lstrip('.') for ext in allowed_list)}.",
            status_code=400,
            error_code="UNSUPPORTED_IMAGE_FORMAT",
            details={"extension": extension, "allowed": allowed_list},
            remediation="Convert the image to JPEG, PNG, WEBP, or BMP format before uploading.",
        )


class EmptyImageFileError(VisionDetectionError):
    """Raised when an uploaded image file contains 0 bytes."""

    def __init__(self) -> None:
        super().__init__(
            "Uploaded image file is empty.",
            status_code=400,
            error_code="EMPTY_IMAGE_FILE",
            remediation="Select a valid, non-empty image file from your device.",
        )


class ImageSizeLimitExceededError(VisionDetectionError):
    """Raised when an uploaded image exceeds the maximum supported file size."""

    def __init__(self, file_size_bytes: int, max_allowed_bytes: int = 15 * 1024 * 1024) -> None:
        size_mb = round(file_size_bytes / (1024 * 1024), 2)
        limit_mb = round(max_allowed_bytes / (1024 * 1024), 2)
        super().__init__(
            f"Image size ({size_mb} MB) exceeds maximum allowed upload size of {limit_mb} MB.",
            status_code=413,
            error_code="IMAGE_SIZE_LIMIT_EXCEEDED",
            details={"file_size_bytes": file_size_bytes, "max_allowed_bytes": max_allowed_bytes},
            remediation="Resize or compress your image to be under 15 MB before uploading.",
        )


class CorruptedImageError(VisionDetectionError):
    """Raised when image bytes cannot be decoded by PIL or are corrupted."""

    def __init__(self, reason: str = "Uploaded file is corrupted or not a valid image.") -> None:
        super().__init__(
            reason,
            status_code=400,
            error_code="CORRUPTED_IMAGE_FILE",
            remediation="Ensure the file is an intact raster image that opens in a standard image viewer.",
        )


class ModelNotLoadedError(VisionDetectionError):
    """Raised when MobileNetV3 model checkpoint is not initialized or not found."""

    def __init__(
        self,
        reason: str = "AI vision model is not loaded. Ensure best_model.pth and classes.json exist in models/mobilenetv3.",
    ) -> None:
        super().__init__(
            reason,
            status_code=503,
            error_code="MODEL_NOT_LOADED",
            remediation="Ensure models/mobilenetv3/best_model.pth exists or search for items manually.",
        )


class InferenceRuntimeError(VisionDetectionError):
    """Raised when an unexpected tensor or runtime error occurs during inference."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Inference error: {reason}",
            status_code=500,
            error_code="INFERENCE_RUNTIME_ERROR",
            details={"reason": reason},
            remediation="Try a different photograph with balanced lighting and clear item focus.",
        )


# ---------------------------------------------------------------------------
# AI Search & Reranking Errors
# ---------------------------------------------------------------------------
class AISearchError(EWasteBaseException):
    """Base exception for Tavily research or Ollama reranking failures."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_code: str = "AI_SEARCH_ERROR",
        details: Any = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            details=details,
            remediation=remediation or "Use the deterministic ReWire matching mode as an immediate alternative.",
        )


class AIConfigMissingError(AISearchError):
    """Raised when required AI search credentials or model settings are not configured."""

    def __init__(self, missing_var: str) -> None:
        super().__init__(
            f"AI search is not configured: {missing_var} is required.",
            status_code=503,
            error_code="AI_CONFIG_MISSING",
            details={"missing_variable": missing_var},
            remediation=f"Configure {missing_var} in your environment or .env, or use deterministic ReWire mode.",
        )


class AIProviderUnavailableError(AISearchError):
    """Raised when external search (Tavily) or local LLM (Ollama) service is unreachable."""

    def __init__(self, provider: str, detail: str = "") -> None:
        msg = detail or f"AI provider '{provider}' is unavailable. Please try again later."
        super().__init__(
            msg,
            status_code=503 if provider.lower() == "ollama" else 502,
            error_code=f"AI_{provider.upper()}_UNAVAILABLE",
            details={"provider": provider},
            remediation=f"Check that {provider} is running and network is active, or use deterministic ReWire mode.",
        )


class AIInvalidOutputError(AISearchError):
    """Raised when the LLM outputs unstructured or invalid JSON that fails schema validation."""

    def __init__(self, detail: str = "Local AI returned an invalid recommendation response.") -> None:
        super().__init__(
            detail,
            status_code=502,
            error_code="AI_INVALID_OUTPUT",
            remediation="The model produced an unparseable response; retry or switch to deterministic matching.",
        )


class AIDependencyMissingError(AISearchError):
    """Raised when optional AI dependencies (e.g. langchain-ollama or tavily-python) are not installed."""

    def __init__(self, package_name: str, feature: str = "AI Search") -> None:
        super().__init__(
            f"{feature} requires '{package_name}' which is not installed.",
            status_code=503,
            error_code="AI_DEPENDENCY_MISSING",
            details={"missing_package": package_name, "feature": feature},
            remediation=f"Run 'pip install {package_name}' to enable this feature, or use deterministic matching.",
        )


# ---------------------------------------------------------------------------
# Resilience & Reliability Errors
# ---------------------------------------------------------------------------
class RateLimitExceededError(EWasteBaseException):
    """Raised when request volume from a client exceeds configured rate limits."""

    def __init__(self, retry_after: int = 60, endpoint: str = "") -> None:
        super().__init__(
            f"Rate limit exceeded for {endpoint or 'this endpoint'}. Please slow down.",
            status_code=429,
            error_code="RATE_LIMIT_EXCEEDED",
            title="Rate Limit Exceeded",
            remediation=f"Wait {retry_after} seconds before repeating this request.",
            details={"retry_after": retry_after, "endpoint": endpoint},
        )
        self.retry_after = retry_after


class CircuitBreakerOpenError(EWasteBaseException):
    """Raised when an external service circuit breaker is open to prevent cascading failure."""

    def __init__(self, service_name: str, cooldown_seconds: float = 30.0) -> None:
        super().__init__(
            f"Circuit breaker is OPEN for external service '{service_name}'. Requests temporarily suspended.",
            status_code=503,
            error_code=f"CIRCUIT_{service_name.upper()}_OPEN",
            title=f"{service_name.title()} Service Circuit Open",
            remediation="The external service is currently experiencing repeated failures. Try again in a few moments or use deterministic mode.",
            details={"service_name": service_name, "cooldown_seconds": cooldown_seconds},
        )
        self.service_name = service_name
        self.cooldown_seconds = cooldown_seconds


# ---------------------------------------------------------------------------
# General Request & Item Errors
# ---------------------------------------------------------------------------
class PayloadEmptyError(EWasteBaseException):
    """Raised when a request payload contains no actionable items."""

    def __init__(self, message: str = "Inventory payload cannot be empty.") -> None:
        super().__init__(
            message,
            status_code=422,
            error_code="PAYLOAD_EMPTY",
            remediation="Add at least one electronic component or device to your inventory before submitting.",
        )


class ItemNotFoundError(EWasteBaseException):
    """Raised when a requested catalog item cannot be found."""

    def __init__(self, item_id: str, kind: str = "item") -> None:
        super().__init__(
            f"No {kind} found with ID '{item_id}'.",
            status_code=404,
            error_code="ITEM_NOT_FOUND",
            details={"item_id": item_id, "kind": kind},
            remediation="Verify the item ID against the supported catalog at /vocab.",
        )

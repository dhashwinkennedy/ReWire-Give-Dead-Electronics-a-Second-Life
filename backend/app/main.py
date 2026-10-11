import io
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai_search import AISearchService, reorder_recommendations
from app.catalog import Catalogs, load_catalogs
from app.config import CATALOG_STRICT, CATALOG_VERSION_FALLBACK, LOG_DIR, STATIC_DIR
from app.rate_limiter import ai_search_rate_limiter, vision_rate_limiter
from app.exceptions import (
    CorruptedImageError,
    EmptyImageFileError,
    EWasteBaseException,
    ImageSizeLimitExceededError,
    InferenceRuntimeError,
    ModelNotLoadedError,
    UnsupportedImageFormatError,
)
from app.expander import expand_inventory
from app.matcher import browse_recommendations, build_recommendations
from app.resolver import Resolver
from app.schemas import RepurposeRequest, RepurposeResponse
from app.logger import (
    configure_advanced_logging,
    shutdown_logging,
    set_request_context,
    clear_request_context,
    log_buffer,
)

logger = logging.getLogger("ewaste")


def _configure_logging() -> None:
    """Configure robust application file and console logging."""
    configure_advanced_logging()


def _build_vocab(catalogs: Catalogs) -> dict[str, Any]:
    """Dynamically assemble client autocomplete index from in-memory catalogs (no static dump file needed)."""
    component_names = {row["id"]: row["display_name"] for row in catalogs.components}
    items = []
    for row in catalogs.components:
        items.append({
            "id": row["id"], "kind": "component", "name": row["display_name"],
            "aliases": row.get("aliases", []), "category": row.get("category", ""),
            "components": [],
        })
    for row in catalogs.devices:
        salvage = [
            {"id": entry["component_id"], "name": component_names.get(entry["component_id"], entry["component_id"]),
             "typical_quantity": entry.get("typical_quantity", 1), "do_not_salvage": entry.get("do_not_salvage", False),
             "note": entry.get("note")}
            for entry in row.get("salvageable_components", [])
        ]
        preferred = list(row.get("common_failure_components", []))
        salvage.sort(key=lambda entry: (entry["id"] not in preferred, entry["name"].lower()))
        items.append({
            "id": row["device_id"], "kind": "device", "name": row["device_name"],
            "aliases": row.get("aliases", []), "category": row.get("device_category", ""),
            "components": salvage, "common_failure_components": preferred,
            "safety_level": row.get("safety_level", "low"),
        })
    items.sort(key=lambda row: (row["kind"], re.sub(r"[^a-z0-9]+", " ", row["name"].lower()).strip(), row["id"]))
    return {"catalog_version": catalogs.version, "items": items}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    logger.info("Initializing E-Waste Repurposing backend...")
    catalogs = load_catalogs(strict=CATALOG_STRICT)
    app.state.catalogs = catalogs
    app.state.resolver = Resolver(catalogs)
    app.state.ai_search_service = AISearchService()
    app.state.vocab = _build_vocab(catalogs)
    logger.info(
        f"Catalogs loaded (v{catalogs.version}): {len(catalogs.components)} components, "
        f"{len(catalogs.devices)} devices, {len(catalogs.projects)} projects; "
        f"dynamic vocabulary indexed ({len(app.state.vocab['items'])} entries)."
    )

    # Initialize AI vision detection modules
    try:
        from ai_detection.inference import ComponentClassifier, get_classifier
        from ai_detection.resolver import CatalogResolver

        app.state.ai_resolver = CatalogResolver()
        try:
            app.state.classifier = get_classifier()
            logger.info("AI MobileNetV3 vision model loaded successfully.")
        except Exception as e:
            logger.warning(f"AI classifier could not be loaded at startup: {e}")
            app.state.classifier = None
    except Exception as e:
        logger.warning(f"AI detection module initialization warning: {e}")
        app.state.ai_resolver = None
        app.state.classifier = None

    yield
    shutdown_logging()


app = FastAPI(
    title="E-Waste Repurposing Platform",
    description="Deterministic reuse-project matching for electronic components and salvaged devices.",
    version=CATALOG_VERSION_FALLBACK,
    lifespan=lifespan,
)

# Enable CORS middleware to handle OPTIONS preflight requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Support client correlation tracking or generate a unique request ID
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
    request.state.request_id = request_id

    start = time.perf_counter()
    method = request.method
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"

    set_request_context(
        request_id=request_id,
        client_ip=client_ip,
        method=method,
        path=path,
    )

    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            f"{method} {path} -> {response.status_code} ({duration_ms:.1f}ms) [{client_ip}]",
            extra={"duration_ms": duration_ms, "status_code": response.status_code, "request_id": request_id},
        )
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(
            f"{method} {path} -> ERROR: {exc} ({duration_ms:.1f}ms) [{client_ip}]",
            exc_info=True,
            extra={"duration_ms": duration_ms, "request_id": request_id},
        )
        raise exc
    finally:
        clear_request_context()


@app.exception_handler(EWasteBaseException)
async def ewaste_base_exception_handler(request: Request, exc: EWasteBaseException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
    content: dict[str, Any] = {
        "detail": exc.message,
        "error_code": exc.error_code,
        "title": getattr(exc, "title", None) or exc.error_code.replace("_", " ").title(),
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
    }
    if getattr(exc, "remediation", None) is not None:
        content["remediation"] = exc.remediation
    if exc.details is not None:
        content["details"] = exc.details

    logger.warning(
        f"[req:{request_id}] Domain exception {exc.error_code} ({exc.status_code}) on {request.method} {request.url.path}: {exc.message}"
    )
    headers = {"X-Request-ID": request_id}
    if hasattr(exc, "retry_after"):
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
    content: dict[str, Any] = {
        "detail": exc.detail,
        "error_code": f"HTTP_{exc.status_code}",
        "title": f"HTTP {exc.status_code} Error",
        "remediation": "Verify the request URI and parameters, or consult the API documentation.",
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
    }
    logger.warning(
        f"[req:{request_id}] HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
    content: dict[str, Any] = {
        "detail": jsonable_encoder(exc.errors()),
        "error_code": "VALIDATION_ERROR",
        "title": "Request Validation Error",
        "remediation": "Review the items in your request payload to ensure all fields and quantities are valid.",
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
    }
    logger.warning(
        f"[req:{request_id}] Request validation error on {request.method} {request.url.path}: {len(exc.errors())} error(s)"
    )
    return JSONResponse(
        status_code=422,
        content=content,
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import asyncio
    if isinstance(exc, asyncio.CancelledError):
        # Allow client disconnects to propagate without generating server error telemetry
        raise exc

    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
    logger.error(
        f"[req:{request_id}] Unhandled server exception on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )

    # Dead-letter diagnostic sink
    try:
        dead_letter = LOG_DIR / "dead_letter.log"
        with open(dead_letter, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} | req:{request_id} | {request.method} {request.url.path} | {type(exc).__name__}: {exc}\n")
    except Exception:
        pass

    content: dict[str, Any] = {
        "detail": "An internal server error occurred.",
        "error_code": "INTERNAL_SERVER_ERROR",
        "title": "Internal Server Error",
        "remediation": "An unexpected server condition occurred. Please retry or contact support with the request reference ID.",
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
    }
    return JSONResponse(
        status_code=500,
        content=content,
        headers={"X-Request-ID": request_id},
    )


@app.get("/static/vocab.json", include_in_schema=False)
@app.get("/vocab", include_in_schema=False)
async def get_vocab(request: Request) -> dict[str, Any]:
    """Dynamically serve autocomplete vocabulary directly from in-memory catalogs."""
    if not hasattr(request.app.state, "vocab") or request.app.state.vocab is None:
        request.app.state.vocab = _build_vocab(request.app.state.catalogs)
    return request.app.state.vocab


app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/v1/repurpose", response_model=RepurposeResponse, summary="Find ranked low-voltage reuse projects")
async def repurpose(payload: RepurposeRequest, request: Request) -> RepurposeResponse:
    catalogs: Catalogs = request.app.state.catalogs
    resolver: Resolver = request.app.state.resolver
    generated_at = f"{catalogs.version[:4]}-{catalogs.version[5:7]}-{catalogs.version[8:10]}T00:00:00Z"
    logger.info(f"[REPURPOSE] Request received: {len(payload.items)} item(s), mode={'browse' if not payload.items else 'match'}")
    if not payload.items:
        recommendations = browse_recommendations(catalogs, payload.options.detail)
        logger.info(f"[REPURPOSE] Browse mode complete: returning {len(recommendations)} available catalog projects")
        return RepurposeResponse(
            meta={"catalog_version": catalogs.version, "generated_at": generated_at, "mode": "browse"},
            resolved_inventory=[], unrecognized=[], safety_banners=[], recommendations=recommendations,
            best_combination=None,
            summary={"items_recognized": 0, "projects_ready": 0, "projects_almost": 0, "top_waste_reduction_grams": 0},
        )

    expansion = expand_inventory(payload.items, catalogs, resolver)
    recommendations, combination = build_recommendations(expansion, catalogs, payload.options)
    if expansion.unrecognized:
        unknown_logger = logging.getLogger("ewaste.unrecognized")
        for item in expansion.unrecognized:
            unknown_logger.warning(json.dumps({"name": item["input"]}, ensure_ascii=False))
    top_waste = combination["total_waste_grams"] if combination is not None else (
        recommendations[0]["waste_reduction"]["grams"] if recommendations else 0
    )
    ready_count = sum(1 for row in recommendations if row["tier"] == "ready")
    almost_count = sum(1 for row in recommendations if row["tier"] == "almost")
    logger.info(
        f"[REPURPOSE] Match complete: recognized={expansion.items_recognized}/{len(payload.items)}, "
        f"unrecognized={len(expansion.unrecognized)}, recommendations={len(recommendations)} "
        f"(ready={ready_count}, almost={almost_count}), top_waste={top_waste}g"
    )
    return RepurposeResponse(
        meta={"catalog_version": catalogs.version, "generated_at": generated_at, "mode": "match"},
        resolved_inventory=expansion.resolved_inventory, unrecognized=expansion.unrecognized,
        safety_banners=expansion.safety_banners, recommendations=recommendations,
        best_combination=combination,
        summary={"items_recognized": expansion.items_recognized,
                 "projects_ready": ready_count,
                 "projects_almost": almost_count,
                 "top_waste_reduction_grams": top_waste},
    )


@app.post(
    "/api/v1/ai_search",
    response_model=RepurposeResponse,
    summary="Find catalogue-backed reuse projects with local AI and web research",
    description=("Uses the same request and response contract as `/api/v1/repurpose`. "
                 "Catalog matching remains deterministic; a local Ollama model ranks catalog candidates using focused Tavily research."),
)
async def ai_search(payload: RepurposeRequest, request: Request) -> RepurposeResponse:
    """Run the established matcher, then safely rank its catalog results with local AI."""
    client_ip = request.client.host if request.client else "unknown"
    ai_search_rate_limiter.check(client_ip, "/api/v1/ai_search")

    catalogs: Catalogs = request.app.state.catalogs
    resolver: Resolver = request.app.state.resolver
    ai_service: AISearchService = request.app.state.ai_search_service
    generated_at = f"{catalogs.version[:4]}-{catalogs.version[5:7]}-{catalogs.version[8:10]}T00:00:00Z"
    logger.info("AI search request started; item_count=%s", len(payload.items))

    if not payload.items:
        deterministic = RepurposeResponse(
            meta={"catalog_version": catalogs.version, "generated_at": generated_at, "mode": "browse"},
            resolved_inventory=[], unrecognized=[], safety_banners=[],
            recommendations=browse_recommendations(catalogs, payload.options.detail), best_combination=None,
            summary={"items_recognized": 0, "projects_ready": 0, "projects_almost": 0, "top_waste_reduction_grams": 0},
        )
    else:
        expansion = expand_inventory(payload.items, catalogs, resolver)
        recommendations, combination = build_recommendations(expansion, catalogs, payload.options)
        if expansion.unrecognized:
            unknown_logger = logging.getLogger("ewaste.unrecognized")
            for item in expansion.unrecognized:
                unknown_logger.warning(json.dumps({"name": item["input"]}, ensure_ascii=False))
        top_waste = combination["total_waste_grams"] if combination is not None else (
            recommendations[0]["waste_reduction"]["grams"] if recommendations else 0
        )
        deterministic = RepurposeResponse(
            meta={"catalog_version": catalogs.version, "generated_at": generated_at, "mode": "match"},
            resolved_inventory=expansion.resolved_inventory, unrecognized=expansion.unrecognized,
            safety_banners=expansion.safety_banners, recommendations=recommendations,
            best_combination=combination,
            summary={"items_recognized": expansion.items_recognized,
                     "projects_ready": sum(1 for row in recommendations if row["tier"] == "ready"),
                     "projects_almost": sum(1 for row in recommendations if row["tier"] == "almost"),
                     "top_waste_reduction_grams": top_waste},
        )

    preferred_ids = await ai_service.rank_recommendations(
        payload=payload,
        resolved_inventory=[item.model_dump(by_alias=True) for item in deterministic.resolved_inventory],
        recommendations=[item.model_dump(by_alias=True) for item in deterministic.recommendations],
    )
    # Model output can only reorder schema-validated deterministic recommendations.
    result = RepurposeResponse.model_validate({
        **deterministic.model_dump(by_alias=True),
        "recommendations": reorder_recommendations(
            [item.model_dump(by_alias=True) for item in deterministic.recommendations], preferred_ids
        ),
    })
    logger.info("AI search complete; recommendations=%s", len(result.recommendations))
    return result


@app.post(
    "/detect",
    summary="Identify an electronic component or device from an uploaded image",
    tags=["AI Vision Detection"],
)
@app.post(
    "/api/v1/detect",
    include_in_schema=False,
)
async def detect_component(
    request: Request,
    image: UploadFile = File(..., description="Image file containing an electronic component or device"),
) -> dict[str, Any]:
    """
    Accepts one image of an electronic component or device.
    Uses MobileNetV3 image classifier followed by catalog resolver
    to return canonical catalog identity and normalized inventory representation.
    """
    if not image or not image.filename:
        raise EmptyImageFileError()

    client_ip = request.client.host if request.client else "unknown"
    vision_rate_limiter.check(client_ip, "/detect")

    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    file_ext = Path(image.filename).suffix.lower()
    if file_ext not in valid_exts:
        raise UnsupportedImageFormatError(file_ext, valid_exts)

    MAX_IMAGE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB max image upload
    content = await image.read()
    if not content or len(content) == 0:
        raise EmptyImageFileError()

    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise ImageSizeLimitExceededError(len(content), MAX_IMAGE_SIZE_BYTES)

    logger.info(f"[DETECT] Image upload received: filename='{image.filename}', size={len(content)} bytes")

    classifier = getattr(request.app.state, "classifier", None)
    if classifier is None:
        raise ModelNotLoadedError()

    try:
        from PIL import Image as PILImage
    except (ImportError, ModuleNotFoundError) as imp_err:
        logger.error(f"[DETECT] Vision dependency PIL/Pillow is not installed: {imp_err}")
        raise ModelNotLoadedError() from imp_err

    try:
        pil_img = PILImage.open(io.BytesIO(content)).convert("RGB")
    except Exception as pil_err:
        logger.warning(f"[DETECT] Corrupted image bytes or PIL decode failure: {pil_err}")
        raise CorruptedImageError() from pil_err

    resolver = getattr(request.app.state, "ai_resolver", None)
    if resolver is None:
        from ai_detection.resolver import CatalogResolver
        resolver = CatalogResolver()

    try:
        pred = classifier.predict(pil_img)
    except Exception as e:
        logger.error(f"Inference error during /detect: {e}", exc_info=True)
        raise InferenceRuntimeError(str(e))

    top_label = pred["top_label"]
    confidence = pred["confidence"]
    is_ambiguous = pred.get("is_ambiguous", False)
    is_low_conf = pred.get("is_low_confidence", False)

    logger.info(
        f"[DETECT] Model prediction: top_label='{top_label}', confidence={confidence:.4f}, "
        f"ambiguous={is_ambiguous}, low_confidence={is_low_conf}"
    )

    # Unknown or low confidence handling
    if is_low_conf or is_ambiguous:
        if is_ambiguous:
            reason = (
                f"Object is ambiguous between {top_label} ({confidence}) "
                f"and {pred.get('top2_label')} ({pred.get('top2_confidence')})"
            )
        else:
            reason = "Object could not be confidently identified"

        logger.info(f"[DETECT] Ambiguous or low-confidence outcome: {reason} -> returning 'unknown'")
        return {
            "detected_label": "unknown",
            "confidence": confidence,
            "resolved": False,
            "reason": reason,
        }

    # Catalog resolution
    res = resolver.resolve(top_label)
    if not res.resolved:
        logger.info(f"[DETECT] Catalog resolution failed for '{top_label}' -> returning unresolved")
        return {
            "detected_label": top_label,
            "confidence": confidence,
            "resolved": False,
            "reason": "Object could not be confidently identified or mapped to the supported catalog",
        }

    logger.info(
        f"[DETECT] Successfully resolved '{top_label}' -> {res.resolved_type}:{res.resolved_id} "
        f"(resolution_conf={res.resolution_confidence:.2f}, match_tier={res.match_tier})"
    )
    return {
        "detected_label": top_label,
        "confidence": confidence,
        "resolved": True,
        "resolved_type": res.resolved_type,
        "resolved_id": res.resolved_id,
        "resolution_confidence": res.resolution_confidence,
        "normalized_inventory": {
            "name": res.resolved_id,
            "type": res.resolved_type,
        },
    }


@app.get(
    "/api/v1/logs",
    summary="Query recent structured server logs and telemetry",
    tags=["Observability & Diagnostics"],
    include_in_schema=False,
)
async def get_recent_logs(
    level: str | None = None,
    limit: int = 50,
    search: str | None = None,
) -> dict[str, Any]:
    """Query recent in-memory log buffer records for telemetry and real-time dashboard debugging."""
    logs = log_buffer.get_logs(level=level, limit=limit, search=search)
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs,
        "stats": log_buffer.get_stats(),
    }


@app.get(
    "/api/v1/logs/stats",
    summary="Retrieve telemetry and log metrics",
    tags=["Observability & Diagnostics"],
    include_in_schema=False,
)
async def get_log_stats() -> dict[str, Any]:
    """Retrieve logger operational statistics, record counts, and uptime."""
    return {
        "status": "healthy",
        "telemetry": log_buffer.get_stats(),
        "log_directory": str(LOG_DIR.resolve()),
    }


@app.post(
    "/api/v1/logs/client",
    summary="Ingest client-side frontend logs and error beacons",
    tags=["Observability & Diagnostics"],
    include_in_schema=False,
)
async def ingest_client_logs(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Ingest client-side error beacons, warnings, and diagnostic telemetry."""
    client_logger = logging.getLogger("ewaste.client")
    logs = payload.get("logs") if isinstance(payload.get("logs"), list) else [payload]
    client_ip = request.client.host if request.client else "unknown"
    ingested_count = 0

    for entry in logs:
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level", "INFO")).upper()
        module = entry.get("module", "frontend")
        message = entry.get("message", "Client log event")
        data = entry.get("data")
        msg = f"[CLIENT:{module}] {message}"
        if data:
            msg += f" | data={json.dumps(data, ensure_ascii=False, default=str)}"

        extra_data = {
            "client_ip": client_ip,
            "client_timestamp": entry.get("timestamp"),
            "client_url": entry.get("url"),
            "user_agent": entry.get("userAgent"),
        }

        if level in {"ERROR", "FATAL", "CRITICAL"}:
            client_logger.error(msg, extra=extra_data)
        elif level in {"WARN", "WARNING"}:
            client_logger.warning(msg, extra=extra_data)
        else:
            client_logger.info(msg, extra=extra_data)
        ingested_count += 1

    return {"status": "accepted", "ingested": ingested_count}
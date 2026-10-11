from __future__ import annotations

import io
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from ai_detection.inference import set_classifier
from ai_detection.resolver import CatalogResolver


def make_test_image_bytes(format: str = "JPEG", size: tuple[int, int] = (100, 100)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=(120, 150, 180))
    img.save(buf, format=format)
    return buf.getvalue()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_detect_missing_image_returns_422(client: TestClient):
    response = client.post("/detect")
    assert response.status_code == 422


def test_detect_invalid_file_extension_returns_400(client: TestClient):
    files = {"image": ("document.txt", b"plain text", "text/plain")}
    response = client.post("/detect", files=files)
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_detect_empty_file_returns_400(client: TestClient):
    files = {"image": ("empty.jpg", b"", "image/jpeg")}
    response = client.post("/detect", files=files)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_detect_corrupt_image_returns_400(client: TestClient):
    files = {"image": ("corrupted.jpg", b"not-a-valid-jpeg-stream", "image/jpeg")}
    response = client.post("/detect", files=files)
    assert response.status_code == 400
    assert "corrupted" in response.json()["detail"].lower()


def test_detect_successful_component_response(client: TestClient):
    mock_classifier = MagicMock()
    mock_classifier.predict.return_value = {
        "top_label": "Arduino Uno",
        "confidence": 0.94,
        "is_ambiguous": False,
        "is_low_confidence": False,
        "top2_label": "Arduino Nano",
        "top2_confidence": 0.04,
    }
    app.state.classifier = mock_classifier
    app.state.ai_resolver = CatalogResolver()

    img_bytes = make_test_image_bytes()
    files = {"image": ("arduino.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["detected_label"] == "Arduino Uno"
    assert data["confidence"] == 0.94
    assert data["resolved"] is True
    assert data["resolved_type"] == "component"
    assert data["resolved_id"] == "arduino_uno"
    assert data["resolution_confidence"] == 1.0
    assert data["normalized_inventory"] == {
        "name": "arduino_uno",
        "type": "component",
    }


def test_detect_successful_device_response(client: TestClient):
    mock_classifier = MagicMock()
    mock_classifier.predict.return_value = {
        "top_label": "Phone Charger",
        "confidence": 0.91,
        "is_ambiguous": False,
        "is_low_confidence": False,
        "top2_label": "Power Bank",
        "top2_confidence": 0.05,
    }
    app.state.classifier = mock_classifier
    app.state.ai_resolver = CatalogResolver()

    img_bytes = make_test_image_bytes()
    files = {"image": ("charger.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["detected_label"] == "Phone Charger"
    assert data["confidence"] == 0.91
    assert data["resolved"] is True
    assert data["resolved_type"] == "device"
    assert data["resolved_id"] == "phone_charger"
    assert data["resolution_confidence"] == 1.0
    assert data["normalized_inventory"] == {
        "name": "phone_charger",
        "type": "device",
    }


def test_detect_low_confidence_unresolved_response(client: TestClient):
    mock_classifier = MagicMock()
    mock_classifier.predict.return_value = {
        "top_label": "LED",
        "confidence": 0.42,
        "is_ambiguous": False,
        "is_low_confidence": True,
        "top2_label": "Diode",
        "top2_confidence": 0.38,
    }
    app.state.classifier = mock_classifier
    app.state.ai_resolver = CatalogResolver()

    img_bytes = make_test_image_bytes()
    files = {"image": ("fuzzy.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["detected_label"] == "unknown"
    assert data["confidence"] == 0.42
    assert data["resolved"] is False
    assert "confidently identified" in data["reason"]


def test_detect_ambiguous_prediction_response(client: TestClient):
    mock_classifier = MagicMock()
    mock_classifier.predict.return_value = {
        "top_label": "Arduino Uno",
        "confidence": 0.44,
        "is_ambiguous": True,
        "is_low_confidence": False,
        "top2_label": "Arduino Nano",
        "top2_confidence": 0.42,
    }
    app.state.classifier = mock_classifier
    app.state.ai_resolver = CatalogResolver()

    img_bytes = make_test_image_bytes()
    files = {"image": ("ambiguous.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["detected_label"] == "unknown"
    assert data["resolved"] is False
    assert "ambiguous" in data["reason"].lower()


def test_detect_model_not_loaded_returns_503(client: TestClient):
    app.state.classifier = None
    set_classifier(None)

    img_bytes = make_test_image_bytes()
    files = {"image": ("test.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 503
    assert "not loaded" in response.json()["detail"].lower()


def test_detect_real_model_inference(client: TestClient):
    """Integration test with the real trained MobileNetV3 model on a sample image."""
    from ai_detection.inference import get_classifier
    app.state.classifier = get_classifier()

    img_bytes = make_test_image_bytes(format="JPEG", size=(224, 224))
    files = {"image": ("real_test.jpg", img_bytes, "image/jpeg")}
    response = client.post("/detect", files=files)

    assert response.status_code == 200
    data = response.json()
    assert "detected_label" in data
    assert "confidence" in data
    assert "resolved" in data

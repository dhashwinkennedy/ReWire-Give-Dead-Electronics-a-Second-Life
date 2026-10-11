from __future__ import annotations

import pytest
from ai_detection.resolver import CatalogResolutionResult, CatalogResolver


@pytest.fixture
def resolver() -> CatalogResolver:
    return CatalogResolver(fuzzy_threshold=80.0)


def test_exact_canonical_component_match(resolver: CatalogResolver):
    result = resolver.resolve("arduino_uno")
    assert result.resolved is True
    assert result.resolved_id == "arduino_uno"
    assert result.resolved_type == "component"
    assert result.match_tier == "exact_id"
    assert result.resolution_confidence == 1.0


def test_exact_display_name_component_match(resolver: CatalogResolver):
    result = resolver.resolve("Arduino Uno")
    assert result.resolved is True
    assert result.resolved_id == "arduino_uno"
    assert result.resolved_type == "component"
    assert result.resolution_confidence >= 0.99


def test_exact_canonical_device_match(resolver: CatalogResolver):
    result = resolver.resolve("phone_charger")
    assert result.resolved is True
    assert result.resolved_id == "phone_charger"
    assert result.resolved_type == "device"
    assert result.resolution_confidence == 1.0


def test_exact_display_name_device_match(resolver: CatalogResolver):
    result = resolver.resolve("Phone Charger")
    assert result.resolved is True
    assert result.resolved_id == "phone_charger"
    assert result.resolved_type == "device"
    assert result.resolution_confidence >= 0.99


def test_exact_alias_match(resolver: CatalogResolver):
    # 'micro-usb charger' is in aliases of phone_charger
    result = resolver.resolve("micro-usb charger")
    assert result.resolved is True
    assert result.resolved_id == "phone_charger"
    assert result.resolved_type == "device"
    assert result.match_tier in {"exact_alias", "normalized_alias"}
    assert result.resolution_confidence >= 0.94


def test_normalized_alias_match(resolver: CatalogResolver):
    # 'step down transformer' is an alias of transformer
    result = resolver.resolve("step down transformer")
    assert result.resolved is True
    assert result.resolved_id == "transformer"
    assert result.resolved_type == "component"
    assert result.resolution_confidence >= 0.94


def test_fuzzy_match_typo(resolver: CatalogResolver):
    # Typo: 'arduno uno' -> 'arduino_uno'
    result = resolver.resolve("arduno uno")
    assert result.resolved is True
    assert result.resolved_id == "arduino_uno"
    assert result.resolved_type == "component"
    assert result.match_tier == "fuzzy"
    assert result.resolution_confidence >= 0.80


def test_fuzzy_match_component_plural(resolver: CatalogResolver):
    # 'potentiometers' -> 'potentiometer'
    result = resolver.resolve("potentiometers")
    assert result.resolved is True
    assert result.resolved_id == "potentiometer"
    assert result.resolved_type == "component"


def test_unresolved_result(resolver: CatalogResolver):
    result = resolver.resolve("totally_fake_part_xyz_9999")
    assert result.resolved is False
    assert result.resolved_id is None
    assert result.resolved_type is None
    assert result.match_tier == "unresolved"
    assert result.resolution_confidence == 0.0
    assert result.reason is not None


def test_empty_string_unresolved(resolver: CatalogResolver):
    result = resolver.resolve("")
    assert result.resolved is False
    assert result.match_tier == "unresolved"

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from shopguide.coordinates import PageTransform, pixel_box
from shopguide.schemas import (
    BBox,
    Locator,
    Product,
    ProductVariant,
    VideoLocator,
)
from shopguide.settings import Settings
from shopguide.tools.registry import arguments_hash


def test_u01_strict_product_and_variant():
    base = {
        "product_id": "product_one",
        "domain": "demo",
        "brand": "Test",
        "model": "100",
        "category": "fixture",
    }
    for change in (
        {"unexpected": 1},
        {"model": 100},
        {"product_id": "../secret"},
        {"aliases": ("Foo",)},
    ):
        with pytest.raises(ValidationError):
            Product(**(base | change))
    with pytest.raises(ValidationError):
        ProductVariant(variant_id="variant_one")
    with pytest.raises(ValidationError):
        ProductVariant(variant_id="variant_one", product_id="product_one", market=True)
    assert (
        ProductVariant(variant_id="variant_one", product_id="product_one").market
        is None
    )


def test_u03_locator_discriminator_and_time():
    adapter = TypeAdapter(Locator)
    good = {
        "type": "video",
        "video_version_id": "video_one",
        "start_s": 0.0,
        "end_s": 2.0,
        "duration_s": 5.0,
    }
    assert isinstance(adapter.validate_python(good), VideoLocator)
    for change in (
        {"page_id": "page_one"},
        {"end_s": 6.0},
        {"frame_timestamp_s": 3.0},
        {"start_s": float("nan")},
        {"type": "manual"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(good | change)


@pytest.mark.parametrize(
    "box",
    [(0, 0, 0, 1), (-0.1, 0, 1, 1), (0, 0, 1, 1.1), (0, 1, 1, 0), (0, 0, math.inf, 1)],
)
def test_u04_bad_boxes(box):
    with pytest.raises(ValidationError):
        BBox(**dict(zip(("x0", "y0", "x1", "y1"), box)))


@given(
    st.floats(min_value=0, max_value=0.9, allow_nan=False),
    st.floats(min_value=0, max_value=0.9, allow_nan=False),
    st.integers(1, 2000),
    st.integers(1, 2000),
)
def test_u04_positive_pixel_area(x, y, width, height):
    box = BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.1)
    x0, y0, x1, y1 = pixel_box(box, width, height)
    assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@given(
    st.floats(min_value=10, max_value=210, allow_nan=False),
    st.floats(min_value=20, max_value=120, allow_nan=False),
)
def test_u05_rotated_cropbox_roundtrip(rotation, x, y):
    transform = PageTransform((10.0, 20.0, 210.0, 120.0), rotation)
    assert transform.inverse(*transform.forward(x, y)) == pytest.approx(
        (x, y), abs=1e-9
    )
    full = transform.box((10.0, 20.0, 210.0, 120.0))
    assert full == BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    matrix = transform.matrix()
    assert (
        matrix[0] * x + matrix[1] * y + matrix[2],
        matrix[3] * x + matrix[4] * y + matrix[5],
    ) == pytest.approx(transform.forward(x, y))


def test_u05_known_rotation():
    assert PageTransform((10.0, 20.0, 210.0, 120.0), 90).forward(10, 120) == (1, 0)
    assert PageTransform((10.0, 20.0, 210.0, 120.0), 270).forward(10, 120) == (0, 1)


def test_u10_parameter_hash():
    assert arguments_hash({"a": 1, "b": {"x": "值", "y": [1, 2]}}) == arguments_hash(
        json.loads('{"b":{"y":[1,2],"x":"值"},"a":1}')
    )
    assert arguments_hash({"x": [1, 2]}) != arguments_hash({"x": [2, 1]})
    with pytest.raises(ValueError):
        arguments_hash({"x": float("nan")})


def test_u17_formal_gate():
    assert Settings().model_mode == "fake"
    for config in (
        {"formal": True},
        {"formal": True, "model_mode": "real"},
        {"allow_generated_images": True},
        {"allow_real_business_writes": True},
    ):
        with pytest.raises(ValidationError):
            Settings(**config)
    valid = {
        "model_mode": "real",
        "formal": True,
        "planner": {
            "model_id": "planner",
            "source_revision": "a" * 40,
            "endpoint_env": "SG_PLANNER_BASE_URL",
            "api_key_env": "SG_PLANNER_API_KEY",
        },
        "vision": {
            "model_id": "vision",
            "source_revision": "b" * 40,
            "endpoint_env": "SG_VISION_BASE_URL",
            "api_key_env": "SG_VISION_API_KEY",
        },
        "data_manifest": {
            "dataset_id": "pm209",
            "source_revision": "c" * 40,
            "archive_sha256": "d" * 64,
            "audit_status": "passed",
            "license_review_status": "approved_for_research",
        },
    }
    assert Settings.model_validate(valid).formal
    del valid["vision"]["source_revision"]
    with pytest.raises(ValidationError):
        Settings.model_validate(valid)

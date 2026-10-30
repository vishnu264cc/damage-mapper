from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from app.core.config import get_settings
import app.services.ai.engine as ai_engine
from app.services.ai.adapters import ClassPrediction, DetectedObject
from app.services.ai.clip import CATEGORY_PROMPTS
from app.services.ai.engine import AIAnalysis, assess_image
from app.services.ai.preprocessing import ImageValidationError
from app.services.ai.clip import ClipImageClassifier
from app.services.ai.yolo import YoloObjectDetector
from app.services.scoring import severity_for


class FakeClassifier:
    model_name = "Test CLIP adapter"

    def __init__(self, predictions: list[ClassPrediction]) -> None:
        self.predictions = predictions

    def classify(self, image: Image.Image) -> list[ClassPrediction]:
        assert image.mode == "RGB"
        assert max(image.size) <= 1024
        return self.predictions


class FakeDetector:
    model_name = "Test YOLO adapter"

    def detect(self, _image: Image.Image) -> list[DetectedObject]:
        return [DetectedObject("person", 0.88, (0.03, 0.05, 0.25, 0.5))]


class FailedClassifier:
    model_name = "Unavailable test model"

    def classify(self, image: Image.Image) -> list[ClassPrediction]:
        raise RuntimeError("model weights unavailable")


class FailedDetector:
    model_name = "Unavailable test detector"

    def detect(self, _image: Image.Image) -> list[DetectedObject]:
        raise RuntimeError("detector weights unavailable")


def _sample_path(tmp_path: Path, png_image: bytes) -> Path:
    path = tmp_path / "sample.png"
    path.write_bytes(png_image)
    return path


def test_clip_adapter_results_include_candidates_and_optional_yolo_objects(
    tmp_path: Path, png_image: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    predictions = [
        ClassPrediction(category, 0.8 - index * 0.1)
        for index, category in enumerate(CATEGORY_PROMPTS)
    ]
    monkeypatch.setattr(get_settings(), "ai_review_threshold", 0.55)

    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=FakeClassifier(predictions),
        detector=FakeDetector(),
    )

    assert result.status == "complete"
    assert result.predicted_category == predictions[0].category
    assert result.confidence == 0.8
    assert len(result.candidate_classes) == 3
    assert result.review_required is False
    assert result.detector_status == "available"
    assert result.detector_model_name == "Test YOLO adapter"
    assert result.detected_objects[0].label == "person"
    assert result.processing_time_ms >= 0


def test_classifier_category_vocabulary_is_explicit() -> None:
    assert set(CATEGORY_PROMPTS) == {
        "Flood damage",
        "Building damage",
        "Fire and smoke",
        "Landslide",
        "Cyclone damage",
        "Road and infrastructure obstruction",
        "Other / unclear",
    }


def test_clip_adapter_uses_pretrained_pipeline_and_maps_supported_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = CATEGORY_PROMPTS["Building damage"]
    calls: list[tuple[str, str]] = []

    def fake_pipeline(task: str, model: str):
        calls.append((task, model))

        def classify(image: Image.Image, candidate_labels: list[str]):
            assert prompt in candidate_labels
            return [
                {"label": prompt, "score": 0.81},
                {"label": CATEGORY_PROMPTS["Flood damage"], "score": 0.62},
            ]

        return classify

    transformers = ModuleType("transformers")
    transformers.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    adapter = ClipImageClassifier("example/fine-tuned-clip")

    with Image.new("RGB", (20, 20)) as image:
        predictions = adapter.classify(image)

    assert calls == [("zero-shot-image-classification", "example/fine-tuned-clip")]
    assert adapter.model_name == "CLIP zero-shot (example/fine-tuned-clip)"
    assert predictions == [
        ClassPrediction("Building damage", 0.81),
        ClassPrediction("Flood damage", 0.62),
    ]


def test_yolo_adapter_returns_normalized_real_detection_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Tensor:
        def __init__(self, value):
            self.value = value

        def tolist(self):
            return self.value

    class FakeYolo:
        def __init__(self, model_path: str) -> None:
            assert model_path == "custom-hazards.pt"

        def predict(self, *, source: Image.Image, verbose: bool):
            assert verbose is False
            boxes = SimpleNamespace(
                cls=Tensor([0]),
                conf=Tensor([0.9]),
                xyxy=Tensor([[10, 5, 50, 25]]),
            )
            return [SimpleNamespace(boxes=boxes, names={0: "person"})]

    ultralytics = ModuleType("ultralytics")
    ultralytics.YOLO = FakeYolo
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
    adapter = YoloObjectDetector("custom-hazards.pt")

    with Image.new("RGB", (100, 50)) as image:
        detections = adapter.detect(image)

    assert detections == [DetectedObject("person", 0.9, (0.1, 0.1, 0.5, 0.5))]


def test_low_confidence_prediction_is_flagged_for_review(
    tmp_path: Path, png_image: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "ai_review_threshold", 0.55)
    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=FakeClassifier([ClassPrediction("Flood damage", 0.42)]),
        detector=None,
    )

    assert result.predicted_category == "Flood damage"
    assert result.confidence == 0.42
    assert result.review_required is True
    assert result.detector_status == "disabled"


def test_missing_classifier_returns_no_fabricated_prediction(
    tmp_path: Path, png_image: bytes
) -> None:
    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=None,
        detector=None,
    )

    assert result.status == "unavailable"
    assert result.predicted_category is None
    assert result.confidence is None
    assert result.candidate_classes == []
    assert result.review_required is True


def test_classifier_exception_falls_back_without_claiming_a_prediction(
    tmp_path: Path, png_image: bytes
) -> None:
    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=FailedClassifier(),
        detector=None,
    )

    assert result.status == "unavailable"
    assert result.predicted_category is None
    assert result.confidence is None
    assert result.candidate_classes == []
    assert result.review_required is True


def test_clip_load_failure_returns_unavailable_without_guessing(
    tmp_path: Path, png_image: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_classifier() -> None:
        raise RuntimeError("model weights unavailable")

    monkeypatch.setattr(get_settings(), "disable_model", False)
    monkeypatch.setattr(ai_engine, "_get_classifier", fail_classifier)
    monkeypatch.setattr(ai_engine, "_get_detector", lambda: None)

    result = ai_engine.analyze_image(_sample_path(tmp_path, png_image))

    assert result.status == "unavailable"
    assert result.predicted_category is None
    assert result.confidence is None
    assert result.candidate_classes == []
    assert result.review_required is True


def test_optional_yolo_failure_does_not_discard_clip_result(
    tmp_path: Path, png_image: bytes
) -> None:
    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=FakeClassifier([ClassPrediction("Flood damage", 0.9)]),
        detector=FailedDetector(),
    )

    assert result.status == "complete"
    assert result.predicted_category == "Flood damage"
    assert result.detector_status == "unavailable"
    assert result.detected_objects == []


def test_invalid_adapter_predictions_are_not_returned(
    tmp_path: Path, png_image: bytes
) -> None:
    result = assess_image(
        _sample_path(tmp_path, png_image),
        classifier=FakeClassifier(
            [
                ClassPrediction("unlisted category", 0.99),
                ClassPrediction("Flood damage", 1.2),
            ]
        ),
        detector=None,
    )

    assert result.status == "unavailable"
    assert result.predicted_category is None
    assert result.confidence is None
    assert result.candidate_classes == []


def test_invalid_image_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "not-an-image.png"
    invalid.write_bytes(b"not an image")

    with pytest.raises(ImageValidationError):
        assess_image(invalid, classifier=None, detector=None)


def test_report_severity_estimate_does_not_depend_on_image_category() -> None:
    severity, score = severity_for(
        urgency=50, people_affected=10, access_blocked=False
    )
    assert severity == "Low"
    assert score == 31.8


def test_incident_severity_is_independent_of_classifier_category(
    client, png_image: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.incidents as incident_service

    def result(category: str) -> AIAnalysis:
        return AIAnalysis(
            status="complete",
            predicted_category=category,
            confidence=0.88,
            model_name="Test CLIP adapter",
            processing_time_ms=12.0,
            candidate_classes=[ClassPrediction(category, 0.88)],
            detected_objects=[],
            review_required=False,
            confidence_threshold=0.55,
            detector_status="disabled",
            detector_model_name=None,
        )

    assessments = iter([result("Flood damage"), result("Building damage")])
    monkeypatch.setattr(
        incident_service, "analyze_image", lambda _path: next(assessments)
    )
    payload = {
        "description": "Reported incident evidence",
        "urgency": "75",
        "people_affected": "24",
        "access_blocked": "true",
        "reporter": "Field team",
    }

    flood_response = client.post(
        "/api/incidents",
        data={**payload, "latitude": "12.9716", "longitude": "77.5946"},
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    building_response = client.post(
        "/api/incidents",
        data={**payload, "latitude": "40.7128", "longitude": "-74.0060"},
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert flood_response.status_code == building_response.status_code == 201
    flood = flood_response.json()
    building = building_response.json()
    assert flood["damage_type"] == "Flood damage"
    assert building["damage_type"] == "Building damage"
    assert flood["severity_score"] == building["severity_score"]


def test_ai_analysis_api_returns_explicit_unavailable_status(
    client, png_image: bytes
) -> None:
    response = client.post(
        "/api/ai/analyze",
        files={"image": ("evidence.png", png_image, "image/png")},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "unavailable"
    assert result["predicted_category"] is None
    assert result["confidence"] is None
    assert result["candidate_classes"] == []
    assert result["review_required"] is True
    from app.services.uploads import UPLOADS

    assert list(UPLOADS.iterdir()) == []

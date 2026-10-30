from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from app.core.config import get_settings
from app.services.ai.adapters import (
    ClassPrediction,
    DetectedObject,
    ImageClassifier,
    ObjectDetector,
)
from app.services.ai.clip import CATEGORY_PROMPTS, ClipImageClassifier
from app.services.ai.preprocessing import ImageValidationError, load_preprocessed_image
from app.services.ai.yolo import YoloObjectDetector

logger = logging.getLogger(__name__)
_adapter_lock = threading.Lock()
_classifier: ImageClassifier | None = None
_detector: ObjectDetector | None = None
_classifier_error: str | None = None
_detector_error: str | None = None
_SUPPORTED_CATEGORIES = frozenset(CATEGORY_PROMPTS)


@dataclass(frozen=True)
class AIAnalysis:
    status: Literal["complete", "unavailable"]
    predicted_category: str | None
    confidence: float | None
    model_name: str
    processing_time_ms: float
    candidate_classes: list[ClassPrediction]
    detected_objects: list[DetectedObject]
    review_required: bool
    confidence_threshold: float
    detector_status: Literal["available", "disabled", "unavailable"]
    detector_model_name: str | None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "predicted_category": self.predicted_category,
            "confidence": self.confidence,
            "model_name": self.model_name,
            "processing_time_ms": self.processing_time_ms,
            "candidate_classes": [asdict(candidate) for candidate in self.candidate_classes],
            "detected_objects": [asdict(detection) for detection in self.detected_objects],
            "review_required": self.review_required,
            "confidence_threshold": self.confidence_threshold,
            "detector_status": self.detector_status,
            "detector_model_name": self.detector_model_name,
        }


def _get_classifier() -> ImageClassifier:
    global _classifier, _classifier_error
    with _adapter_lock:
        if _classifier is None:
            _classifier = ClipImageClassifier(get_settings().clip_model)
            _classifier_error = None
        return _classifier


def _get_detector() -> ObjectDetector | None:
    global _detector, _detector_error
    model_path = get_settings().yolo_model
    if not model_path:
        return None
    with _adapter_lock:
        if _detector is None:
            _detector = YoloObjectDetector(model_path)
            _detector_error = None
        return _detector


def assess_image(
    path: Path,
    *,
    classifier: ImageClassifier | None,
    detector: ObjectDetector | None,
    classifier_error: bool = False,
    detector_error: bool = False,
    started_at: float | None = None,
) -> AIAnalysis:
    started = started_at if started_at is not None else time.perf_counter()
    image = load_preprocessed_image(path)
    settings = get_settings()

    predictions: list[ClassPrediction] = []
    objects: list[DetectedObject] = []
    detected_by: ObjectDetector | None = detector
    classification_error = classifier_error
    detection_failed = detector_error
    if classifier is not None:
        try:
            predictions = _validated_predictions(classifier.classify(image))
            if predictions:
                _clear_classifier_error()
        except Exception as error:
            classification_error = True
            _set_classifier_error(error)
            logger.exception("CLIP image classification failed")

    if detector is not None:
        try:
            objects = detector.detect(image)
            _clear_detector_error()
        except Exception as error:
            detection_failed = True
            detected_by = None
            _set_detector_error(error)
            logger.exception("YOLO object detection failed")

    image.close()
    predictions = sorted(predictions, key=lambda item: item.confidence, reverse=True)[:3]
    top = predictions[0] if predictions else None
    status: Literal["complete", "unavailable"] = (
        "complete" if top is not None and not classification_error else "unavailable"
    )
    threshold = settings.ai_review_threshold
    model_label = (
        classifier.model_name
        if classifier is not None
        else (
            "Demo fallback (model disabled)"
            if settings.disable_model
            else "Model unavailable; human review required"
        )
    )
    detector_status: Literal["available", "disabled", "unavailable"]
    if detected_by is not None:
        detector_status = "available"
    elif detector_error or detection_failed:
        detector_status = "unavailable"
    else:
        detector_status = "disabled"

    return AIAnalysis(
        status=status,
        predicted_category=top.category if status == "complete" and top else None,
        confidence=top.confidence if status == "complete" and top else None,
        model_name=model_label,
        processing_time_ms=round((time.perf_counter() - started) * 1000, 2),
        candidate_classes=predictions,
        detected_objects=objects,
        review_required=(
            status != "complete" or top is None or top.confidence < threshold
        ),
        confidence_threshold=threshold,
        detector_status=detector_status,
        detector_model_name=detected_by.model_name if detected_by else None,
    )


def analyze_image(path: Path) -> AIAnalysis:
    started = time.perf_counter()
    if get_settings().disable_model:
        return assess_image(path, classifier=None, detector=None)

    classifier: ImageClassifier | None = None
    detector: ObjectDetector | None = None
    classifier_error = False
    detector_error = False
    try:
        classifier = _get_classifier()
    except Exception as error:
        classifier_error = True
        _set_classifier_error(error)
        logger.exception("CLIP model could not be loaded")
    try:
        detector = _get_detector()
    except Exception as error:
        detector_error = True
        _set_detector_error(error)
        logger.exception("YOLO model could not be loaded")
    return assess_image(
        path,
        classifier=classifier,
        detector=detector,
        classifier_error=classifier_error,
        detector_error=detector_error,
        started_at=started,
    )


def _validated_predictions(
    predictions: list[ClassPrediction],
) -> list[ClassPrediction]:
    valid: list[ClassPrediction] = []
    for prediction in predictions:
        confidence = prediction.confidence
        if (
            prediction.category in _SUPPORTED_CATEGORIES
            and isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
        ):
            valid.append(ClassPrediction(prediction.category, float(confidence)))
    return valid


def _set_classifier_error(error: Exception) -> None:
    global _classifier_error
    _classifier_error = f"{type(error).__name__}: model load or inference failed"


def _clear_classifier_error() -> None:
    global _classifier_error
    _classifier_error = None


def _set_detector_error(error: Exception) -> None:
    global _detector_error
    _detector_error = f"{type(error).__name__}: model load or inference failed"


def _clear_detector_error() -> None:
    global _detector_error
    _detector_error = None


def model_error() -> str | None:
    return _classifier_error or _detector_error


def model_name() -> str:
    settings = get_settings()
    if settings.disable_model:
        return "AI disabled (human review required)"
    detector = f"; YOLO {settings.yolo_model}" if settings.yolo_model else ""
    return f"CLIP zero-shot ({settings.clip_model}){detector}"

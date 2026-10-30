from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image

from app.services.ai.adapters import DetectedObject

MIN_DETECTION_CONFIDENCE = 0.25
MAX_DETECTIONS = 25


class YoloObjectDetector:
    def __init__(self, model_path: str) -> None:
        from ultralytics import YOLO

        self._model_path = model_path
        self._model: Any = YOLO(model_path)

    @property
    def model_name(self) -> str:
        return f"YOLO ({Path(self._model_path).name or self._model_path})"

    def detect(self, image: Image.Image) -> list[DetectedObject]:
        results = self._model.predict(source=image, verbose=False)
        if not results:
            return []

        result = results[0]
        boxes = result.boxes
        if boxes is None:
            return []

        width, height = image.size
        class_ids = boxes.cls.tolist()
        confidences = boxes.conf.tolist()
        coordinates = boxes.xyxy.tolist()
        names = result.names
        detections: list[DetectedObject] = []
        for class_id, confidence, box in zip(
            class_ids, confidences, coordinates, strict=True
        ):
            if (
                not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                continue
            if confidence < MIN_DETECTION_CONFIDENCE or len(box) != 4:
                continue
            normalized_box = (
                min(1.0, max(0.0, float(box[0]) / width)),
                min(1.0, max(0.0, float(box[1]) / height)),
                min(1.0, max(0.0, float(box[2]) / width)),
                min(1.0, max(0.0, float(box[3]) / height)),
            )
            if not all(math.isfinite(value) for value in normalized_box):
                continue
            label = (
                names.get(int(class_id), str(class_id))
                if isinstance(names, dict)
                else names[int(class_id)]
            )
            detections.append(
                DetectedObject(
                    label=str(label),
                    confidence=float(confidence),
                    box=normalized_box,
                )
            )
        detections.sort(key=lambda item: item.confidence, reverse=True)
        return detections[:MAX_DETECTIONS]

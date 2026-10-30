from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class ClassPrediction:
    category: str
    confidence: float


@dataclass(frozen=True)
class DetectedObject:
    label: str
    confidence: float
    box: tuple[float, float, float, float]


class ImageClassifier(Protocol):
    @property
    def model_name(self) -> str: ...

    def classify(self, image: Image.Image) -> list[ClassPrediction]: ...


class ObjectDetector(Protocol):
    @property
    def model_name(self) -> str: ...

    def detect(self, image: Image.Image) -> list[DetectedObject]: ...

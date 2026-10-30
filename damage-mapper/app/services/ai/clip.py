from __future__ import annotations

import math
from typing import Any

from PIL import Image

from app.services.ai.adapters import ClassPrediction

CATEGORY_PROMPTS = {
    "Flood damage": "a photograph of flood damage, flood water, or a flooded area",
    "Building damage": "a photograph of a damaged, collapsed, or structurally failed building",
    "Fire and smoke": "a photograph of fire, burning, or smoke damage",
    "Landslide": "a photograph of a landslide, mudslide, or slope failure",
    "Cyclone damage": "a photograph of cyclone or severe wind damage, fallen trees, or damaged roofs",
    "Road and infrastructure obstruction": "a photograph of a blocked or damaged road, bridge, or infrastructure",
    "Other / unclear": "a photograph with no clear visible disaster damage or an unclear scene",
}


class ClipImageClassifier:
    def __init__(self, model_id: str) -> None:
        from transformers import pipeline

        self._model_id = model_id
        self._pipeline: Any = pipeline("zero-shot-image-classification", model=model_id)

    @property
    def model_name(self) -> str:
        return f"CLIP zero-shot ({self._model_id})"

    def classify(self, image: Image.Image) -> list[ClassPrediction]:
        results = self._pipeline(
            image,
            candidate_labels=list(CATEGORY_PROMPTS.values()),
        )
        prompt_categories = {prompt: category for category, prompt in CATEGORY_PROMPTS.items()}
        predictions: list[ClassPrediction] = []
        for result in results:
            category = prompt_categories.get(result.get("label"))
            confidence = result.get("score")
            if (
                category is not None
                and isinstance(confidence, (int, float))
                and math.isfinite(confidence)
                and 0 <= confidence <= 1
            ):
                predictions.append(ClassPrediction(category, float(confidence)))
        return sorted(predictions, key=lambda item: item.confidence, reverse=True)

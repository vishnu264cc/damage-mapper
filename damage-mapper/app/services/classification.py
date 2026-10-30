from __future__ import annotations

from pathlib import Path

from app.services.ai.engine import analyze_image, model_error, model_name


def classify_image(path: Path) -> tuple[str, float, str]:
    """Compatibility facade for callers using the original tuple response."""
    assessment = analyze_image(path)
    return (
        assessment.predicted_category or "Needs review",
        assessment.confidence or 0.0,
        assessment.model_name,
    )

from __future__ import annotations

from app.schemas.prioritization import PriorityWeights
from app.services.prioritization.priority import (
    DEFAULT_PRIORITY_WEIGHTS,
    calculate_priority,
    population_exposure,
)
from app.services.prioritization.severity import estimate_severity


def priority(
    urgency: int,
    people_affected: int,
    access_blocked: bool,
    severity_score: float,
) -> tuple[float, float]:
    """Compatibility wrapper for callers of the original scoring utility."""
    severity = (
        "Critical"
        if severity_score >= 80
        else "High"
        if severity_score >= 60
        else "Moderate"
        if severity_score >= 40
        else "Low"
    )
    assessment = calculate_priority(
        severity_score=severity_score,
        urgency=urgency,
        people_affected=people_affected,
        access_blocked=access_blocked,
        severity=severity,
        model_review_required=True,
        weights=PriorityWeights(**DEFAULT_PRIORITY_WEIGHTS),
    )
    return assessment.score, round(population_exposure(people_affected), 1)


def severity_for(
    urgency: int,
    people_affected: int,
    access_blocked: bool,
) -> tuple[str, float]:
    """Compatibility wrapper for the independent preliminary severity estimator."""
    assessment = estimate_severity(
        urgency=urgency,
        people_affected=people_affected,
        access_blocked=access_blocked,
    )
    return assessment.severity, assessment.score

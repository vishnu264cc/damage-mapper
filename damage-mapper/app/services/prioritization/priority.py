from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.schemas.prioritization import PriorityWeights
from app.services.prioritization.severity import PEOPLE_NORMALIZATION_CAP

DEFAULT_PRIORITY_WEIGHTS = {
    "severity": 0.40,
    "urgency": 0.25,
    "exposure": 0.20,
    "accessibility": 0.15,
}
HIGH_RISK_THRESHOLD = 75.0


@dataclass(frozen=True)
class PriorityFactor:
    factor: str
    value: float
    weight: float
    contribution: float
    explanation: str


@dataclass(frozen=True)
class PriorityAssessment:
    score: float
    factors: list[PriorityFactor]
    weights: dict[str, float]
    high_risk: bool
    high_risk_review_required: bool
    review_required: bool
    high_risk_threshold: float
    preliminary: bool = True
    label: str = "Preliminary decision-support assessment"

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "factors": [asdict(factor) for factor in self.factors],
            "weights": self.weights,
            "high_risk": self.high_risk,
            "high_risk_review_required": self.high_risk_review_required,
            "review_required": self.review_required,
            "high_risk_threshold": self.high_risk_threshold,
            "preliminary": self.preliminary,
            "label": self.label,
        }


def population_exposure(people_affected: int) -> float:
    if people_affected <= 0:
        return 0.0
    return min(
        100.0,
        100.0
        * math.log1p(people_affected)
        / math.log1p(PEOPLE_NORMALIZATION_CAP),
    )


def calculate_priority(
    *,
    severity_score: float,
    urgency: int,
    people_affected: int,
    access_blocked: bool,
    severity: str,
    model_review_required: bool,
    weights: PriorityWeights,
    high_risk_threshold: float = HIGH_RISK_THRESHOLD,
) -> PriorityAssessment:
    values = [
        (
            "severity",
            float(severity_score),
            weights.severity,
            f"Preliminary severity score: {severity_score:.1f}/100.",
        ),
        ("urgency", float(urgency), weights.urgency, f"Reported urgency: {urgency}/100."),
        (
            "exposure",
            population_exposure(people_affected),
            weights.exposure,
            f"Population exposure based on {people_affected} reported people affected.",
        ),
        (
            "accessibility",
            100.0 if access_blocked else 0.0,
            weights.accessibility,
            "Access is reported blocked." if access_blocked else "Access is not reported blocked.",
        ),
    ]
    factors = [
        PriorityFactor(
            factor=name,
            value=round(value, 2),
            weight=weight,
            contribution=round(value * weight, 2),
            explanation=explanation,
        )
        for name, value, weight, explanation in values
    ]
    score = round(min(100.0, max(0.0, sum(factor.contribution for factor in factors))), 1)
    high_risk = severity == "Critical" or score >= high_risk_threshold
    return PriorityAssessment(
        score=score,
        factors=factors,
        weights=weights.model_dump(),
        high_risk=high_risk,
        high_risk_review_required=high_risk,
        review_required=high_risk or model_review_required,
        high_risk_threshold=high_risk_threshold,
    )

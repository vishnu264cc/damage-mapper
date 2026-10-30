from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable

URGENCY_WEIGHT = 0.40
PEOPLE_WEIGHT = 0.25
ACCESS_WEIGHT = 0.20
VISUAL_WEIGHT = 0.15
PEOPLE_NORMALIZATION_CAP = 5_000

VISUAL_CATEGORY_SCORES = {
    "Flood damage": 70.0,
    "Building damage": 90.0,
    "Fire and smoke": 95.0,
    "Landslide": 88.0,
    "Cyclone damage": 78.0,
    "Road and infrastructure obstruction": 62.0,
}
VISUAL_HAZARD_SCORES = {
    "fire": 95.0,
    "smoke": 90.0,
    "rubble": 88.0,
    "debris": 80.0,
    "flood": 72.0,
    "water": 60.0,
    "collapsed": 90.0,
    "structural damage": 88.0,
}


@dataclass(frozen=True)
class SeverityFactor:
    factor: str
    value: float
    weight: float
    contribution: float
    evidence: str


@dataclass(frozen=True)
class SeverityAssessment:
    severity: str
    score: float
    factors: list[SeverityFactor]
    evidence: list[str]
    uncertainty: list[str]
    preliminary: bool = True
    label: str = "Preliminary decision-support assessment"

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "score": self.score,
            "factors": [asdict(factor) for factor in self.factors],
            "evidence": self.evidence,
            "uncertainty": self.uncertainty,
            "preliminary": self.preliminary,
            "label": self.label,
        }


def _people_score(people_affected: int) -> float:
    if people_affected <= 0:
        return 0.0
    return min(
        100.0,
        100.0
        * math.log1p(people_affected)
        / math.log1p(PEOPLE_NORMALIZATION_CAP),
    )


def _visual_signal(
    *,
    visual_category: str | None,
    visual_confidence: float | None,
    visual_detections: Iterable[dict],
    confidence_threshold: float,
) -> tuple[float | None, list[str], list[str]]:
    scores: list[float] = []
    evidence: list[str] = []
    uncertainty: list[str] = []

    category_score = VISUAL_CATEGORY_SCORES.get(visual_category or "")
    if category_score is not None and visual_confidence is not None:
        if visual_confidence >= confidence_threshold:
            scores.append(category_score)
            evidence.append(
                f"Image classifier ranked {visual_category} "
                f"({visual_confidence:.2f} uncalibrated score)."
            )
            uncertainty.append(
                "Visual category is an uncalibrated model output, not verified damage."
            )
        else:
            uncertainty.append(
                "Image classification is below the review threshold and was excluded "
                "from the severity score."
            )
    elif visual_category == "Other / unclear":
        uncertainty.append("The image model did not identify a specific damage category.")

    for detection in visual_detections:
        label = str(detection.get("label", "")).strip().lower()
        confidence = detection.get("confidence")
        if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
            continue
        if not confidence_threshold <= confidence <= 1:
            continue
        matching_scores = [
            score for term, score in VISUAL_HAZARD_SCORES.items() if term in label
        ]
        if matching_scores:
            scores.append(max(matching_scores))
            evidence.append(
                f"Object detector reported {label} "
                f"({confidence:.2f} uncalibrated score)."
            )
            uncertainty.append(
                "Detected objects indicate visible scene content, not confirmed "
                "hazard extent or damage."
            )

    if not scores:
        uncertainty.append("No usable visual hazard indicators were available.")
        return None, evidence, uncertainty
    return max(scores), evidence, uncertainty


def estimate_severity(
    *,
    urgency: int,
    people_affected: int,
    access_blocked: bool,
    visual_category: str | None = None,
    visual_confidence: float | None = None,
    visual_detections: Iterable[dict] = (),
    confidence_threshold: float = 0.55,
) -> SeverityAssessment:
    visual_score, visual_evidence, uncertainty = _visual_signal(
        visual_category=visual_category,
        visual_confidence=visual_confidence,
        visual_detections=visual_detections,
        confidence_threshold=confidence_threshold,
    )
    available = [
        ("reported_urgency", float(urgency), URGENCY_WEIGHT, f"Reported urgency: {urgency}/100."),
        (
            "people_affected",
            _people_score(people_affected),
            PEOPLE_WEIGHT,
            f"Reported people affected: {people_affected}.",
        ),
        (
            "blocked_access",
            100.0 if access_blocked else 0.0,
            ACCESS_WEIGHT,
            "Access reported blocked." if access_blocked else "Access not reported blocked.",
        ),
    ]
    if visual_score is not None:
        available.append(
            ("visual_indicators", visual_score, VISUAL_WEIGHT, "Visual indicators were included.")
        )

    weight_total = sum(weight for _, _, weight, _ in available)
    factors = [
        SeverityFactor(
            factor=name,
            value=round(value, 2),
            weight=round(weight / weight_total, 4),
            contribution=round(value * weight / weight_total, 2),
            evidence=evidence,
        )
        for name, value, weight, evidence in available
    ]
    score = round(sum(factor.contribution for factor in factors), 1)
    if score >= 80:
        severity = "Critical"
    elif score >= 60:
        severity = "High"
    elif score >= 40:
        severity = "Moderate"
    else:
        severity = "Low"

    evidence = [factor.evidence for factor in factors]
    evidence.extend(visual_evidence)
    if people_affected == 0:
        uncertainty.append("A zero reported impact count may mean unknown or underreported.")
    uncertainty.extend(
        [
            "Urgency, impact counts, and access conditions are self-reported.",
            "This score is not a structural-safety or professional damage assessment.",
        ]
    )
    return SeverityAssessment(
        severity=severity,
        score=score,
        factors=factors,
        evidence=list(dict.fromkeys(evidence)),
        uncertainty=list(dict.fromkeys(uncertainty)),
    )

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.auth_dependencies import AdminUser, CurrentUser, ResponderOrAdmin
from app.core.config import get_settings
from app.models import Incident, IncidentImage, User
from app.schemas.incident import (
    IncidentAnalyticsFilters,
    IncidentAnalyticsResponse,
    DemoSeedResponse,
    IncidentHistoryResponse,
    IncidentMapResponse,
    IncidentPage,
    IncidentRead,
    IncidentStatsResponse,
    IncidentUpdate,
    MapLocationFilters,
    Severity,
    VerificationStatus,
)
from app.services.demo import seed_demo as create_demo_data
from app.services.incidents import (
    IncidentNotFoundError,
    create_incident as create_incident_record,
    delete_incident as delete_incident_record,
    get_analytics,
    get_incident as get_incident_record,
    get_incident_history,
    get_map_points,
    get_stats,
    iter_analytics_export_records,
    list_incidents as list_incident_records,
    update_incident as update_incident_record,
)
from app.services.uploads import UploadValidationError, read_upload, save_upload
from app.services.auth import enforce_rate_limit
from app.services.auth import RateLimitExceeded

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["incidents"])
private_upload_router = APIRouter(tags=["Incident evidence"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post("/incidents", response_model=IncidentRead, status_code=201)
def create_incident(
    session: DatabaseSession,
    actor: CurrentUser,
    description: Annotated[str, Form(min_length=1, max_length=4000)],
    latitude: Annotated[float, Form(ge=-90, le=90, allow_inf_nan=False)],
    longitude: Annotated[float, Form(ge=-180, le=180, allow_inf_nan=False)],
    image: Annotated[UploadFile, File(description="JPEG, PNG or WEBP; maximum 10 MiB")],
    urgency: Annotated[int, Form(ge=0, le=100)] = 50,
    people_affected: Annotated[int, Form(ge=0)] = 0,
    access_blocked: Annotated[bool, Form()] = False,
    reporter: Annotated[str, Form(max_length=200)] = "Anonymous",
) -> IncidentRead:
    try:
        enforce_rate_limit(
            session,
            action="upload",
            client_address=f"user:{actor.id}",
            limit=get_settings().auth_upload_limit,
        )
    except RateLimitExceeded as error:
        raise HTTPException(
            status_code=429,
            detail=str(error),
            headers={"Retry-After": str(error.retry_after)},
        ) from error
    if not description.strip():
        raise HTTPException(status_code=422, detail="Description cannot be blank")
    try:
        upload = save_upload(image.file, image.content_type, image.filename)
    except UploadValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        incident = create_incident_record(
            session,
            description=description,
            latitude=latitude,
            longitude=longitude,
            urgency=urgency,
            people_affected=people_affected,
            access_blocked=access_blocked,
            reporter=actor.display_name,
            upload=upload,
            reporting_user_id=actor.id,
        )
    except Exception:
        logger.exception("Could not create incident")
        raise
    return IncidentRead.model_validate(incident)


@router.get("/incidents", response_model=IncidentPage)
def list_incidents(
    session: DatabaseSession,
    actor: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: VerificationStatus | None = None,
    verification_status: VerificationStatus | None = None,
    incident_status: Literal["Reported", "Assigned", "In progress", "Resolved"] | None = None,
    severity: Severity | None = None,
    damage_type: Annotated[str | None, Query(max_length=100)] = None,
    sort_by: Literal["priority_score", "created_at", "severity_score"] = "priority_score",
    sort_order: Literal["asc", "desc"] = "desc",
) -> IncidentPage:
    if status and verification_status and status != verification_status:
        raise HTTPException(
            status_code=422,
            detail="Use either status or verification_status, not conflicting values",
        )
    return list_incident_records(
        session,
        page=page,
        page_size=page_size,
        q=q,
        verification_status=verification_status or status,
        incident_status=incident_status,
        severity=severity,
        damage_type=damage_type,
        sort_by=sort_by,
        sort_order=sort_order,
        reporting_user_id=actor.id if actor.role == "citizen" else None,
    )


@router.get("/incidents/map", response_model=IncidentMapResponse)
def list_map_points(
    session: DatabaseSession,
    _: ResponderOrAdmin,
    limit: Annotated[int, Query(ge=1, le=5000)] = 2000,
    offset: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
    severity: Severity | None = None,
    damage_type: Annotated[str | None, Query(max_length=100)] = None,
    latitude: Annotated[float | None, Query(ge=-90, le=90, allow_inf_nan=False)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180, allow_inf_nan=False)] = None,
    radius_m: Annotated[float | None, Query(gt=0, le=500_000, allow_inf_nan=False)] = None,
) -> IncidentMapResponse:
    try:
        location = MapLocationFilters(
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return get_map_points(
        session,
        limit=limit,
        offset=offset,
        severity=severity,
        damage_type=damage_type,
        latitude=location.latitude,
        longitude=location.longitude,
        radius_m=location.radius_m,
    )


def _serve_incident_image(
    incident: Incident, actor: User, image: IncidentImage
) -> Response:
    if actor.role == "citizen" and incident.reporting_user_id != actor.id:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        content = read_upload(image.storage_path)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Incident image is unavailable"
        ) from error
    if not content:
        raise HTTPException(status_code=404, detail="Incident image is unavailable")
    return Response(
        content,
        media_type=image.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.get(
    "/incidents/{incident_id}/image",
    response_class=Response,
    summary="Retrieve incident evidence for an authorized user",
)
def incident_image(
    incident_id: str,
    session: DatabaseSession,
    actor: CurrentUser,
) -> Response:
    try:
        incident = get_incident_record(session, incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    image = next((item for item in incident.images if item.is_primary), None)
    image = image or (incident.images[0] if incident.images else None)
    if image is None:
        raise HTTPException(status_code=404, detail="Incident image not found")
    return _serve_incident_image(incident, actor, image)


@private_upload_router.get(
    "/uploads/{storage_path}",
    response_class=Response,
    include_in_schema=False,
)
def legacy_incident_image(
    storage_path: str,
    session: DatabaseSession,
    actor: CurrentUser,
) -> Response:
    image = session.scalar(
        select(IncidentImage).where(IncidentImage.storage_path == storage_path)
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Incident image not found")
    try:
        incident = get_incident_record(session, image.incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _serve_incident_image(incident, actor, image)


@router.get("/incidents/{incident_id}/history", response_model=IncidentHistoryResponse)
def incident_history(
    incident_id: str, session: DatabaseSession, actor: ResponderOrAdmin
) -> dict:
    try:
        return get_incident_history(session, incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/incidents/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: str, session: DatabaseSession, actor: CurrentUser
) -> IncidentRead:
    try:
        incident = get_incident_record(session, incident_id)
        if actor.role == "citizen" and incident.reporting_user_id != actor.id:
            raise HTTPException(status_code=404, detail="Incident not found")
        return IncidentRead.model_validate(incident)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/incidents/{incident_id}", response_model=IncidentRead)
def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    session: DatabaseSession,
    actor: CurrentUser,
) -> IncidentRead:
    fields = payload.model_fields_set
    if actor.role == "citizen":
        raise HTTPException(status_code=403, detail="Citizens cannot modify incident workflows")
    if actor.role == "responder":
        if fields - {"incident_status"}:
            raise HTTPException(
                status_code=403,
                detail="Responders may only update response status",
            )
        try:
            incident = get_incident_record(session, incident_id)
        except IncidentNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if incident.assigned_to not in {actor.display_name, actor.email}:
            raise HTTPException(
                status_code=403,
                detail="Responders may update only incidents assigned to them",
            )
    try:
        incident = update_incident_record(
            session, incident_id, payload, actor_user_id=actor.id
        )
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return IncidentRead.model_validate(incident)


@router.delete("/incidents/{incident_id}", status_code=204, response_class=Response)
def delete_incident(
    incident_id: str, session: DatabaseSession, actor: AdminUser
) -> Response:
    try:
        delete_incident_record(
            session, incident_id, actor_user_id=actor.id
        )
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)


@router.get("/stats", response_model=IncidentStatsResponse)
def stats(session: DatabaseSession, _: ResponderOrAdmin) -> IncidentStatsResponse:
    return IncidentStatsResponse(**get_stats(session))


@router.get("/analytics", response_model=IncidentAnalyticsResponse)
def analytics(
    session: DatabaseSession,
    _: ResponderOrAdmin,
    filters: Annotated[IncidentAnalyticsFilters, Query()],
) -> IncidentAnalyticsResponse:
    return get_analytics(session, filters)


def _csv_safe_text(value: str | None) -> str:
    if value is None:
        return ""
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _analytics_csv(
    session: Session, filters: IncidentAnalyticsFilters
) -> Iterator[str]:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "incident_id",
            "reported_at",
            "latitude",
            "longitude",
            "category",
            "severity",
            "priority_score",
            "urgency",
            "people_affected",
            "access_blocked",
            "verification_status",
            "response_status",
            "assigned_to",
            "description",
        )
    )
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    for incident in iter_analytics_export_records(session, filters):
        writer.writerow(
            (
                _csv_safe_text(incident.id),
                incident.created_at.isoformat(),
                incident.latitude,
                incident.longitude,
                _csv_safe_text(incident.damage_type),
                _csv_safe_text(incident.severity),
                incident.priority_score,
                incident.urgency,
                incident.people_affected,
                incident.access_blocked,
                _csv_safe_text(incident.verification_status),
                _csv_safe_text(incident.incident_status),
                _csv_safe_text(incident.assigned_to),
                _csv_safe_text(incident.description),
            )
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)


@router.get(
    "/analytics/export.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
    summary="Export filtered incident records as CSV",
)
def export_analytics_csv(
    session: DatabaseSession,
    _: ResponderOrAdmin,
    filters: Annotated[IncidentAnalyticsFilters, Query()],
) -> StreamingResponse:
    return StreamingResponse(
        _analytics_csv(session, filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="damage-mapper-incidents.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/demo-seed", response_model=DemoSeedResponse)
def seed_demo(session: DatabaseSession, actor: AdminUser) -> DemoSeedResponse:
    if not get_settings().demo_enabled:
        raise HTTPException(status_code=404, detail="Demo data is disabled")
    return DemoSeedResponse(**create_demo_data(session, actor_user_id=actor.id))


@router.get("/demo-seed", response_model=DemoSeedResponse, deprecated=True)
def seed_demo_legacy(session: DatabaseSession, actor: AdminUser) -> DemoSeedResponse:
    if not get_settings().demo_enabled:
        raise HTTPException(status_code=404, detail="Demo data is disabled")
    return DemoSeedResponse(**create_demo_data(session, actor_user_id=actor.id))

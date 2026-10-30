from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.auth_dependencies import AdminUser, ResponderOrAdmin
from app.models import ResponderLocation
from app.schemas.incident import (
    ResponderLocationList,
    ResponderLocationRead,
    ResponderLocationUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/api/responders", tags=["Geospatial"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/locations",
    response_model=ResponderLocationList,
    summary="Read explicitly recorded responder locations",
)
def list_responder_locations(
    session: DatabaseSession, _: AdminUser
) -> ResponderLocationList:
    locations = session.scalars(
        select(ResponderLocation).order_by(ResponderLocation.responder_name)
    ).all()
    items = [ResponderLocationRead.model_validate(item) for item in locations]
    return ResponderLocationList(items=items, total=len(items))


@router.put(
    "/locations",
    response_model=ResponderLocationRead,
    summary="Set a responder's latest GPS location",
)
def update_responder_location(
    payload: ResponderLocationUpdate,
    session: DatabaseSession,
    actor: ResponderOrAdmin,
) -> ResponderLocationRead:
    if actor.role == "responder" and payload.responder_name != actor.display_name:
        raise HTTPException(
            status_code=403,
            detail="Responders may update only their own location",
        )
    location = session.get(ResponderLocation, payload.responder_name)
    if location is None:
        location = ResponderLocation(responder_name=payload.responder_name)
        session.add(location)
    location.latitude = payload.latitude
    location.longitude = payload.longitude
    location.updated_at = datetime.now(timezone.utc)
    record_audit(
        session,
        "responder_location_updated",
        {"responder_name": payload.responder_name},
        actor_user_id=actor.id,
    )
    session.commit()
    session.refresh(location)
    return ResponderLocationRead.model_validate(location)

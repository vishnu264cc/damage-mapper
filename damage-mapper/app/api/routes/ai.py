from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.auth_dependencies import CurrentUser
from app.api.dependencies import get_db
from app.core.config import get_settings
from app.schemas.ai import AIAnalysisRead
from app.services.auth import RateLimitExceeded, enforce_rate_limit
from app.services.ai import analyze_image
from app.services.ai.preprocessing import ImageValidationError
from app.services.uploads import (
    UploadValidationError,
    materialize_upload,
    remove_upload,
    save_upload,
)

router = APIRouter(prefix="/api/ai", tags=["AI assessment"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/analyze",
    response_model=AIAnalysisRead,
    summary="Classify a disaster image and optionally detect visible objects",
)
def analyze_uploaded_image(
    actor: CurrentUser,
    session: DatabaseSession,
    image: Annotated[UploadFile, File(description="JPEG, PNG or WEBP; maximum 10 MiB")],
) -> AIAnalysisRead:
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
    try:
        upload = save_upload(image.file, image.content_type, image.filename)
    except UploadValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        with materialize_upload(upload.storage_path) as image_path:
            result = analyze_image(image_path)
    except ImageValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        remove_upload(upload.storage_path)
    return AIAnalysisRead(**result.as_dict())

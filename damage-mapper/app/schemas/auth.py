from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Role = Literal["citizen", "responder", "admin"]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    role: Role
    is_active: bool


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("A valid email address is required")
        return normalized


class CitizenRegistration(Credentials):
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name cannot be blank")
        return normalized


class AdminBootstrap(CitizenRegistration):
    pass


class AdminUserCreate(CitizenRegistration):
    role: Literal["responder", "admin"]


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_update(self) -> AdminUserUpdate:
        if not any(
            getattr(self, field) is not None
            for field in ("role", "password", "is_active")
        ):
            raise ValueError("At least one account field must be provided")
        return self


class AuthTokenRead(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserRead

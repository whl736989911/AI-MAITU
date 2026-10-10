from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChallengeRequest(StrictModel):
    instance_id: str = Field(min_length=1, max_length=128)
    operation: Literal["activate", "sync"]
    public_key: str = Field(min_length=40, max_length=4096)


class ChallengeReply(StrictModel):
    challenge: str
    expires_at: int


class ProofRequest(StrictModel):
    proof: str = Field(min_length=32, max_length=16384)


class TokenReply(StrictModel):
    token: str


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    totp: str = Field(min_length=6, max_length=8)


class ReauthRequest(StrictModel):
    totp: str = Field(min_length=6, max_length=8)


class SessionUser(StrictModel):
    id: int
    username: str
    role: Literal["owner", "operator", "reader"]


class SessionReply(StrictModel):
    user: SessionUser
    csrf_token: str
    timezone: str


class CustomerCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    contact: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=4000)


class CustomerUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    contact: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=4000)
    active: bool
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=1000)


class CustomerView(StrictModel):
    id: str
    name: str
    contact: str
    notes: str
    active: bool
    version: int
    created_at: int


class ActivationCreate(StrictModel):
    customer_id: str
    expires_at: int = Field(gt=0)
    ttl_seconds: int = Field(default=86400, ge=60, le=2592000)


class ActivationView(StrictModel):
    id: str
    customer_id: str
    code: str | None = None
    expires_at: int
    license_expires_at: int | None = None
    used_at: int | None
    revoked_at: int | None


class LicenseAction(StrictModel):
    expires_at: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=1000)
    version: int = Field(ge=1)


class ReplaceRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    version: int = Field(ge=1)


class LicenseView(StrictModel):
    id: str
    instance_id: str
    customer_id: str
    expires_at: int
    revoked: bool
    expired: bool
    revision: int
    created_at: int


class InstanceView(StrictModel):
    id: str
    customer_id: str
    public_key_fingerprint: str
    status: str
    last_seen_at: int | None
    license_id: str
    license_expires_at: int
    license_status: str
    license_revision: int


class AuditView(StrictModel):
    id: int
    actor: str
    action: str
    object_type: str
    object_id: str
    before_json: str
    after_json: str
    reason: str
    created_at: int


class AdminCreate(StrictModel):
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["owner", "operator", "reader"]


class AdminEnrollment(StrictModel):
    id: int
    username: str
    role: Literal["owner", "operator", "reader"]
    totp_secret: str
    provisioning_uri: str
    active: bool


class AdminUserView(StrictModel):
    id: int
    username: str
    role: Literal["owner", "operator", "reader"]
    active: bool
    created_at: int


class AdminUserUpdate(StrictModel):
    role: Literal["owner", "operator", "reader"]
    active: bool
    totp: str | None = Field(default=None, min_length=6, max_length=8)


class VerifyEnrollment(StrictModel):
    totp: str = Field(min_length=6, max_length=8)


class RecentContact(StrictModel):
    id: str
    last_seen_at: int


class OverviewView(StrictModel):
    customers: int
    active_customers: int
    instances: int
    active_licenses: int
    expired_licenses: int
    revoked_licenses: int
    expiring_soon: int
    recent_contacts: list[RecentContact]
    timezone: str


class SigningKeyView(StrictModel):
    kid: str
    public_pem: str
    state: Literal["staged", "active", "retired"]
    created_at: int


class SigningKeyStageView(StrictModel):
    kid: str
    public_pem: str
    state: Literal["staged"]


class SigningKeyActivationView(StrictModel):
    kid: str
    state: Literal["active"]


class SystemView(StrictModel):
    service_status: Literal["running"]
    timezone: str
    tls_certificate_configured: bool
    tls_certificate_expires_at: int | None
    tls_certificate_readable: bool
    active_signing_kid: str | None
    staged_signing_kids: list[str]


class ActivationRevoke(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)


class HealthView(StrictModel):
    status: Literal["ok"]

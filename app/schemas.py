from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SocketStatus(BaseModel):
    id: int
    on: bool
    power_w: float = 0.0
    device: str = "Unknown"


class DeviceOut(BaseModel):
    id: str
    name: str
    room: str
    online: bool
    lastSeen: str


class StripStatusOut(BaseModel):
    ts: int
    online: bool
    total_power_w: float
    voltage_v: float
    current_a: float
    sockets: list[SocketStatus]


class TelemetryPointOut(BaseModel):
    ts: int
    power_w: float


class CmdRequest(BaseModel):
    socket: int | None = None
    action: str
    mode: str | None = None
    duration: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CmdSubmitOut(BaseModel):
    ok: bool
    cmdId: str
    stripId: str
    acceptedAt: int


class CmdStateOut(BaseModel):
    cmdId: str
    state: Literal["pending", "success", "failed", "timeout", "cancelled"]
    updatedAt: int
    message: str = ""
    durationMs: int | None = None


class AIReportOut(BaseModel):
    room_id: str
    period: str
    summary: str
    anomalies: list[str]
    suggestions: list[str]


class AuthLoginRequest(BaseModel):
    account: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=6, max_length=128)


class AuthUserOut(BaseModel):
    username: str
    email: str
    role: Literal["admin"]


class AuthLoginOut(BaseModel):
    ok: bool
    token: str
    user: AuthUserOut

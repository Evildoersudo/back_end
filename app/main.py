from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .config import settings
from .db import Base, engine, get_session
from .models import Device, StripStatus
from .mqtt_bridge import mqtt_bridge
from .schemas import AIReportOut, CmdRequest, CmdStateOut, CmdSubmitOut, DeviceOut, StripStatusOut
from .services import (
    ai_report,
    build_telemetry_series,
    create_cmd_record,
    ensure_default_admin,
    ensure_seed_data,
    get_cmd_state,
    has_pending_conflict,
    login_user,
    mark_timeouts,
    refresh_online_state,
    update_cmd_state,
    utc_iso,
)
from .schemas import (
    AuthLoginOut,
    AuthLoginRequest,
    AuthUserOut,
)
from .ws import ws_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dorm-backend")


def error_response(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "code": code,
            "message": message,
            "details": details or {},
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with get_session() as session:
        ensure_seed_data(session)
        ensure_default_admin(session)
        mark_timeouts(session)

    mqtt_bridge.set_loop(asyncio.get_running_loop())
    mqtt_bridge.start()
    try:
        yield
    finally:
        mqtt_bridge.stop()


app = FastAPI(title="Dorm Power Backend", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    return error_response(400, "BAD_REQUEST", "request validation failed", {"errors": exc.errors()})


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mqtt_enabled": mqtt_bridge.enabled,
        "mqtt_connected": mqtt_bridge.connected,
        "database_url": settings.database_url,
    }


@app.post("/api/auth/login", response_model=AuthLoginOut)
def auth_login(req: AuthLoginRequest) -> Any:
    with get_session() as session:
        user = login_user(session, req.account, req.password)
        if user is None:
            return error_response(401, "UNAUTHORIZED", "invalid account or password")
        token = secrets.token_urlsafe(24)
        return AuthLoginOut(
            ok=True,
            token=token,
            user=AuthUserOut(username=user.username, email=user.email, role="admin"),
        )


@app.get("/api/devices", response_model=list[DeviceOut])
def get_devices() -> list[DeviceOut]:
    with get_session() as session:
        items = session.scalars(select(Device).order_by(Device.id.asc())).all()
        output: list[DeviceOut] = []
        for d in items:
            refresh_online_state(session, d)
            reason = mqtt_bridge.get_offline_reason(d.id) if not d.online else None
            output.append(
                DeviceOut(
                    id=d.id,
                    name=d.name,
                    room=d.room,
                    online=d.online,
                    lastSeen=utc_iso(d.last_seen_ts),
                    offlineReason=reason,
                )
            )
        return output


@app.get("/api/devices/{device_id}/status", response_model=StripStatusOut)
def get_device_status(device_id: str) -> Any:
    with get_session() as session:
        d = session.get(Device, device_id)
        s = session.get(StripStatus, device_id)
        if d is None or s is None:
            return error_response(404, "NOT_FOUND", "device not found")

        refresh_online_state(session, d)
        try:
            sockets = json.loads(s.sockets_json)
        except Exception:
            sockets = []
        return StripStatusOut(
            ts=s.ts,
            online=d.online and s.online,
            total_power_w=s.total_power_w,
            voltage_v=s.voltage_v,
            current_a=s.current_a,
            sockets=sockets,
        )


@app.get("/api/telemetry")
def get_telemetry(
    device: str = Query(..., min_length=1),
    range: str = Query(..., pattern="^(60s|24h|7d|30d)$"),
) -> Any:
    with get_session() as session:
        if session.get(Device, device) is None:
            return error_response(404, "NOT_FOUND", "device not found")
        try:
            return build_telemetry_series(session, device, range)
        except ValueError:
            return error_response(400, "BAD_REQUEST", "range is invalid")


@app.post("/api/strips/{device_id}/cmd", response_model=CmdSubmitOut)
async def post_cmd(device_id: str, req: CmdRequest) -> Any:
    with get_session() as session:
        if session.get(Device, device_id) is None:
            return error_response(404, "NOT_FOUND", "device not found")
        if has_pending_conflict(session, device_id, req.socket):
            return error_response(409, "CMD_CONFLICT", "pending command exists for target")
        cmd = create_cmd_record(session, device_id, req)

    cmd_payload = {
        "cmdId": cmd.cmd_id,
        "ts": int(time.time()),
        "type": req.action.upper(),
        "socketId": req.socket,
        "payload": req.payload,
        "mode": req.mode,
        "duration": req.duration,
        "source": "web",
    }
    published = mqtt_bridge.publish_cmd(device_id, cmd_payload)
    if not published:
        with get_session() as session:
            update_cmd_state(session, cmd.cmd_id, "failed", message="mqtt unavailable")
        await ws_manager.broadcast(
            {
                "type": "CMD_ACK",
                "cmdId": cmd.cmd_id,
                "state": "failed",
                "ts": int(time.time()),
                "updatedAt": int(time.time()),
                "message": "mqtt unavailable",
            }
        )

    return CmdSubmitOut(ok=True, cmdId=cmd.cmd_id, stripId=device_id, acceptedAt=int(time.time()))


@app.get("/api/cmd/{cmd_id}", response_model=CmdStateOut)
def get_cmd(cmd_id: str) -> Any:
    with get_session() as session:
        state = get_cmd_state(session, cmd_id)
        if state is None:
            return error_response(404, "NOT_FOUND", "cmd not found")
        return state


@app.get("/api/rooms/{room_id}/ai_report", response_model=AIReportOut)
def get_ai_report(room_id: str, period: str = Query("7d")) -> Any:
    if period not in {"7d", "30d"}:
        return error_response(400, "BAD_REQUEST", "period is invalid")
    with get_session() as session:
        exists = session.scalar(select(Device.id).where(Device.room == room_id).limit(1))
        if exists is None:
            return error_response(404, "NOT_FOUND", "room not found")
        result = ai_report(session, room_id, period)
        return AIReportOut(**result)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)

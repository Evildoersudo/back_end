from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import CommandRecord, Device, StripStatus, Telemetry
from .schemas import CmdRequest, CmdStateOut, SocketStatus

RANGE_CONFIG = {
    "60s": {"points": 60, "step": 1},
    "24h": {"points": 96, "step": 15 * 60},
    "7d": {"points": 168, "step": 60 * 60},
    "30d": {"points": 120, "step": 6 * 60 * 60},
}


def utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_seed_data(session: Session) -> None:
    existing = session.scalar(select(Device).limit(1))
    if existing:
        return

    now = int(time.time())
    device = Device(
        id="strip01",
        name="Dorm302-Strip01",
        room="A-302",
        online=True,
        last_seen_ts=now,
    )
    status = StripStatus(
        device_id="strip01",
        ts=now,
        online=True,
        total_power_w=0.0,
        voltage_v=220.0,
        current_a=0.0,
        sockets_json=json.dumps(
            [
                {"id": 1, "on": False, "power_w": 0.0, "device": "None"},
                {"id": 2, "on": False, "power_w": 0.0, "device": "None"},
                {"id": 3, "on": False, "power_w": 0.0, "device": "None"},
                {"id": 4, "on": False, "power_w": 0.0, "device": "None"},
            ],
            ensure_ascii=False,
        ),
    )
    session.add(device)
    session.add(status)


def parse_room(device_id: str) -> str:
    if device_id.startswith("A-") and "-" in device_id:
        parts = device_id.split("-")
        if len(parts) >= 2:
            return f"{parts[0]}-{parts[1]}"
    return "A-302"


def upsert_device(session: Session, device_id: str, last_seen_ts: int | None = None) -> Device:
    dev = session.get(Device, device_id)
    now = int(time.time())
    seen = last_seen_ts or now
    if dev is None:
        dev = Device(
            id=device_id,
            name=f"DormDevice-{device_id}",
            room=parse_room(device_id),
            online=True,
            last_seen_ts=seen,
        )
        session.add(dev)
    else:
        dev.last_seen_ts = max(dev.last_seen_ts, seen)
        dev.online = now - dev.last_seen_ts <= settings.online_timeout_seconds
    return dev


def refresh_online_state(session: Session, device: Device) -> None:
    _ = session
    now = int(time.time())
    device.online = now - device.last_seen_ts <= settings.online_timeout_seconds


def update_status_from_payload(session: Session, device_id: str, payload: dict[str, Any]) -> None:
    ts = int(payload.get("ts") or time.time())
    device = upsert_device(session, device_id, ts)
    refresh_online_state(session, device)

    sockets = payload.get("sockets", [])
    if not isinstance(sockets, list):
        sockets = []
    valid_sockets: list[dict[str, Any]] = []
    for item in sockets:
        if not isinstance(item, dict):
            continue
        if "id" not in item:
            continue
        try:
            socket = SocketStatus(**item)
        except Exception:
            continue
        valid_sockets.append(socket.model_dump())

    status = session.get(StripStatus, device_id)
    if status is None:
        status = StripStatus(device_id=device_id, ts=ts, online=device.online)
        session.add(status)

    status.ts = ts
    status.online = bool(payload.get("online", device.online))
    status.total_power_w = float(payload.get("total_power_w", 0.0))
    status.voltage_v = float(payload.get("voltage_v", 220.0))
    status.current_a = float(payload.get("current_a", 0.0))
    status.sockets_json = json.dumps(valid_sockets, ensure_ascii=False)


def save_telemetry_point(session: Session, device_id: str, payload: dict[str, Any]) -> None:
    ts = int(payload.get("ts") or time.time())
    upsert_device(session, device_id, ts)
    point = Telemetry(
        device_id=device_id,
        ts=ts,
        power_w=float(payload.get("power_w", payload.get("total_power_w", 0.0))),
        voltage_v=float(payload.get("voltage_v", 220.0)),
        current_a=float(payload.get("current_a", 0.0)),
    )
    session.add(point)


def create_cmd_record(session: Session, device_id: str, req: CmdRequest) -> CommandRecord:
    now = int(time.time())
    cmd_id = f"cmd_{now}_{uuid.uuid4().hex[:8]}"
    payload = {
        "socket": req.socket,
        "action": req.action,
        "mode": req.mode,
        "duration": req.duration,
        "payload": req.payload,
    }
    cmd = CommandRecord(
        cmd_id=cmd_id,
        device_id=device_id,
        socket=req.socket,
        action=req.action,
        payload_json=json.dumps(payload, ensure_ascii=False),
        state="pending",
        message="",
        created_at=now,
        updated_at=now,
        expires_at=now + settings.cmd_timeout_seconds,
    )
    session.add(cmd)
    return cmd


def has_pending_conflict(session: Session, device_id: str, socket: int | None) -> bool:
    now = int(time.time())
    mark_timeouts(session)
    if socket is None:
        q = select(CommandRecord).where(
            and_(
                CommandRecord.device_id == device_id,
                CommandRecord.state == "pending",
                CommandRecord.expires_at >= now,
            )
        )
    else:
        q = select(CommandRecord).where(
            and_(
                CommandRecord.device_id == device_id,
                CommandRecord.socket == socket,
                CommandRecord.state == "pending",
                CommandRecord.expires_at >= now,
            )
        )
    return session.scalar(q.limit(1)) is not None


def update_cmd_state(
    session: Session,
    cmd_id: str,
    state: str,
    message: str = "",
    duration_ms: int | None = None,
) -> CommandRecord | None:
    cmd = session.get(CommandRecord, cmd_id)
    if cmd is None:
        return None
    cmd.state = state
    cmd.message = message
    cmd.updated_at = int(time.time())
    if duration_ms is not None:
        cmd.duration_ms = duration_ms
    return cmd


def mark_timeouts(session: Session) -> None:
    now = int(time.time())
    q = select(CommandRecord).where(
        and_(CommandRecord.state == "pending", CommandRecord.expires_at < now)
    )
    for cmd in session.scalars(q).all():
        cmd.state = "timeout"
        cmd.message = "ack timeout"
        cmd.updated_at = now


def get_cmd_state(session: Session, cmd_id: str) -> CmdStateOut | None:
    mark_timeouts(session)
    cmd = session.get(CommandRecord, cmd_id)
    if cmd is None:
        return None
    return CmdStateOut(
        cmdId=cmd.cmd_id,
        state=cmd.state,  # type: ignore[arg-type]
        updatedAt=cmd.updated_at,
        message=cmd.message,
        durationMs=cmd.duration_ms,
    )


def build_telemetry_series(
    session: Session,
    device_id: str,
    range_key: str,
) -> list[dict[str, float | int]]:
    cfg = RANGE_CONFIG.get(range_key)
    if cfg is None:
        raise ValueError("range is invalid")

    points: int = cfg["points"]
    step: int = cfg["step"]
    now_ts = int(time.time())
    start_ts = now_ts - (points - 1) * step

    rows = session.scalars(
        select(Telemetry)
        .where(
            and_(
                Telemetry.device_id == device_id,
                Telemetry.ts >= start_ts,
                Telemetry.ts <= now_ts,
            )
        )
        .order_by(Telemetry.ts.asc())
    ).all()

    latest_status = session.get(StripStatus, device_id)
    carry = latest_status.total_power_w if latest_status else 0.0

    result: list[dict[str, float | int]] = []
    idx = 0
    for i in range(points):
        slot_ts = start_ts + i * step
        slot_end = slot_ts + step
        while idx < len(rows) and rows[idx].ts <= slot_end:
            carry = rows[idx].power_w
            idx += 1
        result.append({"ts": slot_ts, "power_w": round(float(carry), 3)})
    return result


def ai_report(session: Session, room_id: str, period: str) -> dict[str, Any]:
    devices = session.scalars(select(Device).where(Device.room == room_id)).all()
    device_ids = [d.id for d in devices]
    if not device_ids:
        return {
            "room_id": room_id,
            "period": period,
            "summary": "No device data in this room yet.",
            "anomalies": ["No analyzable sample found."],
            "suggestions": ["Ensure devices upload status and telemetry periodically."],
        }

    days = 7 if period == "7d" else 30
    start_ts = int(time.time()) - days * 24 * 3600
    data = session.scalars(
        select(Telemetry)
        .where(and_(Telemetry.device_id.in_(device_ids), Telemetry.ts >= start_ts))
        .order_by(Telemetry.ts.asc())
    ).all()
    if not data:
        return {
            "room_id": room_id,
            "period": period,
            "summary": "Devices are online but telemetry coverage is insufficient.",
            "anomalies": ["Not enough telemetry points in selected period."],
            "suggestions": ["Increase telemetry frequency to every 1-5 seconds."],
        }

    avg_power = sum(d.power_w for d in data) / max(len(data), 1)
    peak = max(d.power_w for d in data)
    return {
        "room_id": room_id,
        "period": period,
        "summary": f"Average power is about {avg_power:.1f}W, peak is about {peak:.1f}W.",
        "anomalies": [f"Peak power reached {peak:.1f}W. Check high-load periods."],
        "suggestions": [
            "Enable auto off for low-priority sockets after 00:30.",
            "Set alerts for periods above baseline by 20%.",
        ],
    }

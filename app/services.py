from __future__ import annotations

import json
import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import CommandRecord, Device, StripStatus, Telemetry, UserAccount
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


def _hash_secret(secret: str, salt: str, iterations: int = 160_000) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt.encode("utf-8"), iterations)
    return digest.hex()


def hash_password(password: str) -> str:
    iterations = 160_000
    salt = secrets.token_hex(16)
    digest = _hash_secret(password, salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations_str, salt, digest = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        calc = _hash_secret(password, salt, int(iterations_str))
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def find_user_by_account(session: Session, account: str) -> UserAccount | None:
    normalized = account.strip()
    if not normalized:
        return None
    return session.scalar(
        select(UserAccount).where(
            or_(
                UserAccount.username == normalized,
                UserAccount.email == normalized.lower(),
            )
        ).limit(1)
    )


def register_user(session: Session, username: str, email: str, password: str) -> UserAccount:
    now = int(time.time())
    normalized_username = username.strip()
    normalized_email = email.strip().lower()
    if session.get(UserAccount, normalized_username) is not None:
        raise ValueError("username already exists")
    if session.scalar(select(UserAccount.username).where(UserAccount.email == normalized_email).limit(1)):
        raise ValueError("email already exists")
    user = UserAccount(
        username=normalized_username,
        email=normalized_email,
        password_hash=hash_password(password),
        role="admin",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    return user


def login_user(session: Session, account: str, password: str) -> UserAccount | None:
    normalized = account.strip()
    admin_username = settings.admin_username.strip() or "admin"
    admin_email = settings.admin_email.strip().lower() or "admin@dorm.local"
    if normalized not in {admin_username, admin_email}:
        return None
    user = session.get(UserAccount, admin_username)
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.updated_at = int(time.time())
    return user


def create_reset_code(session: Session, account: str, expires_in: int = 600) -> tuple[UserAccount | None, str]:
    user = find_user_by_account(session, account)
    if user is None:
        return None, ""
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time())
    user.reset_code_hash = hash_password(code)
    user.reset_expires_at = now + expires_in
    user.updated_at = now
    return user, code


def reset_password_with_code(session: Session, account: str, code: str, new_password: str) -> bool:
    user = find_user_by_account(session, account)
    if user is None:
        return False
    now = int(time.time())
    if user.reset_expires_at < now:
        return False
    if not user.reset_code_hash or not verify_password(code, user.reset_code_hash):
        return False
    user.password_hash = hash_password(new_password)
    user.reset_code_hash = ""
    user.reset_expires_at = 0
    user.updated_at = now
    return True


def ensure_default_admin(session: Session) -> None:
    now = int(time.time())
    username = settings.admin_username.strip() or "admin"
    email = settings.admin_email.strip().lower() or "admin@dorm.local"
    password = settings.admin_password

    user = session.get(UserAccount, username)
    if user is None:
        user = UserAccount(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        return

    changed = False
    if user.email != email:
        user.email = email
        changed = True
    if not verify_password(password, user.password_hash):
        user.password_hash = hash_password(password)
        changed = True
    if user.role != "admin":
        user.role = "admin"
        changed = True
    if changed:
        user.updated_at = now


def parse_room(device_id: str) -> str:
    room, _ = parse_device_meta(device_id)
    return room


ROOM_PATTERN = re.compile(r"^[A-Za-z]-?\d{2,4}$")
LEGACY_DEVICE_PATTERN = re.compile(r"^([A-Za-z]-?\d{2,4})[-_](.+)$")


def parse_device_meta(device_id: str) -> tuple[str, str]:
    normalized = " ".join(device_id.strip().split())
    if not normalized:
        return "A-302", "unknown"

    chunks = normalized.split(" ", 1)
    if len(chunks) == 2 and ROOM_PATTERN.match(chunks[0]):
        room, name = chunks[0], chunks[1].strip()
        return room, name or normalized

    legacy_match = LEGACY_DEVICE_PATTERN.match(normalized)
    if legacy_match:
        room = legacy_match.group(1).strip()
        name = legacy_match.group(2).strip()
        return room, name or normalized

    if ROOM_PATTERN.match(normalized):
        return normalized, normalized

    return "A-302", normalized


def upsert_device(session: Session, device_id: str, last_seen_ts: int | None = None) -> Device:
    room, display_name = parse_device_meta(device_id)
    dev = session.get(Device, device_id)
    if dev is None:
        for obj in session.new:
            if isinstance(obj, Device) and obj.id == device_id:
                dev = obj
                break
    now = int(time.time())
    seen = last_seen_ts or now
    if dev is None:
        dev = Device(
            id=device_id,
            name=display_name,
            room=room,
            online=True,
            last_seen_ts=seen,
        )
        session.add(dev)
    else:
        if dev.room == "A-302" and room != "A-302":
            dev.room = room
        if dev.name.startswith("DormDevice-") and display_name:
            dev.name = display_name
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


def mark_device_offline(session: Session, device_id: str, reason: str = "", ts: int | None = None) -> Device:
    now = ts or int(time.time())
    offline_seen = max(0, now - settings.online_timeout_seconds - 1)
    dev = session.get(Device, device_id)
    if dev is None:
        room, name = parse_device_meta(device_id)
        dev = Device(
            id=device_id,
            name=name,
            room=room,
            online=False,
            last_seen_ts=offline_seen,
        )
        session.add(dev)
    else:
        dev.last_seen_ts = min(dev.last_seen_ts, offline_seen)
        dev.online = False

    status = session.get(StripStatus, device_id)
    if status is None:
        status = StripStatus(
            device_id=device_id,
            ts=now,
            online=False,
            total_power_w=0.0,
            voltage_v=220.0,
            current_a=0.0,
            sockets_json="[]",
        )
        session.add(status)
    else:
        status.ts = now
        status.online = False
        status.total_power_w = 0.0
        status.current_a = 0.0
        try:
            sockets = json.loads(status.sockets_json)
        except Exception:
            sockets = []
        if isinstance(sockets, list):
            normalized: list[dict[str, Any]] = []
            for s in sockets:
                if not isinstance(s, dict):
                    continue
                item = dict(s)
                item["on"] = False
                item["power_w"] = 0.0
                normalized.append(item)
            status.sockets_json = json.dumps(normalized, ensure_ascii=False)

    _ = reason
    return dev


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


def apply_command_effect_to_status(session: Session, cmd: CommandRecord) -> None:
    if cmd.socket is None:
        return
    action = (cmd.action or "").strip().lower()
    if action not in {"on", "off"}:
        return

    status = session.get(StripStatus, cmd.device_id)
    if status is None:
        return

    try:
        sockets = json.loads(status.sockets_json)
    except Exception:
        sockets = []
    if not isinstance(sockets, list):
        sockets = []

    changed = False
    updated: list[dict[str, Any]] = []
    for item in sockets:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if sid == cmd.socket:
            next_item = dict(item)
            next_item["on"] = action == "on"
            if action == "off":
                next_item["power_w"] = 0.0
            changed = True
            updated.append(next_item)
        else:
            updated.append(item)

    if not changed:
        return

    status.sockets_json = json.dumps(updated, ensure_ascii=False)
    status.total_power_w = float(
        sum(float(x.get("power_w", 0.0)) for x in updated if isinstance(x, dict))
    )
    status.ts = int(time.time())


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

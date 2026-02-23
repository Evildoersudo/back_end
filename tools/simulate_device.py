from __future__ import annotations

import argparse
import math
import time
from typing import Any

from sqlalchemy import and_, select

from app.db import Base, engine, get_session
from app.models import CommandRecord
from app.services import ensure_seed_data, save_telemetry_point, update_cmd_state, update_status_from_payload


def make_status(ts: int, tick: int) -> dict[str, Any]:
    base = 130.0 + 45.0 * math.sin(tick / 9.0)
    p1 = max(base * 0.45, 5.0)
    p2 = max(base * 0.25 + 8.0 * math.sin(tick / 6.0), 0.0)
    p3 = max(base * 0.20 + 5.0 * math.cos(tick / 7.0), 0.0)
    p4 = max(base - p1 - p2 - p3, 0.0)
    total = p1 + p2 + p3 + p4
    current = total / 220.0
    return {
        "ts": ts,
        "online": True,
        "total_power_w": round(total, 2),
        "voltage_v": round(220.0 + 1.8 * math.sin(tick / 13.0), 2),
        "current_a": round(current, 3),
        "sockets": [
            {"id": 1, "on": True, "power_w": round(p1, 2), "device": "PC"},
            {"id": 2, "on": True, "power_w": round(p2, 2), "device": "Monitor"},
            {"id": 3, "on": True, "power_w": round(p3, 2), "device": "Fan"},
            {"id": 4, "on": True, "power_w": round(p4, 2), "device": "Router"},
        ],
    }


def auto_ack_pending(device_id: str, ack_delay_seconds: float) -> int:
    now = int(time.time())
    acked = 0
    with get_session() as session:
        pending = session.scalars(
            select(CommandRecord).where(
                and_(
                    CommandRecord.device_id == device_id,
                    CommandRecord.state == "pending",
                    CommandRecord.created_at <= int(now - ack_delay_seconds),
                )
            )
        ).all()
        for cmd in pending:
            duration = max((now - cmd.created_at) * 1000, 1)
            update_cmd_state(
                session,
                cmd_id=cmd.cmd_id,
                state="success",
                message="simulated ack",
                duration_ms=duration,
            )
            acked += 1
    return acked


def run(device_id: str, interval: float, duration: float, auto_ack: bool, ack_delay: float) -> None:
    Base.metadata.create_all(bind=engine)
    with get_session() as session:
        ensure_seed_data(session)

    start = time.time()
    tick = 0
    print(f"[sim] start device={device_id} interval={interval}s duration={duration}s auto_ack={auto_ack}")
    while True:
        now = int(time.time())
        status = make_status(now, tick)
        telemetry = {
            "ts": now,
            "power_w": status["total_power_w"],
            "voltage_v": status["voltage_v"],
            "current_a": status["current_a"],
        }
        with get_session() as session:
            update_status_from_payload(session, device_id, status)
            save_telemetry_point(session, device_id, telemetry)

        acked = auto_ack_pending(device_id, ack_delay) if auto_ack else 0
        print(
            f"[sim] ts={now} power={status['total_power_w']}W voltage={status['voltage_v']}V"
            + (f" acked={acked}" if auto_ack else "")
        )

        tick += 1
        if duration > 0 and (time.time() - start) >= duration:
            print("[sim] done")
            return
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate status/telemetry without ESP device.")
    parser.add_argument("--device-id", default="strip01", help="Target device id.")
    parser.add_argument("--interval", type=float, default=1.0, help="Write interval in seconds.")
    parser.add_argument("--duration", type=float, default=0.0, help="Run duration seconds (0 means forever).")
    parser.add_argument("--auto-ack", action="store_true", help="Auto mark pending commands as success.")
    parser.add_argument("--ack-delay", type=float, default=1.2, help="Seconds before auto ack.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        device_id=args.device_id,
        interval=max(args.interval, 0.2),
        duration=max(args.duration, 0.0),
        auto_ack=args.auto_ack,
        ack_delay=max(args.ack_delay, 0.2),
    )

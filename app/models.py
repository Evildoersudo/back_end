from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    room: Mapped[str] = mapped_column(String(64), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_ts: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class StripStatus(Base):
    __tablename__ = "strip_status"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_power_w: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    voltage_v: Mapped[float] = mapped_column(Float, default=220.0, nullable=False)
    current_a: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sockets_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ts: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    power_w: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    voltage_v: Mapped[float] = mapped_column(Float, default=220.0, nullable=False)
    current_a: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class CommandRecord(Base):
    __tablename__ = "cmd_records"

    cmd_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    socket: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    message: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

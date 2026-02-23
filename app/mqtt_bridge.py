from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

from .config import settings
from .db import get_session
from .services import (
    apply_command_effect_to_status,
    mark_device_offline,
    save_telemetry_point,
    update_cmd_state,
    update_status_from_payload,
)
from .ws import ws_manager

logger = logging.getLogger("mqtt-bridge")


class MQTTBridge:
    def __init__(self) -> None:
        self._enabled = settings.mqtt_enabled
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = mqtt.Client(client_id="dorm-power-backend")
        self._offline_reasons: dict[str, str] = {}
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def connected(self) -> bool:
        return self._connected

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def get_offline_reason(self, device_id: str) -> str | None:
        reason = self._offline_reasons.get(device_id, "").strip()
        return reason or None

    def _set_offline_reason(self, device_id: str, reason: str) -> None:
        self._offline_reasons[device_id] = reason.strip() or "设备离线"

    def _clear_offline_reason(self, device_id: str) -> None:
        self._offline_reasons.pop(device_id, None)

    def _normalize_offline_reason(self, reason: str) -> str:
        text = reason.strip().lower()
        if not text:
            return "设备断电或异常离线"
        if "power" in text or "断电" in text:
            return "设备断电"
        if "app" in text or "remote" in text or "manual" in text:
            return "APP 人为控制断电"
        if "overcurrent" in text or "overload" in text or "过流" in text or "过载" in text:
            return "过流/过载保护断电"
        if "unplug" in text or "拔掉" in text:
            return "插排电源被拔除"
        return reason.strip()

    def start(self) -> None:
        if not self._enabled:
            logger.info("MQTT disabled via MQTT_ENABLED=0")
            return
        try:
            self._client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
            self._client.loop_start()
            logger.info("MQTT connecting to %s:%s", settings.mqtt_host, settings.mqtt_port)
        except Exception as exc:
            logger.exception("MQTT connect failed: %s", exc)

    def stop(self) -> None:
        if not self._enabled:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            logger.exception("MQTT stop failed")

    def publish_cmd(self, device_id: str, payload: dict[str, Any]) -> bool:
        if not (self._enabled and self._connected):
            return False
        topics: list[str] = [f"{settings.mqtt_topic_prefix}/{device_id}/cmd"]
        chunks = [x for x in device_id.split(" ", 1) if x]
        if len(chunks) == 2:
            topics.append(f"{settings.mqtt_topic_prefix}/{chunks[0]}/{chunks[1]}/cmd")

        payload_text = json.dumps(payload, ensure_ascii=False)
        ok = False
        for topic in dict.fromkeys(topics):
            result = self._client.publish(topic, payload_text, qos=1)
            ok = ok or result.rc == mqtt.MQTT_ERR_SUCCESS
        return ok

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        self._connected = reason_code == 0
        logger.info("MQTT connected rc=%s", reason_code)
        if not self._connected:
            return
        base = settings.mqtt_topic_prefix
        for kind in ("status", "telemetry", "ack", "event", "lwt", "will", "offline"):
            # Compatible with both:
            # 1) dorm/{deviceId}/{kind}
            # 2) dorm/{room}/{device}/{kind}
            client.subscribe(f"{base}/+/{kind}", qos=1)
            client.subscribe(f"{base}/+/+/{kind}", qos=1)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any = None) -> None:
        self._connected = False
        logger.warning("MQTT disconnected rc=%s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parsed = self._parse_topic(msg.topic)
        if parsed is None:
            return
        device_id, msg_type = parsed
        raw_text = msg.payload.decode("utf-8", errors="ignore")

        payload: dict[str, Any]
        try:
            loaded = json.loads(raw_text)
            payload = loaded if isinstance(loaded, dict) else {"value": loaded}
        except Exception:
            if msg_type in {"lwt", "will", "offline"}:
                payload = {"message": raw_text}
            else:
                logger.warning("Invalid JSON payload on topic=%s", msg.topic)
                return

        with get_session() as session:
            if msg_type == "status":
                self._clear_offline_reason(device_id)
                update_status_from_payload(session, device_id, payload)
                self._broadcast_safe({"type": "DEVICE_STATUS", "deviceId": device_id, "payload": payload})
            elif msg_type == "telemetry":
                self._clear_offline_reason(device_id)
                save_telemetry_point(session, device_id, payload)
                self._broadcast_safe({"type": "TELEMETRY", "deviceId": device_id, "payload": payload})
            elif msg_type in {"lwt", "will", "offline"}:
                reason = self._normalize_offline_reason(str(payload.get("reason") or payload.get("message") or msg_type))
                self._set_offline_reason(device_id, reason)
                mark_device_offline(session, device_id, reason=reason)
                self._broadcast_safe(
                    {"type": "DEVICE_OFFLINE", "deviceId": device_id, "payload": {"reason": reason}}
                )
            elif msg_type == "ack":
                self._clear_offline_reason(device_id)
                cmd_id = str(payload.get("cmdId", ""))
                status = str(payload.get("status", "success"))
                cost_ms = payload.get("costMs")
                cmd = update_cmd_state(
                    session,
                    cmd_id,
                    "success" if status == "success" else "failed",
                    message=str(payload.get("errorMsg", "")),
                    duration_ms=int(cost_ms) if isinstance(cost_ms, (int, float)) else None,
                )
                if cmd:
                    if cmd.state == "success":
                        apply_command_effect_to_status(session, cmd)
                    event = {
                        "type": "CMD_ACK",
                        "cmdId": cmd.cmd_id,
                        "state": cmd.state,
                        "ts": int(time.time()),
                        "updatedAt": cmd.updated_at,
                        "message": cmd.message,
                        "durationMs": cmd.duration_ms,
                    }
                    self._broadcast_safe(event)

    def _broadcast_safe(self, payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), self._loop)

    def _parse_topic(self, topic: str) -> tuple[str, str] | None:
        topic_parts = [p for p in topic.strip("/").split("/") if p]
        prefix_parts = [p for p in settings.mqtt_topic_prefix.strip("/").split("/") if p]
        if len(topic_parts) < len(prefix_parts) + 2:
            return None
        if topic_parts[: len(prefix_parts)] != prefix_parts:
            return None

        tail = topic_parts[len(prefix_parts) :]
        msg_type = tail[-1]
        if msg_type not in {"status", "telemetry", "ack", "event", "lwt", "will", "offline"}:
            return None

        device_parts = [p.strip() for p in tail[:-1] if p.strip()]
        if not device_parts:
            return None

        # Canonical device id: "{room} {device}" to keep URL-safe path segment (no slash).
        if len(device_parts) == 1:
            token = " ".join(device_parts[0].split())
            return token, msg_type

        if len(device_parts) == 2:
            room = " ".join(device_parts[0].split())
            dev = " ".join(device_parts[1].split())
            if room and dev:
                return f"{room} {dev}", msg_type
            return " ".join([x for x in (room, dev) if x]), msg_type

        return " ".join(device_parts), msg_type


mqtt_bridge = MQTTBridge()

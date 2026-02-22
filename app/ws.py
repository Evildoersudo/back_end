from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        text = json.dumps(payload, ensure_ascii=False)
        stale: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)


ws_manager = WSManager()

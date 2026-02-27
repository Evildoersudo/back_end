# APP 端接口时序图

本文档补充 APP 联调关键流程时序，覆盖：

- 登录
- 拉设备列表
- 订阅 WebSocket
- 下发命令
- ACK 回执闭环

---

## 1. 总流程（登录 -> 拉列表 -> WS -> 下发命令 -> ACK）

```mermaid
sequenceDiagram
    autonumber
    participant APP as APP
    participant API as Backend API (FastAPI)
    participant WS as WS /ws
    participant MQTT as MQTT Bridge
    participant DEV as Device (ESP8266)

    APP->>API: POST /api/auth/login {account,password}
    API-->>APP: 200 {ok, token, user}

    APP->>API: GET /api/devices
    API-->>APP: 200 Device[]

    APP->>WS: connect ws://host:8000/ws
    WS-->>APP: (connected)

    APP->>API: POST /api/strips/{deviceId}/cmd {socket,action}
    API->>MQTT: publish dorm/.../cmd {cmdId,...}
    API-->>APP: 200 {ok, cmdId}

    DEV-->>MQTT: receive cmd, execute
    DEV->>MQTT: publish dorm/.../ack {cmdId,status,costMs}
    MQTT->>API: update cmd state
    API->>WS: broadcast CMD_ACK
    WS-->>APP: {"type":"CMD_ACK","cmdId":"...","state":"success|failed|timeout"}

    APP->>API: GET /api/cmd/{cmdId} (optional polling)
    API-->>APP: 200 {cmdId,state,...}
```

---

## 2. 设备状态与遥测实时更新

```mermaid
sequenceDiagram
    autonumber
    participant DEV as Device
    participant MQTT as MQTT Bridge
    participant API as Backend API
    participant WS as WS /ws
    participant APP as APP

    DEV->>MQTT: publish dorm/.../status {ts,total_power_w,sockets[]}
    MQTT->>API: update_status_from_payload
    API->>WS: broadcast DEVICE_STATUS
    WS-->>APP: {"type":"DEVICE_STATUS","deviceId":"...","payload":{...}}

    DEV->>MQTT: publish dorm/.../telemetry {ts,power_w,...}
    MQTT->>API: save_telemetry_point
    API->>WS: broadcast TELEMETRY
    WS-->>APP: {"type":"TELEMETRY","deviceId":"...","payload":{...}}
```

---

## 3. 离线流程（遗嘱触发）

```mermaid
sequenceDiagram
    autonumber
    participant DEV as Device
    participant MQTT as MQTT Bridge
    participant API as Backend API
    participant WS as WS /ws
    participant APP as APP

    DEV--xMQTT: unexpected disconnect (power off / unplug)
    Note over DEV,MQTT: Broker 触发 LWT / will / offline topic
    MQTT->>API: mark_device_offline(reason)
    API->>WS: broadcast DEVICE_OFFLINE {reason}
    WS-->>APP: {"type":"DEVICE_OFFLINE","deviceId":"...","payload":{"reason":"设备断电"}}

    APP->>API: GET /api/devices (or periodic refresh)
    API-->>APP: online=false, offlineReason=...
```

---

## 4. APP 推荐实现策略

1. 登录成功后保存 `token`（当前后端返回 token，后续可扩展鉴权拦截）。
2. 首页先 `GET /api/devices` 拉首屏数据，再连 `/ws` 做增量更新。
3. 命令执行采用“双通道确认”：
   - 主通道：监听 `CMD_ACK` WebSocket 事件
   - 兜底：轮询 `GET /api/cmd/{cmdId}`
4. 设备离线后禁用控制按钮，并展示 `offlineReason`。
5. `deviceId` 含空格时必须 URL 编码（例如 `A-303 strip01` -> `A-303%20strip01`）。


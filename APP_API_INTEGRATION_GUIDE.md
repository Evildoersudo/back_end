# Dorm Power 项目能力与 APP 接口集成文档

本文档用于 APP 端设计与联调参考，基于当前后端实现（FastAPI）整理。

---

## 1. 项目当前已实现功能

### 1.1 账号与访问

- 单管理员账号登录（仅 `login`，无注册/找回）
- 登录成功返回 token（当前主要用于前端会话，不做严格鉴权拦截）

### 1.2 设备与状态

- 设备自动注册（首次收到设备消息自动入库）
- 设备列表查询
- 单设备实时状态查询（总功率、电压、电流、各插孔状态）
- 在线/离线判定
  - 超时离线（`ONLINE_TIMEOUT_SECONDS`，默认 10 秒）
  - 遗嘱/离线消息即时离线（`lwt/will/offline`）
- 离线原因追踪（设备列表可返回 `offlineReason`）

### 1.3 控制闭环

- 下发控制命令（开关/模式/策略等扩展动作）
- 命令冲突检测（同目标有 pending 时返回 409）
- 命令状态查询（pending/success/failed/timeout）
- 设备 ACK 回执处理

### 1.4 历史与分析

- 遥测数据查询（`60s/24h/7d/30d`）
- AI 报告接口（按房间与周期输出摘要/异常/建议）

### 1.5 实时推送

- WebSocket 推送：
  - `DEVICE_STATUS`
  - `TELEMETRY`
  - `CMD_ACK`
  - `DEVICE_OFFLINE`

### 1.6 基础设施

- MQTT 接入（EMQX）
- PostgreSQL 持久化（Docker 部署）
- 健康检查接口 `/health`

---

## 2. 后端基础信息

- Base URL（生产示例）：`http://175.27.162.174:8000`
- API 前缀：`/api`
- WebSocket：`/ws`
- 数据格式：`application/json`
- 时间戳：Unix 秒（`int`）

通用错误结构：

```json
{
  "ok": false,
  "code": "BAD_REQUEST",
  "message": "range is invalid",
  "details": {}
}
```

---

## 3. APP 开发核心接口清单

## 3.1 登录

- `POST /api/auth/login`

请求：

```json
{
  "account": "admin",
  "password": "admin123"
}
```

响应：

```json
{
  "ok": true,
  "token": "xxxxx",
  "user": {
    "username": "admin",
    "email": "admin@dorm.local",
    "role": "admin"
  }
}
```

---

## 3.2 健康检查

- `GET /health`

响应：

```json
{
  "ok": true,
  "mqtt_enabled": true,
  "mqtt_connected": true,
  "database_url": "postgresql+psycopg://..."
}
```

---

## 3.3 设备列表

- `GET /api/devices`

响应：

```json
[
  {
    "id": "A-303 strip01",
    "name": "strip01",
    "room": "A-303",
    "online": false,
    "lastSeen": "2026-02-24T10:30:00Z",
    "offlineReason": "设备断电"
  }
]
```

字段说明：

- `offlineReason`：仅离线时有值；在线时为 `null`

---

## 3.4 单设备状态

- `GET /api/devices/{device_id}/status`

示例：`GET /api/devices/A-303%20strip01/status`

响应：

```json
{
  "ts": 1772000000,
  "online": true,
  "total_power_w": 128.6,
  "voltage_v": 220.9,
  "current_a": 0.58,
  "sockets": [
    { "id": 1, "on": true, "power_w": 82.0, "device": "PC" },
    { "id": 2, "on": false, "power_w": 0.0, "device": "None" }
  ]
}
```

---

## 3.5 遥测历史

- `GET /api/telemetry?device={id}&range={60s|24h|7d|30d}`

示例：
- `/api/telemetry?device=A-303%20strip01&range=24h`

响应：

```json
[
  { "ts": 1771990000, "power_w": 95.2 },
  { "ts": 1771990900, "power_w": 102.7 }
]
```

---

## 3.6 下发控制命令

- `POST /api/strips/{device_id}/cmd`

示例请求（插孔开关）：

```json
{
  "socket": 1,
  "action": "off"
}
```

示例请求（模式）：

```json
{
  "action": "mode",
  "mode": "eco"
}
```

示例请求（策略扩展）：

```json
{
  "action": "policy",
  "payload": {
    "kind": "illegal_detect",
    "enabled": true
  }
}
```

响应：

```json
{
  "ok": true,
  "cmdId": "cmd_1772000000_ab12cd34",
  "stripId": "A-303 strip01",
  "acceptedAt": 1772000000
}
```

---

## 3.7 查询命令执行状态

- `GET /api/cmd/{cmd_id}`

响应：

```json
{
  "cmdId": "cmd_1772000000_ab12cd34",
  "state": "success",
  "updatedAt": 1772000001,
  "message": "",
  "durationMs": 320
}
```

`state` 取值：
- `pending`
- `success`
- `failed`
- `timeout`
- `cancelled`

---

## 3.8 房间 AI 报告

- `GET /api/rooms/{room_id}/ai_report?period=7d|30d`

响应：

```json
{
  "room_id": "A-303",
  "period": "7d",
  "summary": "Average power is about 98.2W...",
  "anomalies": ["Peak power reached 186.0W..."],
  "suggestions": ["Enable auto off after 00:30..."]
}
```

---

## 4. WebSocket 实时事件（APP 推荐接入）

连接：
- `ws://175.27.162.174:8000/ws`

事件示例：

1. 设备状态更新

```json
{
  "type": "DEVICE_STATUS",
  "deviceId": "A-303 strip01",
  "payload": { "...": "..." }
}
```

2. 遥测更新

```json
{
  "type": "TELEMETRY",
  "deviceId": "A-303 strip01",
  "payload": { "...": "..." }
}
```

3. 命令 ACK

```json
{
  "type": "CMD_ACK",
  "cmdId": "cmd_1772000000_ab12cd34",
  "state": "success",
  "ts": 1772000001,
  "updatedAt": 1772000001,
  "message": "",
  "durationMs": 320
}
```

4. 设备离线（遗嘱触发）

```json
{
  "type": "DEVICE_OFFLINE",
  "deviceId": "A-303 strip01",
  "payload": {
    "reason": "设备断电"
  }
}
```

---

## 5. APP 页面与接口映射建议

1. 登录页
- `POST /api/auth/login`

2. 设备总览页
- `GET /api/devices`
- 可选：`/ws` 监听在线状态变化

3. 设备详情页
- `GET /api/devices/{id}/status`
- `GET /api/telemetry?device={id}&range=...`
- `POST /api/strips/{id}/cmd`
- `GET /api/cmd/{cmdId}`
- 可选：`/ws` 实时叠加

4. 实时监控页
- 同设备详情，但加更高频刷新或 WS

5. 历史分析页
- `GET /api/telemetry`
- `GET /api/rooms/{room_id}/ai_report`

6. 系统状态页（运维）
- `GET /health`

---

## 6. 状态与交互规则（建议 APP 保持一致）

- 命令按钮点击后状态机：
  - `sending -> pending -> success/failed/timeout`
- 只有命令状态为 `success` 才提示“执行成功”
- 设备离线时：
  - 禁用实时控制按钮
  - UI 显示 `offlineReason`
- 对 `device_id` 含空格时，路径参数必须 URL 编码（`%20`）

---

## 7. 版本说明

- 文档版本：`v1.0`
- 对应后端：当前 `web/back_end/app/main.py` 实现
- 若后端新增鉴权中间件、分页、批量接口，需同步更新本文件


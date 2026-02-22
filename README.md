# Dorm Power Backend

面向 `web/front/dorm-power-console/API_CONTRACT.md` 的后端实现（无 Docker 版本）。

## 1. 功能覆盖

- `GET /api/devices`
- `GET /api/devices/{id}/status`
- `GET /api/telemetry?device={id}&range={60s|24h|7d|30d}`
- `POST /api/strips/{id}/cmd`
- `GET /api/cmd/{cmdId}`
- `GET /api/rooms/{room_id}/ai_report?period=7d|30d`
- `GET /health`
- `WS /ws`（推送 `CMD_ACK`、状态类事件）

## 2. 目录结构

```text
web/back_end
  app/
    main.py          # FastAPI 入口
    mqtt_bridge.py   # MQTT 订阅/发布桥接
    services.py      # 业务逻辑（设备、遥测、命令、AI报告）
    models.py        # SQLAlchemy 模型
    db.py            # 数据库连接
    config.py        # 环境变量配置
    ws.py            # WebSocket 连接管理
  requirements.txt
  .env.example
```

## 3. 本地运行（无 Docker）

1. 安装依赖：

```bash
cd web/back_end
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. 配置环境变量：

```bash
copy .env.example .env
```

3. 启动服务：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 4. 数据库放置方案

### 方案 A：本地数据库（默认，推荐先跑通）

- 默认 `DATABASE_URL=sqlite:///./iot_backend.db`
- 不需要安装数据库服务，适合开发调试

### 方案 B：服务器数据库

- 修改 `.env` 中 `DATABASE_URL` 指向远程数据库，例如：

```bash
DATABASE_URL=postgresql+psycopg://user:password@<server_ip>:5432/dorm_power
```

- 若使用 PostgreSQL，需要额外安装驱动：

```bash
pip install psycopg[binary]
```

## 5. MQTT 接入说明

- 默认 `MQTT_ENABLED=0`，后端可先独立跑通 HTTP API。
- 启用 MQTT 时设置：

```bash
MQTT_ENABLED=1
MQTT_HOST=<broker_ip>
MQTT_PORT=1883
MQTT_TOPIC_PREFIX=dorm
```

设备 topic 规范：

- `dorm/{deviceId}/status`
- `dorm/{deviceId}/telemetry`
- `dorm/{deviceId}/event`
- `dorm/{deviceId}/cmd`
- `dorm/{deviceId}/ack`

## 6. 接口验证

服务启动后，先验证：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/devices`

默认会自动创建一个种子设备 `strip01`，便于前端联调。

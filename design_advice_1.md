

## 0) 你现在已经验证的闭环是什么（用一句话写进报告）

* 后端作为 MQTT Client 连接 EMQX（Broker）
* 后端向 `cmd topic` 发布命令
* ESP8266 订阅 `cmd topic`，执行后向 `ack topic` 发布回执
* 后端订阅 `ack topic` 收到回执，完成“命令-回执闭环”

> 这套闭环不依赖“EMQX 主动推送”，而是通过订阅机制由 EMQX 转发实现。

---

# 1) 体系结构：EMQX 在中间做“路由”，后端是“网关+API”

你现在的架构应当这样描述：

**ESP（端）** 只负责：

* publish：状态/遥测/事件
* subscribe：命令
* publish：命令回执 ack

**EMQX（broker）** 负责：

* 转发 MQTT 消息（基于 topic）
* 连接管理/ACL（可选）

**后端（cloud gateway）** 负责：

* subscribe：状态/遥测/事件/ack（从 EMQX “拿消息”）
* publish：cmd（通过 EMQX “下发给设备”）
* **把 MQTT 世界转换成前端可用的 HTTP + WebSocket 世界**
* 把实时数据缓存 + 历史数据（Redis ring buffer）做可视化接口

这就叫“云端接入服务 / 设备接入网关”。

---

# 2) Topic 规范（现在你闭环跑通后，就该定规范）

为了后期支持多设备、以及后端一套逻辑订阅全部设备，建议你统一为：

## 2.1 推荐 Topic（最简但可扩展）

* 上行：

  * `dorm/{deviceId}/status`（快照 1~2s）
  * `dorm/{deviceId}/telemetry`（曲线 1~5s）
  * `dorm/{deviceId}/event`（告警/重启/异常）
* 下行：

  * `dorm/{deviceId}/cmd`
  * `dorm/{deviceId}/ack`

后端订阅用通配符一次搞定：

* `dorm/+/status`
* `dorm/+/telemetry`
* `dorm/+/event`
* `dorm/+/ack`

> 你已经用 `cmd/ack` 跑通了，下一步就是把 status/telemetry 加进来，然后你的 Dashboard / Live / Devices 就能用真数据。

---

# 3) Payload 规范（闭环能跑通后，下一步就是“可观测、可追踪”）

你现在跑通的 ack 建议补全为稳定可追踪格式：

## 3.1 cmd（后端下发）

必须字段：`cmdId`（唯一ID）、`ts`、`type`、`payload`
可选：`socketId`、`timeoutMs`、`source`

```json
{
  "cmdId": "c_20260222_abc123",
  "ts": 1730000000,
  "type": "SOCKET_SET",
  "socketId": 1,
  "payload": { "on": false },
  "timeoutMs": 5000,
  "source": "web"
}
```

## 3.2 ack（ESP 回执）

必须字段：`cmdId`、`status`、`ts`
可选：`errorCode`、`errorMsg`、`costMs`

```json
{
  "cmdId": "c_20260222_abc123",
  "status": "success",
  "ts": 1730000001,
  "costMs": 180,
  "errorCode": 0,
  "errorMsg": ""
}
```

> 竞赛展示时你能讲：**系统支持可追踪命令ID、执行耗时统计、失败原因回传**，非常加分。

---

# 4) 后端要做的“工程化完善点”（你现在最需要补齐的部分）

你已经验证了最小闭环。接下来后端要做的，是把闭环变成“可靠系统”。

## 4.1 命令状态管理（后端必须存 cmd 状态）

* `/api/cmd` 生成 cmdId 后，先写 Redis：`cmd:{cmdId} = pending`（TTL 30s）
* publish 到 `dorm/{deviceId}/cmd`
* 收到 `ack` 后更新 `cmd:{cmdId} = success/failed`
* 同时写入事件流：`events:{deviceId}` push 一条 CMD 事件

> 这样前端不管走 WS 还是轮询都能拿到命令结果。

## 4.2 ack 的“关联规则”

后端收到 ack 后要做两件事：

1. **按 cmdId 更新 cmd 状态**（让前端“命令回执状态机”结束 pending）
2. **按 deviceId 记录事件**（Event Feed 可展示）

如果你还想更稳：

* 再维护一个：`lastCmd:{deviceId}:{socketId} -> cmdId`（TTL 60s）

  * SocketCard 上可以显示“最近一次命令结果”

## 4.3 超时机制（必须有）

现实中有两种超时：

* ESP 不在线 / 收不到 cmd
* ESP 收到但 ack 没回来（异常/掉线）

后端建议这样做：

* cmd 写入 Redis 时 TTL=30s
* 前端 pending 超时 4~6s（展示友好）
* 后端即使超时也能 later ack（如果到达则更新 event，可选）

---

# 5) “ESP subscribe、ESP publish ack、后端订阅”的标准实现模式

你现在已经跑通了，我把它总结成你以后每种命令都能复用的模式：

## 5.1 ESP 侧（通用模式）

* subscribe：`dorm/{deviceId}/cmd`
* callback(topic, payload):

  1. parse JSON → 取出 `cmdId/type/payload`
  2. 执行控制逻辑（继电器/模式切换）
  3. 生成 ack JSON（带 cmdId + status + costMs）
  4. publish：`dorm/{deviceId}/ack`

这套 callback 不变，你只是扩展 `type` 分支即可。

## 5.2 后端侧（通用模式）

* POST /api/cmd：

  1. 生成 cmdId
  2. Redis 写 pending
  3. publish 到 `dorm/{deviceId}/cmd`
  4. 返回 cmdId 给前端
* MQTT on_message(ack)：

  1. Redis 更新 cmd 状态
  2. 写 events
  3. WS 推送 CMD_ACK 给前端

---

# 6) 你现在下一步应该怎么推进（最短路径接入前端真数据）

既然 cmd/ack 已通，下一步按这个顺序：

## Step 1：让 ESP 固定周期 publish status（最重要）

* topic：`dorm/{deviceId}/status`
* payload：至少包含 `ts`, `total_power_w`, `sockets[]`（哪怕先简化）

后端收到后写 `status:{deviceId}`，前端 Devices/Detail/Live 立刻能有真数据。

## Step 2：让 ESP publish telemetry（曲线点）

* topic：`dorm/{deviceId}/telemetry`
* payload：`ts, power_w, voltage_v, current_a`
  后端写 Redis ring buffer，Dashboard/History 图表就能是真曲线。

## Step 3：前端接 WebSocket（让回执“秒级可见”）

* 后端 WS 推送 `CMD_ACK`
* 前端 pending 立刻结束并展示成功/失败

---

# 7) 你可以直接复制到竞赛答辩里的“技术亮点”表述

* MQTT Broker（EMQX）实现多设备消息路由与连接管理
* 后端作为设备接入网关，订阅上报数据并提供 REST/WS 接口给前端
* 控制链路支持 cmdId 追踪、ack 回执、超时处理、事件流记录，形成可视化闭环
* 历史数据采用 Redis ring buffer 轻量存储，实现秒级趋势展示与数据导出扩展

---

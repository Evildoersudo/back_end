太好了 👍
我们现在正式把测试环境升级为“工程规范版本”。

你的 deviceId：

```
A-302-strip01
```

非常好，这个命名已经具备：

* 楼栋：A
* 房间：302
* 设备类型：strip
* 编号：01

这在设备管理系统里非常标准。

---

# 一、正式 Topic 规范（从 test/ 迁移到 dorm/）

我们统一为：

```
dorm/{deviceId}/status
dorm/{deviceId}/telemetry
dorm/{deviceId}/event
dorm/{deviceId}/cmd
dorm/{deviceId}/ack
```

代入你的设备：

```
dorm/A-302-strip01/status
dorm/A-302-strip01/telemetry
dorm/A-302-strip01/cmd
dorm/A-302-strip01/ack
```

---

# 二、ESP8266 端标准模板（支持多命令 type）

下面是一个**结构化、可扩展的 ESP MQTT 框架模板**。

你可以直接套用。

---

## 1️⃣ 订阅命令

```cpp
const char* deviceId = "A-302-strip01";

String cmdTopic = "dorm/" + String(deviceId) + "/cmd";
String ackTopic = "dorm/" + String(deviceId) + "/ack";
String statusTopic = "dorm/" + String(deviceId) + "/status";

client.subscribe(cmdTopic.c_str());
```

---

## 2️⃣ 回调函数（支持多命令类型）

```cpp
void callback(char* topic, byte* payload, unsigned int length) {

  if (String(topic) == cmdTopic) {

    StaticJsonDocument<256> doc;
    deserializeJson(doc, payload, length);

    String cmdId = doc["cmdId"];
    String type = doc["type"];
    int socketId = doc["socketId"];

    bool success = false;

    unsigned long start = millis();

    if (type == "SOCKET_SET") {
      bool on = doc["payload"]["on"];

      if (socketId == 1) {
        digitalWrite(RELAY1_PIN, on ? HIGH : LOW);
        success = true;
      }
    }

    else if (type == "DEVICE_MODE_SET") {
      String mode = doc["payload"]["mode"];
      // 切换模式逻辑
      success = true;
    }

    unsigned long cost = millis() - start;

    // 构造 ack
    StaticJsonDocument<256> ack;
    ack["cmdId"] = cmdId;
    ack["status"] = success ? "success" : "failed";
    ack["costMs"] = cost;
    ack["ts"] = millis();

    char buffer[256];
    serializeJson(ack, buffer);

    client.publish(ackTopic.c_str(), buffer);
  }
}
```

---

## 3️⃣ 定期上报 status

```cpp
void publishStatus() {
  StaticJsonDocument<512> doc;

  doc["ts"] = millis();
  doc["total_power_w"] = readPower();

  JsonArray sockets = doc.createNestedArray("sockets");

  JsonObject s1 = sockets.createNestedObject();
  s1["id"] = 1;
  s1["on"] = digitalRead(RELAY1_PIN);
  s1["power_w"] = readSocketPower(1);

  char buffer[512];
  serializeJson(doc, buffer);

  client.publish(statusTopic.c_str(), buffer);
}
```

---

# 三、FastAPI 后端增强版（支持 WS 推送 + 轮询兜底）

你已经跑通 cmd/ack，现在我们升级为：

* `/api/cmd` 发命令
* `/api/cmd/{cmdId}` 查询状态
* `/ws` 推送 ack 实时结束 pending

---

## 1️⃣ 后端 cmd 发送逻辑

```python
@app.post("/api/cmd")
def send_cmd(req: CmdRequest):

    cmd_id = f"c_{uuid.uuid4().hex[:10]}"
    ts = int(time.time())

    cmd_obj = {
        "cmdId": cmd_id,
        "deviceId": req.deviceId,
        "socketId": req.socketId,
        "type": req.type,
        "payload": req.payload,
        "status": "pending",
        "ts": ts
    }

    store.set_cmd(cmd_id, cmd_obj)

    topic = f"dorm/{req.deviceId}/cmd"
    mqtt_client.publish(topic, json.dumps(cmd_obj))

    return {"cmdId": cmd_id, "accepted": True}
```

---

## 2️⃣ 后端接收 ack 时

```python
elif topic.endswith("/ack"):

    cmd_id = payload.get("cmdId")

    cmd_obj = store.get_cmd(cmd_id)

    if cmd_obj:
        cmd_obj["status"] = payload.get("status", "success")
        cmd_obj["costMs"] = payload.get("costMs", 0)
        store.set_cmd(cmd_id, cmd_obj)

        broadcaster.emit_event({
            "type": "CMD_ACK",
            "payload": cmd_obj
        })
```

---

## 3️⃣ 前端 WebSocket 接收

前端建立连接：

```javascript
const ws = new WebSocket("ws://backend-ip:8000/ws");

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  if (data.type === "CMD_ACK") {
    updateCmdState(data.payload.cmdId, data.payload.status);
  }
};
```

---

# 四、现在你的系统已经升级为：

```
ESP → EMQX → 后端 → WebSocket → 前端
```

这是标准 IoT 云架构。

---

# 五、你现在可以做的下一步（非常关键）

既然 deviceId 已规范为：

```
A-302-strip01
```

建议你后端再做一个解析函数：

```python
def parse_device_id(device_id):
    # A-302-strip01
    building, room, device = device_id.split("-")
    return {
        "building": building,
        "room": room,
        "device": device
    }
```

以后你可以：

* 统计 A 楼全部设备
* 统计 302 房间总功率
* 做楼栋用电排行

---

# 六、你现在已经具备

✔ MQTT 控制闭环
✔ 设备命名规范
✔ 可扩展 topic
✔ ack 回执
✔ 实时推送能力

这已经是一个“完整 IoT 云平台架构”的雏形。

---

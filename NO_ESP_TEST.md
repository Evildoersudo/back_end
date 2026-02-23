# No-ESP Local Data Flow Test

Use this when you want to verify the full frontend/backend flow without real ESP devices.

## 1. Start backend

```powershell
cd e:\embedding_competition\web\back_end
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 2. Start simulator (status + telemetry)

Open another terminal:

```powershell
cd e:\embedding_competition\web\back_end
.\.venv\Scripts\python.exe -m tools.simulate_device --device-id strip01 --interval 1
```

## 3. (Optional) Simulate command ack success

If you want `/api/cmd/{cmdId}` to become `success` automatically:

```powershell
cd e:\embedding_competition\web\back_end
.\.venv\Scripts\python.exe -m tools.simulate_device --device-id strip01 --interval 1 --auto-ack
```

## 4. Start frontend

```powershell
cd e:\embedding_competition\web\front\dorm-power-console
npm run dev
```

`web/front/dorm-power-console/.env.local` should point to local backend:

```env
BACKEND_BASE_URL=http://127.0.0.1:8000
```

## 5. Quick verification

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/devices
Invoke-RestMethod http://127.0.0.1:8000/api/devices/strip01/status
Invoke-RestMethod "http://127.0.0.1:8000/api/telemetry?device=strip01&range=60s"
```

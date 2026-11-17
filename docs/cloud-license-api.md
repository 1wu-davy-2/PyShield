# 云端授权与扣次接口（草案）

> 状态：**接口未定稿**。客户端已预留调用点，默认关闭（`PYSHELD_CLOUD_API=1` 才启用）。
> 本文定义桌面端期望的字段，供授权服务实现 `/api/license/*` 时对齐。

## 目标

1. 每次构建前向云端校验授权并**同步扣减 1 次**，不再依赖本地计次。
2. 构建中/空闲时**随机 1–5 分钟**校验一次绑定信息；发现 license_id、序列号、机器码、版本或签名不一致时，立即禁止继续使用。
3. 客户端不再声明“完全离线”：文案与页面逐步改为“授权由云端下发”。

## 客户端标识

每次请求都携带以下字段（来自已安装的 entitlement）：

| 字段 | 说明 |
| --- | --- |
| `license_id` | 云端授权 ID，例如 `TRIAL-PS-0WEA-2QRQ-SMPV` |
| `serial` | 本机序列号，例如 `PS-0WEA-2QRQ-SMPV` |
| `machine` | 机器码（sha256 前 32 位） |
| `edition` | `trial` / `pro` |
| `revision` | 授权修订号，服务端递增 |
| `signature` | 可选：授权文件签名，服务端可复核绑定 |

## 端点

### POST /api/license/preflight

构建前置校验（不扣次）。

```json
{"license_id":"TRIAL-PS-...","serial":"PS-...","machine":"...","edition":"trial","revision":1}
```

响应：

```json
{"ok":true,"allowed":true,"remaining_builds":3,"quota_month":"2026-11","reason":""}
```

不允许时 `ok=false` + `code`（`quota_exhausted` / `revoked` / `machine_mismatch` / `expired`）。

### POST /api/license/consume

真正开始构建时调用，扣 1 次，需幂等键。

```json
{"license_id":"...","serial":"...","machine":"...","reason":"build",
 "idempotency_key":"<job_id>","project_hash":"..."}
```

响应：

```json
{"ok":true,"remaining_builds":2,"charged":true,"duplicate":false}
```

失败必须返回 `charged=false`，客户端据此中止构建；构建失败是否退还由服务端策略决定（建议仅 `PROJECT_CODE` 退还）。

### POST /api/license/verify

随机巡检（1–5 分钟一次），字段同上，另带 `nonce` 与 `client_time`。

```json
{"license_id":"...","serial":"...","machine":"...","revision":1,
 "nonce":"...","client_time":"2026-11-17T09:30:00+08:00"}
```

响应：

```json
{"ok":true,"status":"active","server_time":"...","next_check_after":240}
```

`ok=false` 时客户端进入受限状态（只读、禁止构建），并在界面提示具体原因。

## 客户端行为（已实现骨架）

- `pyshield/cloud_license.py`：`preflight` / `consume` / `verify` / `start_watchdog`，默认全部短路为 `{"ok": true, "skipped": true}`。
- `BuildManager._prepare`：构建前调用 `preflight`；返回不允许时给 `needs_activation`，前端跳「版本与试用」。
- 巡检线程：`start_watchdog(entitlement_provider, on_blocked)`，按随机间隔调用 `verify`，失败回调里禁用构建入口（UI 钩子待接口定稿后接）。

## 待定问题

1. 扣次与构建任务如何绑定（`idempotency_key` 用 job_id 还是本地 UUID）。
2. 断网宽限：建议允许 1 次离线构建并在下次联网补扣，需服务端确认。
3. 巡检失败几次判定为不可用（建议连续 3 次或任意一次 `revoked` 立即生效）。
4. 是否需要客户端上传构建摘要（`project_hash`）做防重放。

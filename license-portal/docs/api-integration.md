# PyShield 授权门户接口对接流程

本文是 `license-portal` 的完整接口对接说明，面向两类读者：

- **桌面客户端开发者**：接入试用、激活、刷新、吊销。
- **门户/运营开发者**：接入浏览器公共 API 和管理端 API，完成发码、续费、升级、吊销和审计。

> 适用版本：`license-portal 0.1.0`，授权文档格式 `pyshield-tool-license/1`。
>
> **最重要的约束**：`/api/trial/*`、`/api/license/*` 属于桌面客户端稳定契约，只能新增字段，不能改路径、改字段含义或删除字段。`/api/public/*`、`/api/admin/*` 是门户内部 API，随门户一起发布，不承诺与旧前端二进制兼容。

---

## 目录

1. [标识与格式](#1-标识与格式)
2. [通用约定](#2-通用约定)
3. [接口分组与鉴权](#3-接口分组与鉴权)
4. [端到端流程](#4-端到端流程)
5. [健康检查与版本](#5-健康检查与版本)
6. [桌面客户端稳定接口](#6-桌面客户端稳定接口)
7. [浏览器公共 API](#7-浏览器公共-api)
8. [管理端 API](#8-管理端-api)
9. [签名授权文档](#9-签名授权文档)
10. [状态机](#10-状态机)
11. [限流、TTL 与幂等](#11-限流ttl-与幂等)
12. [错误码大全](#12-错误码大全)
13. [完整联调示例](#13-完整联调示例)
14. [客户端接入清单](#14-客户端接入清单)
15. [安全边界](#15-安全边界)
16. [排障](#16-排障)
17. [变更管理](#17-变更管理)

---

## 1. 标识与格式

### 1.1 基础标识

| 名称 | 格式 | 生成方 | 用途 |
| --- | --- | --- | --- |
| 序列号 `serial` | `PS-XXXX-XXXX-XXXX` | 客户端首次启动时生成并持久化 | 一台设备一个序列号；服务端以它定位授权记录 |
| 机器码 `machine` | 32 位十六进制，例如 `0123456789abcdef0123456789abcdef` | 客户端本机指纹 | 绑定授权；服务端按小写比较 |
| 授权编号 `license_id` | `TRIAL-<serial>` / `PRO-<serial>` / `ENT-<serial>` | 服务端 | 授权主体标识；升级版本时会变化 |
| 试用码 `trial_code` | `PST-XXXX-XXXX-XXXX` | 服务端 | 页面展示、人工兜底；**不是**邮箱验证码 |
| 邮箱验证码 | 6 位数字 | 服务端 | 试用申请时发送到邮箱，10 分钟有效 |
| 激活码 | `PSP-XXXX-XXXX-XXXX-XXXX` 或 `PSE-XXXX-XXXX-XXXX-XXXX` | 管理端签发 | 专业版/企业版兑换；明文只在签发时返回一次 |

字符集为 Crockford Base32：`0123456789ABCDEFGHJKMNPQRSTVWXYZ`，不包含 `I`、`L`、`O`、`U`。

- 激活码归一化时允许小写、省略连字符，并把常见的 `O -> 0`、`I/L -> 1` 纠正；格式错误返回 `validation_failed`，格式正确但不存在返回 `invalid_key`。
- 序列号必须是 12 位 Crockford 字符（不含前缀 `PS`），机器码必须是 32 位十六进制；否则返回 `validation_failed`，字段级错误在 `errors.serial` / `errors.machine`。
- 邮箱验证码只接受 6 位数字；不接受试用码 `PST-...` 冒充邮箱验证码。

### 1.2 版本与功能

门户只签发以下业务版本：

| `edition` | 说明 | 默认功能 |
| --- | --- | --- |
| `trial` | 免费试用，`trial_days` 天、`trial_builds` 次/月 | `gui` |
| `pro` | 专业版 | `gui`、`native-runtime`、`license-issuance` |
| `enterprise` | 企业版 | `gui`、`native-runtime`、`offline-activation`、`license-issuance`、`ci` |

> 管理端 API 的 `edition` 校验使用内部版本集合，因此 `oem` 能通过参数校验；但 `oem` 没有激活码前缀，生成时会失败，业务上不应通过门户发放 OEM。`trial` 激活码可以生成，但兑换时会被 `/api/license/activate` 以 `invalid_edition` 拒绝；对外发码只应使用 `pro`、`enterprise`。

功能名固定为：`gui`、`native-runtime`、`offline-activation`、`license-issuance`、`ci`、`oem`、`*`。管理端创建激活码时 `features` 留空会使用对应版本的默认功能。

---

## 2. 通用约定

### 2.1 地址与传输

| 项目 | 约定 |
| --- | --- |
| Base URL | 生产：`https://license.example.com`；本地：`http://127.0.0.1:8091` 或配置的端口 |
| 协议 | 桌面客户端连接非本机地址时必须 HTTPS；`localhost`、`127.0.0.1`、`::1` 允许 HTTP |
| 路径 | 精确匹配，**不要加尾随斜杠**；`/api/public/config/` 会返回 404 |
| 请求体 | JSON 对象，UTF-8；最大 64 KiB（可配置 `max_body`） |
| 响应 | JSON，UTF-8；API 响应默认 `Cache-Control: no-store` |
| 时间 | 日期字段为 `YYYY-MM-DD`；时间戳为 UTC ISO 8601，例如 `2026-11-16T08:30:00+00:00` |
| CORS | **不提供 CORS**；浏览器只能同源访问，桌面端用原生 HTTP 客户端 |

> 生产环境建议在 Nginx 层同时限制请求体大小、连接数和访问频率；门户内置限流只覆盖部分写操作。

### 2.2 通用请求头

```http
Accept: application/json
Content-Type: application/json
User-Agent: PyShield/0.6.0
```

- 桌面端不要求 `Origin`；带不带都可以。
- 浏览器写操作如果带 `Origin` 或 `Referer`，必须与配置的 `site_url`（协议 + 主机 + 端口）一致；否则返回 `403 origin_rejected`。
- 无 `Origin` 的 `curl`、服务端到服务端调用是允许的，此时依赖机器码绑定、限流和验证码。

### 2.3 通用错误格式

```json
{
  "error": "机器码与授权记录不一致",
  "code": "machine_mismatch"
}
```

字段校验失败时额外返回字段级错误：

```json
{
  "error": "请检查填写的内容",
  "code": "validation_failed",
  "errors": {
    "serial": "序列号格式不正确，应为 PS-XXXX-XXXX-XXXX",
    "machine": "机器码格式不正确，请从软件内重新打开本页面"
  }
}
```

客户端应：

1. 优先展示 `error`；
2. 用 `code` 做程序分支，不要匹配中文文案；
3. 表单类请求把 `errors` 映射到对应输入框；
4. 未知字段直接忽略，保证未来新增字段不破坏兼容性。

### 2.4 HTTP 状态码语义

| 状态码 | 含义 |
| --- | --- |
| `200` | 成功 |
| `201` | 创建成功（目前只有管理端批量生成激活码） |
| `400` | 参数错误、验证码错误、激活码格式错误、业务前置条件失败 |
| `401` | 未登录，或登录失败 |
| `403` | 来源校验、CSRF、IP 白名单、机器码不匹配、授权/激活码已吊销或过期 |
| `404` | 路径不存在，或对象不存在 |
| `405` | 方法不允许（例如 GET 登录接口） |
| `413` | 请求体过大 |
| `429` | 触发限流，或验证码错误次数过多 |
| `500` | 服务端内部错误 |
| `502` | 邮件发送失败 |

---

## 3. 接口分组与鉴权

| 分组 | 路径前缀 | 鉴权 | 调用方 |
| --- | --- | --- | --- |
| 桌面客户端契约 | `/api/trial/*`、`/api/license/*` | 无会话；涉及授权记录的接口依赖序列号 + 机器码（吊销列表除外） | PyShield 桌面应用 |
| 浏览器公共 API | `/api/public/*` | 无会话；写操作校验 Origin、验证码/限流 | 门户网页 |
| 管理端 API | `/api/admin/*` | `pyshield_portal_session` Cookie + `X-CSRF-Token`，可叠加 IP 白名单 | 门户管理端 |
| 健康/版本 | `/health`、`/api/version` | 无 | 监控、客户端 |

### 3.1 管理端会话

登录成功后服务端设置：

```http
Set-Cookie: pyshield_portal_session=<token>; Path=/; SameSite=Lax; HttpOnly; Secure; Max-Age=43200
```

- Cookie 是 HttpOnly，JavaScript 读不到；真正的 CSRF 防护依赖响应里的 `csrf_token`。
- 所有非 GET 的管理端请求必须带：

```http
X-CSRF-Token: <登录或 /api/admin/me 返回的 csrf_token>
```

- 会话默认 12 小时（`session_hours`），没有刷新接口；过期后重新登录或调用 `/api/admin/me` 判断。
- `Secure` 在生产默认开启；`--dev` 或回环 `site_url` 会自动关闭，方便本地 HTTP 调试。
- 角色 `role` 目前只返回给前端展示，**服务端没有按 role 做只读限制**；不要把它当作权限边界。

### 3.2 管理端 IP 白名单

配置 `admin_allow_ips` 非空时，`/api/admin/*` 只允许精确 IP 或 CIDR：

```json
"admin_allow_ips": ["10.0.0.0/8", "203.0.113.10"]
```

- 在反向代理后必须设置 `trust_proxy: true`，才会读取 `X-Forwarded-For` 的第一个地址；只有可信代理才能开启，否则客户端可以伪造来源 IP。
- 白名单失败返回 `403 ip_rejected`，在登录校验之前发生。

---

## 4. 端到端流程

### 4.1 试用流程（首次启动）

```text
桌面 exe                         浏览器 / 授权门户                    门户服务端
   │ 首次启动生成 serial/machine      │                                  │
   │ 打开 /activate?serial=&machine=  │                                  │
   ├─────────────────────────────────►│ 表单预填 serial/machine           │
   │                                  │ 填邮箱/手机/姓名/个人或公司/同意   │
   │                                  ├─ POST /api/public/trial/apply ───►│
   │                                  │                                  │ 创建 application
   │                                  │                                  │ 发送 6 位验证码
   │                                  │◄─ {application_id, expires_in} ───┤
   │                                  ├─ POST /api/public/trial/verify ──►│
   │                                  │   {application_id, code}          │ 校验并签发试用授权
   │                                  │◄─ 试用码 + deep_link ─────────────┤
   │◄─────────────────────────────────┤ 浏览器可能唤起 pyshield://        │
   │ 轮询 /api/trial/status            │                                  │
   ├────────────────────────────────────────────────────────────────────►│
   │  status=granted 后 POST /api/trial/claim                            │
   ├────────────────────────────────────────────────────────────────────►│
   │◄──────────────── signed document + deep_link ──────────────────────┤
   │ 验签、校验机器码、写入本地 tool-license.json                        │
   ▼                                  │                                  │
```

关键点：

- 浏览器页面需要 `serial` 和 `machine` 才能提交；缺少时提示“从软件内打开本页面”。
- 同一序列号已绑定其他机器 → `serial_bound`；同一机器/同一邮箱或手机号已经完成过试用 → 拒绝重复试用。
- 重复提交试用申请会把该序列号旧的 `pending_email` 申请标记为 `superseded`，并发送新验证码。
- 邮箱验证码 10 分钟有效；错误 5 次后必须重新提交申请或重发验证码。
- `claim` 在授权有效时是幂等的：同一 serial + machine 重复领取返回同一份文档，`revision` 不变。
- 离线/浏览器无法唤起深链时，用户可把页面上的 `PST-...` 试用码或激活码填回客户端；客户端仍以轮询/领取结果为准。

### 4.2 付费激活流程（手动发码）

```text
用户                         运营/管理员                          门户服务端
 │ 打开 /upgrade?edition=pro     │                                   │
 ├──────────────────────────────►│ 人工沟通价格、收款                 │
 │ 微信转账                      │                                   │
 ├──────────────────────────────►│ 管理端 /admin/keys/new 生成激活码  │
 │                              │ POST /api/admin/keys              │
 │                              │◄─ 明文 key（仅此一次）─────────────┤
 │◄─────────────────────────────┤ 复制激活码发给用户                 │
 │ 客户端输入激活码               │                                   │
 ├──────────────────────────────────────────────────────────────────►│
 │                              │ POST /api/license/activate         │
 │                              │ {key, serial, machine}             │
 │◄──────────────────────────────── signed document ─────────────────┤
 │ 验签、写本地授权               │                                   │
 ▼                              │                                   │
```

- 激活码有“兑换期限”（`expires_at`，默认 30 天）和“激活后授权天数”（`days`，默认 365 天），两者不是一回事。
- 同一 `key + serial` 重复激活是幂等的；同一序列号换机器会被 `serial_bound` 拒绝；同一机器已有其他有效 key 会被 `other_key` 拒绝；已有管理员签发授权会被 `admin_license` 拒绝。
- 一个 key 可以被多个不同序列号使用，直到达到 `max_activations`；每个序列号仍只绑定一台机器。
- 用户也可以在网页 `/redeem?serial=&machine=` 兑换，适合客户端无法直接联网激活的场景，服务端返回同样的状态摘要（不返回签名文档）。

### 4.3 续费、升级与吊销

```text
管理端                              门户服务端                       客户端
  │ POST /api/admin/licenses/<serial>/renew     │                       │
  ├────────────────────────────────────────────►│ revision + 1          │
  │ POST /api/admin/licenses/<serial>/upgrade   │ 重新签发 document     │
  ├────────────────────────────────────────────►│                       │
  │ POST /api/admin/licenses/<serial>/revoke    │ status=revoked        │
  ├────────────────────────────────────────────►│ 写入吊销原因           │
  │                                             │                       │
  │                                             │◄─ POST /api/license/status
  │                                             │   {serial,machine,license_id,revision}
  │                                             ├─ 返回新 document（如有）
  │                                             │                       │ 验签后替换本地授权
  │                                             │                       │
  │                                             │◄─ GET /api/license/revocations
  │                                             ├─ 返回签名吊销列表
  │                                             │                       │ 安装吊销列表
```

- 续费/升级每次都会 `revision + 1` 并重新签名；客户端必须用 `license_id` + `revision` 判断是否需要更新。
- 续费：默认在当前到期日（未过期）基础上加 365 天；可显式指定 `expires`（不能早于今天）。
- 升级：可切换 `pro`/`enterprise`，可调整席位数；版本变化时 `license_id` 前缀变化。
- 吊销授权只改变服务端状态和吊销列表；已下载到本地的旧文档不会自动失效，离线客户端仍可能继续使用到到期。需要更强约束时，客户端应定期刷新并同步吊销列表。

### 4.4 当前没有推送/Webhook

门户不提供 webhook、消息推送或长连接；客户端通过轮询：

- 试用等待：建议每 3 秒查 `/api/trial/status`，最多 180 秒；
- 已激活：启动时、手动“刷新授权状态”时调用 `/api/license/status`；
- 吊销列表：建议每天或每次启动时下载 `/api/license/revocations`。

---

## 5. 健康检查与版本

### 5.1 `GET /health`

无需鉴权，用于负载均衡/监控。

```http
GET /health
```

```json
{
  "ok": true,
  "service": "pyshield-license-portal",
  "version": "0.1.0",
  "time": "2026-11-16T08:30:00+00:00"
}
```

### 5.2 `GET|HEAD /api/version`

```http
GET /api/version
```

```json
{"ok": true, "version": "0.1.0"}
```

桌面端可用于诊断；不要用它作为功能开关的唯一依据。

---

## 6. 桌面客户端稳定接口

> 这一节的 5 个路径是冻结契约，客户端二进制依赖它们。不要改路径、不要改方法、不要改字段语义。新增字段必须保持向后兼容。

### 6.1 `GET|POST /api/trial/status`

查询某序列号的试用/授权状态。GET 使用查询参数，POST 使用 JSON。

```http
GET /api/trial/status?serial=PS-AAAA-BBBB-CCCC&machine=0123456789abcdef0123456789abcdef
```

```http
POST /api/trial/status
Content-Type: application/json

{"serial": "PS-AAAA-BBBB-CCCC", "machine": "0123456789abcdef0123456789abcdef"}
```

成功响应（已签发）：

```json
{
  "ok": true,
  "serial": "PS-AAAA-BBBB-CCCC",
  "status": "granted",
  "edition": "trial",
  "expires": "2026-11-30",
  "max_builds": 3,
  "revision": 1,
  "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
  "trial_code": "PST-ABCD-EFGH-JKMN"
}
```

成功响应（未申请/无记录）：

```json
{"ok": true, "serial": "PS-AAAA-BBBB-CCCC", "status": "unknown"}
```

成功响应（已吊销）：

```json
{
  "ok": true,
  "serial": "PS-AAAA-BBBB-CCCC",
  "status": "rejected",
  "edition": "trial",
  "expires": "2026-11-30",
  "max_builds": 3,
  "revision": 2,
  "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
  "trial_code": "PST-ABCD-EFGH-JKMN",
  "reason": "管理员吊销"
}
```

状态映射：

| 数据库有效状态 | `/api/trial/status` |
| --- | --- |
| `active` | `granted` |
| `expired` | `expired` |
| `revoked` | `rejected` |
| 无记录 | `unknown` |

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | serial/machine 缺失或格式错误 |
| `403` | `machine_mismatch` | serial 存在，但绑定的机器码不同 |

客户端策略：

- `unknown`：继续等待或引导去申请；
- `granted`：调用 `/api/trial/claim`；
- `expired`：提示续费/购买；
- `rejected`：停止试用，展示 `reason`。

### 6.2 `POST /api/trial/claim`

领取已签名的授权文档。适用于试用和已签发的正式授权。

```http
POST /api/trial/claim
Content-Type: application/json

{"serial": "PS-AAAA-BBBB-CCCC", "machine": "0123456789abcdef0123456789abcdef"}
```

成功响应：

```json
{
  "ok": true,
  "serial": "PS-AAAA-BBBB-CCCC",
  "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
  "edition": "trial",
  "revision": 1,
  "expires": "2026-11-30",
  "max_builds": 3,
  "trial_code": "PST-ABCD-EFGH-JKMN",
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PST-ABCD-EFGH-JKMN",
  "document": {
    "format": "pyshield-tool-license/1",
    "payload": {
      "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
      "customer": "示例用户",
      "edition": "trial",
      "features": ["gui"],
      "seats": 1,
      "max_activations": 1,
      "max_builds_per_month": 3,
      "bind_mode": "fixed",
      "fingerprints": ["0123456789abcdef0123456789abcdef"],
      "activation_required": false,
      "issued": "2026-11-16",
      "not_before": "2026-11-16",
      "expires": "2026-11-30",
      "grace_days": 0,
      "revocation_epoch": 0,
      "revision": 1,
      "source": "trial",
      "serial": "PS-AAAA-BBBB-CCCC",
      "signing_key_id": "2026-11-16-xxxx",
      "notes": "trial application 12"
    },
    "signature": "<Base85 Ed25519 签名>"
  }
}
```

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | serial/machine 缺失或格式错误 |
| `404` | `license_not_found` | 该序列号还没有授权记录 |
| `403` | `machine_mismatch` | 机器码与授权记录不一致 |
| `403` | `license_revoked` | 授权已吊销 |
| `403` | `license_expired` | 授权已过期（含宽限期后） |

说明：

- 有效授权重复领取是幂等的，返回同一份文档。
- `deep_link` 是客户端深链，不是服务端回调；桌面端应以本地 `serial` 校验后再轮询/领取，不要仅凭深链里的 `code` 直接安装授权。
- 正式 key/管理员签发的授权也可能返回 `trial_code`/`deep_link`，其中 `trial_code` 可能只是展示用前缀；真正可信的是 `document`。

### 6.3 `POST /api/license/activate`

兑换专业版/企业版激活码。

```http
POST /api/license/activate
Content-Type: application/json

{
  "key": "PSP-ABCD-EFGH-JKMN-PQRS",
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef"
}
```

成功响应与 `claim` 类似，但 `status` 固定为 `granted`，并额外带 `verification_required: false`：

```json
{
  "ok": true,
  "status": "granted",
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef",
  "license_id": "PRO-PS-AAAA-BBBB-CCCC",
  "edition": "pro",
  "expires": "2027-11-16",
  "max_builds": 100,
  "revision": 1,
  "trial_code": "PSP-ABCD",
  "document": {"format": "pyshield-tool-license/1", "payload": {}, "signature": "..."},
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PSP-ABCD",
  "verification_required": false
}
```

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | key 格式错误、serial/machine 缺失或格式错误 |
| `400` | `invalid_key` | key 格式正确但不存在 |
| `400` | `invalid_edition` | 用了 trial 激活码，不能走此流程 |
| `403` | `key_revoked` | 激活码被吊销 |
| `403` | `key_expired` | 激活码超过兑换期限 |
| `409` | `serial_bound` | 序列号已绑定其他机器 |
| `409` | `other_key` | 本机已有其他有效激活码授权 |
| `409` | `admin_license` | 本机已有管理员签发授权（不区分是否过期/吊销） |
| `409` | `activation_limit` | 激活码达到最大激活设备数 |
| `403` | `license_revoked` | 同一激活码签发的授权已吊销（换新码可替换） |
| `403` | `license_expired` | 同一激活码签发的授权已过期（换新码可替换） |
| `429` | `rate_limited` | 超过 key 激活限流 |

> 已过期或已吊销的 key 授权，可以被**新的**激活码替换；替换后 `revision` 递增，`expires` 从今天重新计算。管理员签发的授权不适用该规则：只要 `source=admin`，无论当前状态都会返回 `admin_license`，只能由管理端续费/升级；已吊销的管理员授权无法恢复。

### 6.4 `POST /api/license/status`

客户端刷新授权状态，并在需要时取回新文档。

```http
POST /api/license/status
Content-Type: application/json

{
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef",
  "license_id": "PRO-PS-AAAA-BBBB-CCCC",
  "revision": 1
}
```

成功响应（已是最新，不带文档）：

```json
{
  "ok": true,
  "status": "active",
  "serial": "PS-AAAA-BBBB-CCCC",
  "license_id": "PRO-PS-AAAA-BBBB-CCCC",
  "edition": "pro",
  "expires": "2027-11-16",
  "revision": 1,
  "max_builds": 100,
  "trial_code": "PSP-ABCD",
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PSP-ABCD",
  "checked": "2026-11-16T08:35:00+00:00"
}
```

成功响应（有更新，带新文档）：

```json
{
  "ok": true,
  "status": "active",
  "serial": "PS-AAAA-BBBB-CCCC",
  "license_id": "PRO-PS-AAAA-BBBB-CCCC",
  "edition": "pro",
  "expires": "2028-11-16",
  "revision": 2,
  "max_builds": 100,
  "trial_code": "PSP-ABCD",
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PSP-ABCD",
  "document": {"format": "pyshield-tool-license/1", "payload": {}, "signature": "..."},
  "checked": "2026-11-16T08:35:00+00:00"
}
```

未找到授权：

```json
{"ok": true, "status": "unknown", "serial": "PS-AAAA-BBBB-CCCC"}
```

状态：

- `active`：有效（包含宽限期内的授权；`expires` 仍是原到期日）。
- `expired`：已过 `expires + grace_days`，并带 `reason`。
- `revoked`：已吊销，并带 `reason`。
- `unknown`：没有该序列号的授权记录。

服务端只在 `status == "active"` 且满足以下任一条件时返回新 `document`：

1. 客户端传入的 `license_id` 与服务端不同；
2. 客户端传入的 `revision` 小于服务端 `revision`。

`expired` / `revoked` 状态只返回状态和原因，不会返回文档。

因此客户端首次刷新可以传 `license_id: ""`、`revision: 0`，也可以省略这两个字段；已经是最新时不要反复替换本地授权。

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | serial/machine 缺失或格式错误；`revision` 不是整数 |
| `403` | `machine_mismatch` | 机器码与授权记录不一致 |

### 6.5 `GET /api/license/revocations`

返回签名吊销列表文档。该接口不需要参数、不需要鉴权。

```http
GET /api/license/revocations
```

```json
{
  "format": "pyshield-revocations/1",
  "payload": {
    "issued": "2026-11-16T08:36:00+00:00",
    "epoch": 1,
    "revoked": [
      "PRO-PS-AAAA-BBBB-CCCC",
      "TRIAL-PS-DDDD-EEEE-FFFF"
    ],
    "reason": "license portal revocation list"
  },
  "signature": "<Base85 Ed25519 签名>"
}
```

客户端应：

1. 用内置根公钥按 `pyshield-revocations/1` 的签名前缀校验；
2. 保存 `revoked` 集合；
3. 每次 `check()` 时拒绝 `license_id` 在集合中的授权。

注意：

- 当前 `epoch` 固定为 `1`，`revocation_epoch` 在授权文档中固定为 `0`；**不要用 epoch 做增量同步**，以每次返回的完整 `revoked` 列表为准。
- 吊销列表只包含 `license_id`，不包含机器码或个人信息。
- 离线客户端不会自动知道新吊销；需要定期联网下载。

---

## 7. 浏览器公共 API

这些接口供门户 React SPA 使用。全部同源调用；写操作校验 `Origin`/`Referer`，并受 Turnstile 和限流保护。所有写接口都可能额外返回 `403 origin_rejected`（来源与 `site_url` 不一致），下文不再逐表重复。

### 7.1 `GET /api/public/config`

```http
GET /api/public/config
```

```json
{
  "ok": true,
  "site_name": "PyShield 授权中心",
  "support_email": "help@example.com",
  "contact_wechat": "wx-help",
  "trial_days": 14,
  "trial_builds": 3,
  "captcha_site_key": "",
  "email_verification": true,
  "key_redemption_days": 30,
  "editions": [
    {"edition": "trial", "name": "免费版", "note": "评估加密效果与集成流程"},
    {"edition": "pro", "name": "专业版", "note": "独立开发者与小团队交付"},
    {"edition": "enterprise", "name": "企业版", "note": "多席位团队与离线交付"}
  ],
  "version": "0.1.0",
  "server_time": "2026-11-16T08:30:00+00:00"
}
```

- `captcha_site_key` 只有在 Turnstile 的 Site Key 和 Secret 都配置时才非空。
- 站点名、客服微信、支持邮箱都来自配置，不应硬编码在前端。
- `server_time` 可用于提示客户端时钟偏差。

### 7.2 `POST /api/public/trial/apply`

提交试用申请。验证码开启时返回 `application_id`；关闭邮箱验证时直接签发并返回授权摘要。

```http
POST /api/public/trial/apply
Content-Type: application/json

{
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef",
  "email": "user@example.com",
  "phone": "13800138000",
  "name": "张三",
  "org_type": "individual",
  "company_name": "",
  "consent": true,
  "captcha_token": "",
  "website": ""
}
```

字段规则：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `serial` | 是 | `PS-XXXX-XXXX-XXXX` |
| `machine` | 是 | 32 位十六进制 |
| `email` | 是 | 合法邮箱，最长 254 |
| `phone` | 是 | 带 `+` 的 8–15 位国际号码；不带 `+` 的 11–15 位数字（11 位须为中国大陆手机号） |
| `name` | 是 | 2–40 字符 |
| `org_type` | 是 | `individual` 或 `company` |
| `company_name` | 公司时必填 | 2–100 字符；个人时服务端忽略 |
| `consent` | 是 | `true`/`1`/`yes`/`on`/`同意` |
| `captcha_token` | 配置 Turnstile 时必填 | Turnstile 客户端 token |
| `website` | 否 | 蜜罐字段；任何非空值都返回 `spam_rejected` |

> `edition` 字段当前会被忽略；试用申请固定签发 `trial`。需要升级请走 `/api/public/upgrade`。

验证码开启时的成功响应：

```json
{
  "ok": true,
  "verification_required": true,
  "application_id": 42,
  "email": "u***@example.com",
  "expires_in": 600
}
```

邮箱验证关闭时的成功响应：直接返回 `PublicLicense`，`verification_required` 为 `false`，无 `application_id`：

```json
{
  "ok": true,
  "status": "granted",
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef",
  "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
  "edition": "trial",
  "expires": "2026-11-30",
  "max_builds": 3,
  "revision": 1,
  "trial_code": "PST-ABCD-EFGH-JKMN",
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PST-ABCD-EFGH-JKMN",
  "already": false,
  "reason": "",
  "verification_required": false
}
```

常见错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | 字段缺失/格式错误；`errors` 给出字段 |
| `400` | `captcha_failed` | Turnstile 校验失败 |
| `400` | `spam_rejected` | 蜜罐字段非空 |
| `403` | `origin_rejected` | Origin/Referer 与 `site_url` 不一致 |
| `403` | `trial_revoked` | 该设备试用已被吊销 |
| `409` | `serial_bound` | 序列号已绑定其他设备 |
| `409` | `trial_expired` | 该设备试用已结束 |
| `409` | `already_licensed` | 本机已有正式授权记录（不区分是否过期/吊销） |
| `409` | `machine_trial_exists` | 本机已申请过试用 |
| `409` | `customer_trial_exists` | 邮箱/手机号已申请过试用 |
| `409` | `customer_conflict` | 邮箱和手机号属于不同账户 |
| `429` | `rate_limited` | 超过 IP/邮箱/手机号限流 |
| `502` | `mail_failed` | 邮件发送失败 |

### 7.3 `GET /api/public/trial/application?id=<application_id>`

验证码页面使用的元信息，不返回验证码本身。

```http
GET /api/public/trial/application?id=42
```

```json
{
  "ok": true,
  "application_id": 42,
  "email": "u***@example.com",
  "status": "pending_email",
  "edition": "trial",
  "serial": "PS-AAAA-BBBB-CCCC",
  "updated_at": "2026-11-16T08:30:00+00:00",
  "code_expires": "2026-11-16T08:40:00+00:00"
}
```

`status` 可能为 `pending_email`、`verified`、`superseded`。错误：

- `id` 不是整数 → `400 invalid_application`；
- 记录不存在或 `id <= 0` → `404 application_not_found`。

### 7.4 `POST /api/public/trial/verify`

校验邮箱验证码并签发试用授权。

```http
POST /api/public/trial/verify
Content-Type: application/json

{"application_id": 42, "code": "123456"}
```

成功响应为 `PublicLicense`（不含签名文档）：

```json
{
  "ok": true,
  "status": "granted",
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef",
  "license_id": "TRIAL-PS-AAAA-BBBB-CCCC",
  "edition": "trial",
  "expires": "2026-11-30",
  "max_builds": 3,
  "revision": 1,
  "trial_code": "PST-ABCD-EFGH-JKMN",
  "deep_link": "pyshield://activate?serial=PS-AAAA-BBBB-CCCC&code=PST-ABCD-EFGH-JKMN",
  "already": false,
  "reason": "",
  "verification_required": false
}
```

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | `application_id` 或 6 位验证码格式错误 |
| `400` | `code_invalid` | 验证码不匹配，`attempts` 递增 |
| `404` | `application_not_found` | 申请不存在 |
| `409` | `application_not_pending` | 申请已失效/已被替代 |
| `409` | `code_expired` | 验证码过期 |
| `429` | `too_many_attempts` | 错误次数达到 5 次 |
| `429` | `rate_limited` | 验证 IP 超过 30 次/小时 |

已完成的申请再次 verify：如果授权存在，返回 `already: true`；如果授权记录缺失，返回 `500`（需要管理员排查）。

### 7.5 `POST /api/public/trial/resend`

重发邮箱验证码。旧验证码立即失效。

```http
POST /api/public/trial/resend
Content-Type: application/json

{"application_id": 42}
```

```json
{
  "ok": true,
  "application_id": 42,
  "email": "u***@example.com",
  "verification_required": true,
  "expires_in": 600,
  "resend_after": 60
}
```

- `resend_after: 60` 是前端倒计时提示，不是服务端拒绝条件；服务端实际限制是 `resend-app` 5 次/小时、`resend-ip` 10 次/小时。
- 重发会把验证码尝试次数重置为 0。
- 已完成验证的申请返回 `409 application_verified`；已失效返回 `409 application_not_pending`。

错误：

| HTTP | `code` | 场景 |
| --- | --- | --- |
| `400` | `validation_failed` | `application_id` 缺失或不是正整数 |
| `404` | `application_not_found` | 申请不存在 |
| `409` | `application_verified` | 申请已完成验证，无需重发 |
| `409` | `application_not_pending` | 申请已失效/被替代 |
| `429` | `rate_limited` | 超过 IP 或申请维度重发限流 |
| `502` | `mail_failed` | 邮件发送失败 |

### 7.6 `GET /api/public/license?serial=&machine=`

成功页/详情页读取授权摘要。**不返回签名文档。**

```http
GET /api/public/license?serial=PS-AAAA-BBBB-CCCC&machine=0123456789abcdef0123456789abcdef
```

响应字段与 `PublicLicense` 一致；`status` 可能为 `active`、`expired`、`revoked`、`unknown`，也可能为空字符串。`document` 永远不会出现。

错误：`400 validation_failed`（serial/machine 格式错误）、`403 machine_mismatch`（机器码不匹配）。无记录不是错误，返回 `200` + `status=unknown`。

### 7.7 `POST /api/public/upgrade`

创建购买/升级意向，写入 `orders`，不直接签发授权。

```http
POST /api/public/upgrade
Content-Type: application/json

{
  "edition": "pro",
  "serial": "PS-AAAA-BBBB-CCCC",
  "contact": "微信或邮箱",
  "notes": "希望支持离线部署"
}
```

字段：

- `edition`: `pro` 或 `enterprise`；
- `serial`: 可选，非空时必须是合法序列号；
- `contact`: 2–120 字符，必填；
- `notes`: 0–1000 字符，可含换行。

响应：

```json
{
  "ok": true,
  "order_id": 7,
  "edition": "pro",
  "contact_wechat": "wx-help"
}
```

限流：每个 IP 5 次/小时。错误 `validation_failed`、`rate_limited`。

### 7.8 `POST /api/public/redeem`

网页兑换激活码。请求和错误与桌面端 `/api/license/activate` 相同，但响应会去掉 `document`。

```http
POST /api/public/redeem
Content-Type: application/json

{
  "key": "PSP-ABCD-EFGH-JKMN-PQRS",
  "serial": "PS-AAAA-BBBB-CCCC",
  "machine": "0123456789abcdef0123456789abcdef"
}
```

响应为 `PublicLicense`，不包含 `document`；页面只展示状态、到期日、license_id 和试用码/深链。除激活码本身的错误外，浏览器调用还可能返回 `403 origin_rejected`。

### 7.9 浏览器页面路由

| 路由 | 用途 |
| --- | --- |
| `/` | 首页 |
| `/activate?serial=&machine=&edition=` | 试用申请；缺少 serial/machine 时提示从客户端打开 |
| `/verify?application=<id>` | 邮箱验证码页 |
| `/success?serial=&machine=` | 签发成功页，展示试用码和深链 |
| `/upgrade?edition=pro\|enterprise&serial=` | 购买/升级意向 |
| `/redeem?serial=&machine=` | 网页兑换激活码 |

这些路由由 SPA 的 history fallback 提供；Nginx 必须把未知路径回退到 `index.html`。

---

## 8. 管理端 API

### 8.1 登录与退出

#### `POST /api/admin/login`

无需 CSRF、无需会话；受 IP/用户名限流。

```http
POST /api/admin/login
Content-Type: application/json

{"username": "admin", "password": "<管理员密码>"}
```

成功响应会同时设置会话 Cookie：

```json
{
  "ok": true,
  "admin": {"username": "admin", "role": "admin"},
  "csrf_token": "<csrf token>",
  "expires": "2026-11-16T20:30:00+00:00"
}
```

错误：`400 validation_failed`、`401 login_failed`、`403 ip_rejected`、`403 origin_rejected`、`429 rate_limited`。
浏览器登录仍是同源 POST；`curl` 不带 `Origin`/`Referer` 可以调用。

#### `GET /api/admin/me`

需要会话；用于刷新页面后重新获取 `csrf_token`。

```json
{
  "ok": true,
  "admin": {"username": "admin", "role": "admin"},
  "csrf_token": "<csrf token>",
  "expires": "2026-11-16T20:30:00+00:00"
}
```

未登录/过期：`401 unauthorized`。

#### `POST /api/admin/logout`

需要会话和 CSRF；删除服务端会话并清 Cookie。

```json
{"ok": true}
```

### 8.2 概览与筛选

#### `GET /api/admin/dashboard`

```json
{
  "ok": true,
  "counts": {
    "licenses": 12,
    "trials": 5,
    "pro": 6,
    "enterprise": 1,
    "revoked": 1,
    "expired": 2,
    "customers": 10,
    "keys_unused": 20,
    "keys_active": 3,
    "orders_pending": 2,
    "expiring_30d": 4
  },
  "expiring": [
    {
      "serial": "PS-AAAA-BBBB-CCCC",
      "license_id": "PRO-PS-AAAA-BBBB-CCCC",
      "edition": "pro",
      "expires": "2026-12-01",
      "effective_status": "active",
      "features": ["gui", "native-runtime", "license-issuance"]
    }
  ],
  "signing_key": {
    "key_id": "2026-11-16-xxxx",
    "public_key": "<Base85 公钥>",
    "fingerprint": "<指纹>",
    "created": "2026-11-16T08:00:00+00:00"
  },
  "email": {
    "verification": true,
    "configured": true,
    "sender": "help@example.com",
    "from_name": "PyShield 授权中心",
    "reply_to": "help@example.com",
    "host": "smtp.example.com"
  }
}
```

`expiring` 只包含未吊销且 30 天内到期的授权，最多 100 条。

#### `GET /api/admin/filters`

```json
{
  "ok": true,
  "editions": ["trial", "pro", "enterprise"],
  "license_statuses": ["active", "expired", "revoked"],
  "key_statuses": ["unused", "active", "revoked", "expired"],
  "order_statuses": ["pending", "paid", "fulfilled", "cancelled"],
  "audit_actions": ["trial_grant", "key_activate", "license_renew"]
}
```

### 8.3 授权管理

#### `GET /api/admin/licenses?q=&edition=&status=`

- `q`：匹配 serial、license_id、客户邮箱、手机号、姓名、公司名；
- `edition`：`trial`/`pro`/`enterprise`；
- `status`：按 `effective_status` 过滤 `active`/`expired`/`revoked`。

```json
{
  "ok": true,
  "items": [
    {
      "id": 1,
      "serial": "PS-AAAA-BBBB-CCCC",
      "machine": "0123456789abcdef0123456789abcdef",
      "license_id": "PRO-PS-AAAA-BBBB-CCCC",
      "edition": "pro",
      "seats": 1,
      "max_activations": 1,
      "max_builds_per_month": 100,
      "grace_days": 14,
      "issued": "2026-11-16",
      "not_before": "2026-11-16",
      "expires": "2027-11-16",
      "revision": 1,
      "status": "active",
      "effective_status": "active",
      "source": "key",
      "display_code": "PSP-ABCD",
      "customer_email": "user@example.com",
      "customer_name": "张三",
      "customer_company": null,
      "features": ["gui", "native-runtime", "license-issuance"],
      "key_id": 3
    }
  ],
  "total": 1
}
```

列表最多返回 500 行；`total` 是本次返回条数（同样受 500 上限约束），不是数据库总数。列表不返回 `document`、`fingerprints` 或 `customer_org_type`；这些只在详情接口返回。

#### `GET /api/admin/licenses/<serial>`

返回授权详情、激活记录和最近 50 条事件：

```json
{
  "ok": true,
  "license": {
    "serial": "PS-AAAA-BBBB-CCCC",
    "license_id": "PRO-PS-AAAA-BBBB-CCCC",
    "edition": "pro",
    "expires": "2027-11-16",
    "revision": 1,
    "effective_status": "active",
    "features": ["gui", "native-runtime", "license-issuance"],
    "fingerprints": ["0123456789abcdef0123456789abcdef"],
    "activation_required": false,
    "has_document": true,
    "customer_email": "user@example.com",
    "customer_name": "张三",
    "customer_org_type": "individual",
    "customer_company": null,
    "activations": [
      {
        "id": 1,
        "key_id": 3,
        "serial": "PS-AAAA-BBBB-CCCC",
        "machine": "0123456789abcdef0123456789abcdef",
        "license_id": "PRO-PS-AAAA-BBBB-CCCC",
        "activated_at": "2026-11-16T08:32:00+00:00"
      }
    ],
    "events": [
      {
        "id": 10,
        "at": "2026-11-16T08:32:00+00:00",
        "actor_type": "client",
        "actor": "desktop",
        "action": "key_activate",
        "serial": "PS-AAAA-BBBB-CCCC",
        "license_id": "PRO-PS-AAAA-BBBB-CCCC",
        "key_prefix": "PSP-ABCD",
        "detail": {"edition": "pro", "revision": 1}
      }
    ]
  }
}
```

不返回签名文档全文；只返回 `has_document`。需要完整文档时通过桌面端接口或数据库备份获取。

#### `POST /api/admin/licenses/<serial>/renew`

续费。需要 CSRF。

```json
{
  "days": 365,
  "expires": "2028-11-16",
  "max_builds": 200,
  "grace_days": 14,
  "notes": "老客户续费"
}
```

- `days`：1–3650，默认 365；当 `expires` 为空时，从当前到期日（未过期）或今天起算。
- `expires`：可选，`YYYY-MM-DD`，不能早于今天；提供时优先于 `days`。
- `max_builds`：0–100000，0 表示不限；不传保持原值。
- `grace_days`：0–3650；不传保持原值。
- `notes`：最多 500 字符。

响应：

```json
{
  "ok": true,
  "license": {"serial": "PS-AAAA-BBBB-CCCC", "revision": 2, "expires": "2028-11-16", "effective_status": "active"},
  "serial": "PS-AAAA-BBBB-CCCC",
  "action": "renew"
}
```

`license` 是详情摘要（不含 `document`），会 `revision + 1`。已吊销的授权不能续费：`409 license_revoked`。

#### `POST /api/admin/licenses/<serial>/upgrade`

升级版本/席位。需要 CSRF。

```json
{
  "edition": "enterprise",
  "days": 365,
  "expires": "2028-11-16",
  "seats": 5,
  "max_builds": 500,
  "grace_days": 14,
  "notes": "升级企业版"
}
```

- `edition` 必填，`pro` 或 `enterprise`。
- 版本变化时到期日从今天起算；相同版本时从当前到期日（未过期）起算。
- `seats` 可选，1–10000；提供时 `max_activations` 至少提升到 `seats`。
- 版本变化会改变 `license_id` 前缀（如 `TRIAL-...` → `ENT-...`），`revision + 1`。
- 响应结构同续费，`action: "upgrade"`。

错误：`404 license_not_found`、`409 license_revoked`、`400 validation_failed`。

#### `POST /api/admin/licenses/<serial>/revoke`

吊销授权。需要 CSRF。

```json
{"reason": "退款/滥用"}
```

```json
{
  "ok": true,
  "license": {"serial": "PS-AAAA-BBBB-CCCC", "effective_status": "revoked", "revoked_reason": "退款/滥用"},
  "serial": "PS-AAAA-BBBB-CCCC",
  "action": "revoke"
}
```

- `reason` 可选，最多 500 字符；为空时写入“管理员吊销”。
- 吊销是状态变更，不重新签名旧文档；客户端通过 `/api/license/status` 或吊销列表获知。
- 重复吊销状态结果相同，但每次都会写审计事件。

### 8.4 激活码管理

#### `GET /api/admin/keys?q=&status=&edition=`

```json
{
  "ok": true,
  "items": [
    {
      "id": 3,
      "key_prefix": "PSP-ABCD",
      "edition": "pro",
      "days": 365,
      "max_builds_per_month": 100,
      "seats": 1,
      "max_activations": 1,
      "grace_days": 14,
      "features": ["gui", "native-runtime", "license-issuance"],
      "notes": "",
      "batch_id": 1,
      "status": "active",
      "effective_status": "active",
      "expires_at": "2026-12-16",
      "created_at": "2026-11-16T08:31:00+00:00",
      "created_by": "admin",
      "activated_count": 1
    }
  ],
  "total": 1
}
```

- `q` 匹配前缀或备注；`status` 支持 `unused`/`active`/`revoked`/`expired`；`edition` 支持版本过滤。
- 列表不返回 `key_hash`、`key_encrypted`，也不返回完整激活码。
- 最多 500 行。

#### `POST /api/admin/keys`

批量生成激活码。需要 CSRF。成功返回 `201`。

```json
{
  "edition": "pro",
  "quantity": 5,
  "days": 365,
  "max_builds": 100,
  "seats": 1,
  "max_activations": 1,
  "grace_days": 14,
  "redemption_days": 30,
  "batch_name": "2026-11 老客户",
  "notes": "微信收款",
  "features": "gui,native-runtime,license-issuance"
}
```

字段范围：

| 字段 | 默认 | 范围 |
| --- | --- | --- |
| `edition` | `pro` | 内部版本集合；业务只发 `pro`/`enterprise` |
| `quantity` | 1 | 1–200 |
| `days` | 365 | 1–3650 |
| `max_builds` | 100 | 0–100000，0 不限 |
| `seats` | 1 | 1–10000 |
| `max_activations` | 1 | 1–10000 |
| `grace_days` | 14 | 0–3650 |
| `redemption_days` | `key_redemption_days`（默认 30） | 1–3650 |
| `batch_name` | 空 | 0–100 字符；填写或 `quantity > 1` 时创建批次，否则 `batch_id` 为 `null` |
| `notes` | 空 | 0–500 字符 |
| `features` | 版本默认 | 逗号/分号分隔，或 `*` |

响应：

```json
{
  "ok": true,
  "batch_id": 1,
  "keys": [
    {
      "id": 3,
      "key": "PSP-ABCD-EFGH-JKMN-PQRS",
      "prefix": "PSP-ABCD",
      "edition": "pro",
      "days": 365,
      "max_builds": 100,
      "seats": 1,
      "max_activations": 1,
      "expires_at": "2026-12-16"
    }
  ]
}
```

> **明文激活码只在这里返回一次。** 之后的列表只显示前缀；`/reveal` 可以再次解密，但会写审计日志。生成后应立即通过安全渠道交付用户，不要把明文写进普通日志、工单或仓库。

#### `GET /api/admin/keys/<id>`

返回激活码详情和激活记录。`key` 对象字段与列表相同，并额外带：

```json
{
  "ok": true,
  "key": {
    "id": 3,
    "key_prefix": "PSP-ABCD",
    "activations": [
      {
        "id": 1,
        "key_id": 3,
        "serial": "PS-AAAA-BBBB-CCCC",
        "machine": "0123456789abcdef0123456789abcdef",
        "license_id": "PRO-PS-AAAA-BBBB-CCCC",
        "activated_at": "2026-11-16T08:32:00+00:00"
      }
    ]
  }
}
```

#### `POST /api/admin/keys/<id>/reveal`

需要 CSRF；无请求体。返回明文激活码：

```json
{"ok": true, "id": 3, "key": "PSP-ABCD-EFGH-JKMN-PQRS"}
```

会写 `key_reveal` 审计事件。错误：`404 key_not_found`、`500 key_decrypt_failed`。

#### `POST /api/admin/keys/<id>/revoke`

吊销激活码，同时把使用该 key 的授权置为 `revoked`。需要 CSRF。

```json
{"reason": "退款"}
```

```json
{"ok": true, "id": 3, "status": "revoked"}
```

错误：`404 key_not_found`。

### 8.5 客户、订单、审计、签发信息

#### `GET /api/admin/customers?q=`

```json
{
  "ok": true,
  "items": [
    {
      "id": 1,
      "email": "user@example.com",
      "phone": "13800138000",
      "name": "张三",
      "org_type": "individual",
      "company_name": "",
      "consent_at": "2026-11-16T08:30:00+00:00",
      "created_at": "2026-11-16T08:30:00+00:00",
      "updated_at": "2026-11-16T08:32:00+00:00",
      "license_count": 1,
      "last_license_at": "2026-11-16T08:32:00+00:00"
    }
  ],
  "total": 1
}
```

`q` 匹配邮箱、手机号、姓名、公司名。

#### `GET /api/admin/customers/<id>`

```json
{
  "ok": true,
  "customer": {"id": 1, "email": "user@example.com", "phone": "13800138000"},
  "licenses": [{"serial": "PS-AAAA-BBBB-CCCC", "license_id": "PRO-PS-AAAA-BBBB-CCCC", "effective_status": "active"}],
  "orders": [{"id": 7, "status": "paid", "edition": "pro"}],
  "applications": [
    {
      "id": 42,
      "serial": "PS-AAAA-BBBB-CCCC",
      "machine": "0123456789abcdef0123456789abcdef",
      "edition": "trial",
      "status": "verified",
      "code_expires": "2026-11-16T08:40:00+00:00",
      "created_at": "2026-11-16T08:30:00+00:00",
      "updated_at": "2026-11-16T08:31:00+00:00"
    }
  ]
}
```

不会返回验证码哈希、邮箱验证码或完整激活码。

#### `GET /api/admin/orders?status=`

```json
{
  "ok": true,
  "items": [
    {
      "id": 7,
      "customer_id": 1,
      "serial": "PS-AAAA-BBBB-CCCC",
      "kind": "upgrade",
      "edition": "pro",
      "status": "pending",
      "contact": "wx-user",
      "notes": "希望支持离线部署",
      "amount": "",
      "handled_by": "",
      "created_at": "2026-11-16T08:33:00+00:00",
      "updated_at": "2026-11-16T08:33:00+00:00",
      "customer_email": "user@example.com",
      "customer_phone": "13800138000",
      "customer_name": "张三",
      "customer_company": null
    }
  ],
  "total": 1
}
```

#### `POST /api/admin/orders/<id>/status`

需要 CSRF。

```json
{"status": "paid", "amount": "399.00"}
```

- `status`: `pending`/`paid`/`fulfilled`/`cancelled`。
- `amount` 可选，空值时保留原金额；最多 80 字符。

```json
{"ok": true, "order_id": 7, "status": "paid"}
```

错误：`400 invalid_order_status`、`404 order_not_found`。

#### `GET /api/admin/audit?action=&q=&limit=`

```json
{
  "ok": true,
  "items": [
    {
      "id": 10,
      "at": "2026-11-16T08:32:00+00:00",
      "actor_type": "admin",
      "actor": "admin",
      "action": "key_create",
      "serial": "",
      "license_id": "",
      "key_prefix": "PSP-ABCD",
      "ip_hash": "...",
      "detail": {"quantity": 5, "edition": "pro"}
    }
  ],
  "total": 1
}
```

- `action` 精确匹配；`q` 匹配 serial、license_id、key_prefix、actor。
- `limit` 默认 200，范围 1–1000。
- `detail` 如果是 JSON 字符串会解析为对象；否则原样返回。

#### `GET /api/admin/signing`

```json
{
  "ok": true,
  "signing_key": {
    "key_id": "2026-11-16-xxxx",
    "public_key": "<Base85 公钥>",
    "fingerprint": "<指纹>",
    "created": "2026-11-16T08:00:00+00:00"
  },
  "build_command": "python tools/build_commercial.py build --public-key <root.pub> --site https://你的域名",
  "prepare_command": "python tools/build_commercial.py prepare --public-key root.pub"
}
```

公钥可以公开并注入客户端；私钥、口令、`signing.key` 永远不能出现在前端响应或仓库中。

---

## 9. 签名授权文档

### 9.1 文档结构

所有可安装的授权都是同一个外层结构：

```json
{
  "format": "pyshield-tool-license/1",
  "payload": {},
  "signature": "<Base85(Ed25519 signature)>"
}
```

`payload` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `license_id` | string | 必填，授权主体标识 |
| `customer` | string | 客户名/公司名，可为空 |
| `edition` | string | `trial`/`pro`/`enterprise`（门户业务） |
| `features` | string[] | 功能白名单；`*` 表示全部 |
| `seats` | int | 席位数，至少 1 |
| `max_activations` | int | 最大激活设备数，至少 1 |
| `max_builds_per_month` | int | 每月构建配额；`0` 表示不限 |
| `bind_mode` | string | 门户固定为 `fixed` |
| `fingerprints` | string[] | 机器码列表；固定绑定至少一个 |
| `activation_required` | bool | 门户固定为 `false` |
| `issued` | string | 签发日期 `YYYY-MM-DD` |
| `not_before` | string | 生效日期 |
| `expires` | string\|null | 到期日期 |
| `grace_days` | int | 到期后的宽限天数，0–3650 |
| `revocation_epoch` | int | 当前固定为 0；不要依赖它做增量 |
| `revision` | int | 授权修订号，续费/升级递增 |
| `source` | string | `trial`/`key`/`admin` |
| `serial` | string | 序列号 |
| `signing_key_id` | string | 签发密钥标识 |
| `notes` | string | 备注，可为空 |

> `payload` 里即使出现 `signature` 字段，也不参与签名；规范做法是保持 payload 不含 `signature`。

### 9.2 签名算法

签名前先做规范化 JSON：

```python
unsigned = {k: v for k, v in payload.items() if k != "signature"}
message = (
    b"pyshield-tool-license-v1:"
    + json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
)
signature = ed25519.sign(signing_key_seed, message)
document = {
    "format": "pyshield-tool-license/1",
    "payload": payload,
    "signature": base64.b85encode(signature).decode("ascii"),
}
```

要点：

- `sort_keys=True`，键按字典序排序；
- 分隔符是紧凑的 `(",", ":")`，不能有空格；
- `ensure_ascii=True`，非 ASCII 字符转义为 `\uXXXX`；
- 签名前缀是域分离字符串 `pyshield-tool-license-v1:`；
- 签名编码是 Python `base64.b85encode` 的 ASCII 结果，不是 Base64。

吊销列表使用相同的 `sign_document` 结构，但：

- `format`: `pyshield-revocations/1`
- 签名前缀：`pyshield-revocations-v1:`
- payload：`issued`、`epoch`、`revoked`（license_id 数组）、`reason`

### 9.3 客户端验签

推荐直接使用仓库内已有的实现，不要自己重写：

```python
from pyshield import tool_license, trial_license

# 1) 验证签名并解析
key = tool_license.active_public_key()
payload = tool_license.verify_document(
    document,
    key,
    tool_license.TOOL_LICENSE_FORMAT,
)
entitlement = tool_license.parse_entitlement(payload)

# 2) 业务校验
assert entitlement.bind_mode == tool_license.BIND_FIXED
assert entitlement.machine_allowed(machine)
assert not entitlement.activation_required
active, reason = entitlement.active_on(today)
assert active, reason

# 3) 安装为本地授权（会再次验签）
trial_license.apply_document(document)
```

`trial_license` 还提供了更高层的封装：

```python
# 试用/领取
entitlement = trial_license.claim_and_apply(server)

# 激活码
entitlement = trial_license.activate_key(server, key)

# 刷新（服务端有更新时才替换）
result = trial_license.refresh_license(server)
```

`apply_document` 会检查：

- 签名有效；
- `format` 正确；
- `bind_mode == "fixed"`；
- 当前机器在 `fingerprints` 中；
- `activation_required == false`；
- 然后把文档写入 `tool-license.json`（权限受限）。

没有内置公钥时，生产环境必须报错；只有在显式设置 `PYSHELD_DEV=1` 时才允许跳过验签，**生产禁止设置**。

### 9.4 本地授权与配额

- 本地授权文件：`<state_dir>/tool-license.json`；Windows 默认 `%APPDATA%/PyShield`，其他平台 `~/.pyshield`。
- 构建配额：`quota.json`，按 `license_id + 月份` 记录；`max_builds_per_month <= 0` 不启用配额。
- 吊销列表：`revocations.json`，通过 `tool_license.install_revocations(document)` 安装。
- 授权检查：`tool_license.check()` 会验签、绑机、检查日期/宽限期、吊销集合、功能与配额。

宽限期行为：`expires` 之后、`expires + grace_days` 之前，`active_on()` 返回 `True` 并提示 grace period；超过后拒绝。门户的 `license_status` 在宽限期内仍返回 `active`。

---

## 10. 状态机

### 10.1 试用申请

```text
（无）
  │ apply
  ▼
pending_email ──verify──► verified ──► 已签发授权
  │                         │
  │ 新申请 supersede         └─ 已存在授权：再次 verify 返回 already=true
  ▼
superseded
```

- `pending_email`：等待邮箱验证码；重发会刷新验证码并重置尝试次数。
- `verified`：验证通过并已签发（或已存在授权）。
- `superseded`：同一序列号提交了新申请，旧申请失效。

### 10.2 授权有效状态

```text
active ──到期且超过宽限──► expired
   │
   └──管理员/激活码吊销──► revoked
```

- `active`：包含宽限期内；`expires` 仍是原到期日。
- `expired`：`today > expires + grace_days`。
- `revoked`：管理员吊销或激活码吊销；`revoked_reason` 记录原因。
- 数据库 `status` 只有 `active`/`revoked`；`expired` 是读取时按日期计算的 `effective_status`。

### 10.3 激活码状态

```text
unused ──首次激活──► active
   │                   │
   │ 超过 expires_at   │ 超过 expires_at
   └─────────┬─────────┘
             ▼
          expired

任意状态 ──管理员吊销──► revoked
```

- `unused`：未使用且在兑换期限内。
- `active`：至少被一个序列号使用过。
- `expired`：超过 `expires_at` 兑换期限；`unused` 和 `active` 都会变成该状态，但已签发的授权不受影响。
- `revoked`：管理员吊销；使用该 key 的授权同时被置为 `revoked`。

### 10.4 客户端状态映射

| 服务端 | 客户端含义 | 建议动作 |
| --- | --- | --- |
| trial `unknown` | 还没有记录 | 继续轮询或引导申请 |
| trial `granted` | 可领取 | 调 `/api/trial/claim` |
| trial `expired` | 试用结束 | 提示购买/续费 |
| trial `rejected` | 试用吊销 | 停止并展示原因 |
| license `active` | 有效 | 验签/安装新文档（如有） |
| license `expired` | 到期 | 提示续费 |
| license `revoked` | 吊销 | 拒绝使用 |
| license `unknown` | 无记录 | 视为未授权，不要清除本地证据 |

---

## 11. 限流、TTL 与幂等

### 11.1 限流

实现是固定窗口（`window_start = now // window`），计数在独立事务中提交。通过参数格式校验后，即使业务失败（验证码错误、激活码不存在等）也会计数；在限流检查之前的格式校验失败则不消耗配额。触发后统一返回：

```json
{"error": "操作过于频繁，请稍后再试", "code": "rate_limited"}
```

HTTP 状态 `429`。

| 操作 | 维度 | 限制（默认） | 窗口 |
| --- | --- | --- | --- |
| 试用申请 | 每个 IP | `rate_limit_per_hour`（12） | 1 小时 |
| 试用申请 | 每个邮箱 | 3 | 1 小时 |
| 试用申请 | 每个手机号 | 3 | 1 小时 |
| 邮箱验证 | 每个 IP | 30 | 1 小时 |
| 重发验证码 | 每个 IP | 10 | 1 小时 |
| 重发验证码 | 每个 application | 5 | 1 小时 |
| 激活码兑换 | 每个 IP | `rate_limit_per_hour`（12） | 1 小时 |
| 激活码兑换 | 每个 key 前缀（`PSP`/`PSE`） | 100 | 1 小时 |
| 升级意向 | 每个 IP | 5 | 1 小时 |
| 管理员登录 | 每个 IP | 20 | 15 分钟 |
| 管理员登录 | 每个用户名 | 10 | 15 分钟 |

- `resend_after: 60` 只是前端提示；服务端按上表的重发桶限制。
- 试用状态、领取、授权状态、吊销列表、健康检查没有内置限流；高并发场景应在 Nginx/网关另加限制。
- 管理员登录没有账户锁定，只有上述限流；密码使用 scrypt 校验。

### 11.2 TTL 与默认值

| 项目 | 默认 | 说明 |
| --- | --- | --- |
| 邮箱验证码 | 600 秒 | `CODE_TTL_SECONDS` |
| 重发提示 | 60 秒 | `resend_after`，仅 UI |
| 验证码错误次数 | 5 | 超过后 `too_many_attempts` |
| 管理员会话 | 12 小时 | `session_hours` |
| 试用天数 | 14 天 | `trial_days` |
| 试用构建数 | 3 次/月 | `trial_builds` |
| 试用宽限 | 0 天 | `trial_grace_days` |
| 激活码兑换期限 | 30 天 | `key_redemption_days` |
| 激活后授权天数 | 365 天 | 创建 key 时的 `days` |
| 激活码宽限 | 14 天 | 创建 key 时的 `grace_days` |
| 列表最大行数 | 500 | `MAX_LIST_ROWS` |
| 请求体 | 64 KiB | `max_body` |

### 11.3 幂等性

| 操作 | 幂等 | 说明 |
| --- | --- | --- |
| `/api/trial/status` | 是 | 只读 |
| `/api/trial/claim` | 是 | 有效授权重复领取返回同一文档 |
| `/api/license/status` | 是 | 只读；只在需要时返回新文档 |
| `/api/license/revocations` | 是 | 只读 |
| `/api/license/activate` | 是 | 同一 key + serial 的有效授权重复激活返回原授权 |
| `/api/public/trial/verify` | 是 | 已完成的申请再验证返回 `already: true` |
| `/api/public/trial/resend` | 否 | 每次生成新验证码，旧码失效 |
| `/api/public/trial/apply` | 否 | 有新申请时创建记录并 supersede 旧的 pending；已有有效试用则返回 `already: true` |
| `/api/admin/licenses/<serial>/renew` | 否 | 每次 `revision + 1` |
| `/api/admin/licenses/<serial>/upgrade` | 否 | 每次 `revision + 1` |
| `/api/admin/licenses/<serial>/revoke` | 结果幂等 | 每次写审计事件 |
| `/api/admin/keys/<id>/revoke` | 结果幂等 | 每次写审计事件 |

重试建议：

- `status/claim/license status` 可以指数退避重试；
- `activate` 在同一 key/serial 上可以重试（网络超时后先查 `/api/license/status` 再决定）；
- `renew/upgrade` 不要自动重试，先查详情确认 `revision`/`expires`；
- `resend/apply` 不要并发重试，容易触发限流和申请 supersede。

---

## 12. 错误码大全

### 12.1 通用

| `code` | HTTP | 说明 |
| --- | --- | --- |
| `not_found` | 404 | 路径不存在 |
| `method_not_allowed` | 405 | 方法不允许 |
| `validation_failed` | 400 | 字段校验失败，带 `errors` |
| `internal_error` | 500 | 未处理异常 |
| `rate_limited` | 429 | 触发限流 |
| `origin_rejected` | 403 | Origin/Referer 不匹配 |
| `ip_rejected` | 403 | 管理端 IP 白名单拒绝 |
| `csrf_failed` | 403 | CSRF token 缺失或不匹配 |
| `unauthorized` | 401 | 管理端未登录/会话过期 |
| `login_failed` | 401 | 管理员用户名或密码错误 |

### 12.2 试用/公共

| `code` | HTTP | 说明 |
| --- | --- | --- |
| `captcha_failed` | 400 | Turnstile 校验失败 |
| `spam_rejected` | 400 | 蜜罐字段非空 |
| `invalid_application` | 400 | application id 非法 |
| `application_not_found` | 404 | 申请不存在 |
| `application_not_pending` | 409 | 申请已失效/被替代 |
| `application_verified` | 409 | 申请已完成，无需重发 |
| `code_expired` | 409 | 验证码过期 |
| `code_invalid` | 400 | 验证码错误 |
| `too_many_attempts` | 429 | 验证码错误次数过多 |
| `serial_bound` | 409 | 序列号绑定其他设备 |
| `trial_revoked` | 403 | 试用被吊销 |
| `trial_expired` | 409 | 试用已结束 |
| `already_licensed` | 409 | 本机已有正式授权记录（不区分状态） |
| `machine_trial_exists` | 409 | 本机已申请过试用 |
| `customer_trial_exists` | 409 | 邮箱/手机号已申请过试用 |
| `customer_conflict` | 409 | 邮箱/手机号属于不同账户 |
| `mail_failed` | 502 | 验证码邮件发送失败 |

### 12.3 激活/授权

| `code` | HTTP | 说明 |
| --- | --- | --- |
| `invalid_key` | 400 | 激活码不存在 |
| `invalid_edition` | 400 | 版本不能用于该流程 |
| `key_revoked` | 403 | 激活码已吊销 |
| `key_expired` | 403 | 激活码超过兑换期限 |
| `activation_limit` | 409 | 激活码达到最大设备数 |
| `other_key` | 409 | 本机已有其他有效 key 授权 |
| `admin_license` | 409 | 本机已有管理员签发授权 |
| `machine_mismatch` | 403 | 机器码与序列号不匹配 |
| `license_not_found` | 404 | 授权不存在 |
| `license_revoked` | 403/409 | 授权已吊销；桌面领取/激活 403，管理端续费/升级 409 |
| `license_expired` | 403 | 授权已过期（桌面领取/激活） |

### 12.4 管理端对象

| `code` | HTTP | 说明 |
| --- | --- | --- |
| `invalid_key_id` | 400 | 激活码编号非法 |
| `key_not_found` | 404 | 激活码不存在 |
| `key_decrypt_failed` | 500 | 激活码解密失败 |
| `invalid_customer_id` | 400 | 客户编号非法 |
| `customer_not_found` | 404 | 客户不存在 |
| `invalid_order_id` | 400 | 订单编号非法 |
| `order_not_found` | 404 | 订单不存在 |
| `invalid_order_status` | 400 | 订单状态不在允许集合 |
| `admin_exists` | 409 | 管理员已存在（CLI，非 HTTP） |
| `admin_not_found` | 404 | 管理员不存在（CLI，非 HTTP） |

> 底层读取请求体失败时可能返回 `400`/`413` 且 `code` 为默认的 `error`（例如“请求体为空”“请求体过大”“请求体不是合法 JSON”）。客户端应以 HTTP 状态和 `error` 文案兜底。

---

## 13. 完整联调示例

以下示例使用占位符，**不要**把真实管理员密码、激活码、私钥或 SMTP 密码写进脚本仓库。

### 13.1 启动本地门户

```bash
cd PyShield/license-portal
cp config.example.json config.json
(cd web && npm ci && npm run build)
python -m license_portal init --config config.json

export PYSHELD_PORTAL_ADMIN_PASSWORD='<至少10位的占位密码>'
python -m license_portal admin create --config config.json --username admin

python -m license_portal run --config config.json --dev
```

`--dev` 使用 ConsoleMailer，验证码会打印到标准错误，不会真的发邮件；没有 `web/dist` 时根路径返回 503，但 `/api/*` 仍可用。管理员密码也可在交互式终端中由命令提示输入。

本地试用页：

```text
http://127.0.0.1:8091/activate?serial=PS-AAAA-BBBB-CCCC&machine=0123456789abcdef0123456789abcdef
```

> Windows 上如果 8091/8092 落在系统排除的 TCP 端口段，启动会报 `WinError 10013`。用
> `netsh interface ipv4 show excludedportrange protocol=tcp` 查看排除段，换一个空闲端口，并把
> `site_url`、`PYSHELD_TRIAL_SERVER` 同步改成该端口。不要只改监听端口而忘记站点 URL。

### 13.2 健康检查与桌面轮询

```bash
BASE=https://license.example.com
SERIAL=PS-AAAA-BBBB-CCCC
MACHINE=0123456789abcdef0123456789abcdef

curl -sS "$BASE/health"
curl -sS "$BASE/api/version"

# 轮询试用状态
curl -sS "$BASE/api/trial/status?serial=$SERIAL&machine=$MACHINE"

# 已 granted 后领取
curl -sS -X POST "$BASE/api/trial/claim" \
  -H 'Content-Type: application/json' \
  -d "{\"serial\":\"$SERIAL\",\"machine\":\"$MACHINE\"}"
```

### 13.3 管理端登录并生成激活码

```bash
BASE=https://license.example.com
COOKIE=/tmp/pyshield-portal.cookies
read -rsp 'Portal admin password: ' PORTAL_ADMIN_PASSWORD; echo
LOGIN=$(curl -sS -c "$COOKIE" -X POST "$BASE/api/admin/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PORTAL_ADMIN_PASSWORD\"}")
echo "$LOGIN"
CSRF=$(printf '%s' "$LOGIN" | jq -r .csrf_token)

curl -sS -b "$COOKIE" -X POST "$BASE/api/admin/keys" \
  -H "X-CSRF-Token: $CSRF" \
  -H 'Content-Type: application/json' \
  -d '{
    "edition":"pro",
    "quantity":1,
    "days":365,
    "max_builds":100,
    "seats":1,
    "max_activations":1,
    "grace_days":14,
    "redemption_days":30,
    "notes":"联调测试"
  }'
```

上面的 `jq` 仅用于提取 `csrf_token`，没有安装时手动解析 JSON 即可。Windows PowerShell 可以用
`Invoke-RestMethod -SessionVariable` 保存会话；关键是登录后从响应 `csrf_token` 取值，并在每个
非 GET 请求加 `X-CSRF-Token`。

### 13.4 桌面激活与刷新

```bash
BASE=https://license.example.com
SERIAL=PS-AAAA-BBBB-CCCC
MACHINE=0123456789abcdef0123456789abcdef
KEY=PSP-ABCD-EFGH-JKMN-PQRS

curl -sS -X POST "$BASE/api/license/activate" \
  -H 'Content-Type: application/json' \
  -d "{\"key\":\"$KEY\",\"serial\":\"$SERIAL\",\"machine\":\"$MACHINE\"}"

curl -sS -X POST "$BASE/api/license/status" \
  -H 'Content-Type: application/json' \
  -d "{\"serial\":\"$SERIAL\",\"machine\":\"$MACHINE\",\"license_id\":\"PRO-$SERIAL\",\"revision\":1}"
```

### 13.5 吊销与吊销列表

```bash
# 沿用上一节的 BASE / COOKIE / CSRF；补上序列号和机器码
SERIAL=PS-AAAA-BBBB-CCCC
MACHINE=0123456789abcdef0123456789abcdef

# 管理员吊销授权
curl -sS -b "$COOKIE" -X POST "$BASE/api/admin/licenses/$SERIAL/revoke" \
  -H "X-CSRF-Token: $CSRF" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"联调测试"}'

# 客户端重新查状态
curl -sS -X POST "$BASE/api/license/status" \
  -H 'Content-Type: application/json' \
  -d "{\"serial\":\"$SERIAL\",\"machine\":\"$MACHINE\"}"

# 下载签名吊销列表
curl -sS "$BASE/api/license/revocations"
```

### 13.6 Python 客户端最小封装

```python
import json
import urllib.error
import urllib.request

BASE = "https://license.example.com"
SERIAL = "PS-AAAA-BBBB-CCCC"
MACHINE = "0123456789abcdef0123456789abcdef"


def call(method, path, payload=None):
    data = None
    headers = {"Accept": "application/json", "User-Agent": "PyShield/0.6.0"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


# 1. 轮询
status = call("GET", f"/api/trial/status?serial={SERIAL}&machine={MACHINE}")
if status.get("status") == "granted":
    # 2. 领取
    result = call("POST", "/api/trial/claim", {"serial": SERIAL, "machine": MACHINE})
    document = result["document"]
    # 3. 交给 trial_license.apply_document(document) 验签并安装
```

生产客户端更推荐直接调用 `pyshield.trial_license`，它已经封装了服务器地址校验、超时、
响应大小限制、验签和本地写入。

---

## 14. 客户端接入清单

- [ ] 首次启动生成并持久化 `serial`（机器指纹 + 随机盐）；不要每次启动重新生成。
- [ ] 机器码统一小写 32 位十六进制，并与服务端一致。
- [ ] 内置根公钥和站点地址；生产必须 HTTPS，禁止 `PYSHELD_DEV`。
- [ ] 打开 `/activate?serial=&machine=&edition=&source=&version=`，不要把邮箱/手机号拼进 URL。
- [ ] 轮询 `/api/trial/status`（建议 3 秒/180 秒），处理 `unknown/granted/expired/rejected`。
- [ ] `granted` 后调用 `/api/trial/claim`，用内置公钥验签，再写 `tool-license.json`。
- [ ] 激活码流程调用 `/api/license/activate`；网络超时后先查 `/api/license/status` 再重试。
- [ ] 启动/手动刷新调用 `/api/license/status`，用 `license_id + revision` 判断是否替换文档。
- [ ] 定期下载 `/api/license/revocations`，验签并安装；拒绝已吊销 `license_id`。
- [ ] 深链只提取 `serial`，校验等于本机序列号；不要把 `code` 当授权。
- [ ] 错误分支用 `code`，展示用 `error`；表单展示 `errors` 字段。
- [ ] 生产日志不要记录完整激活码、SMTP 密码、私钥、portal.secret。
- [ ] 对 429/5xx 做退避，不要并发轰炸重发/申请接口。

---

## 15. 安全边界

### 15.1 必须遵守

- **私钥与口令**：`signing.key`、`signing.pass`、签发口令只存在服务端受保护目录；绝不进入客户端、前端、数据库明文或 Git。
- **SMTP 密码/授权码**：只通过环境变量或 0600 配置文件提供；不要写入管理端页面、数据库或仓库。
- **portal.secret**：用于密钥哈希/加密、IP 哈希、会话和 CSRF；必须与数据库成对备份，泄露会导致激活码和会话风险。
- **激活码明文**：创建响应会返回一次；之后只能通过管理端 `reveal` 重新解密（会写审计）。不要记录到普通日志或工单系统。
- **公钥**：可以注入客户端；客户端必须验签，不能信任未签名 JSON。
- **HTTPS**：生产必须由 Nginx/Caddy 终止 TLS；门户本身是明文 HTTP 服务，不要直接暴露公网。
- **CORS**：不要添加 `Access-Control-Allow-Origin: *`；管理端依赖同源 + CSRF + Cookie。
- **管理员 IP 白名单**：公网部署强烈建议配置；反向代理场景只信任可信代理的 `X-Forwarded-For`。
- **管理员角色**：当前服务端只校验会话，不按 `role` 做只读限制；不要把 viewer 当只读安全边界。
- **限流**：内置限流是固定窗口的粗粒度保护，不是 WAF；公网应叠加网关限流、WAF 和告警。
- **PII**：邮箱、手机号、姓名、公司属于个人信息；最小化收集、限制管理端访问、设置保留期限。
- **离线吊销**：已安装的旧文档在离线状态仍可用到到期；需要更强控制时缩短有效期或增加心跳。

### 15.2 信任模型

- 桌面端接口没有用户登录，安全依赖：序列号 + 机器码绑定、签名文档、限流、服务端状态。
- 序列号和机器码不是强密钥；不要把它们当作用户密码或 API token，避免在不必要的页面/日志中暴露。
- 浏览器公共 API 的写操作依赖 Origin 校验、Turnstile、邮箱验证码和限流；这些都不能替代 TLS。
- 管理员 API 的凭据是会话 Cookie + CSRF token；Cookie 是 HttpOnly，前端不保存密码。

---

## 16. 排障

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| 根路径 503，提示构建 | `web/dist/index.html` 不存在 | 在 `license-portal/web` 执行 `npm install && npm run build` |
| `WinError 10013` 启动失败 | 端口在 Windows 排除段 | `netsh interface ipv4 show excludedportrange protocol=tcp`，换空闲端口并同步 `site_url` |
| `origin_rejected` | 浏览器来源与 `site_url` 不一致 | 统一协议/主机/端口；curl 不带 Origin 可正常调用 |
| `csrf_failed` | 没带 `X-CSRF-Token` 或 token 过期 | 登录或 `/api/admin/me` 重新取 token |
| `unauthorized` | Cookie 过期/被清 | 重新登录；检查 Secure/SameSite 与 HTTPS |
| `ip_rejected` | 管理端 IP 不在白名单 | 配置精确 IP/CIDR；代理场景检查 `trust_proxy` |
| `machine_mismatch` | serial 绑定其他机器 | 不要修改机器码；用原机器或新序列号 |
| `serial_bound` | 同 serial 换机器 | 用原机器，或为新机器生成新 serial |
| `other_key` / `admin_license` | 本机已有其他授权 | 先确认现有授权，避免重复激活 |
| `invalid_key` | 激活码拼错/不存在 | 检查 16 位 Crockford 字符；`O/I/L` 会被纠正 |
| `key_expired` | 激活码超过兑换期限 | 重新发码；已签发授权不受影响 |
| `license_expired` | 授权超过 `expires + grace_days` | 续费/升级；区分 key 过期 |
| `license status active` 但无 `document` | 本地已是最新 | 正常；只有 license_id 不同或 revision 落后才返回文档 |
| `trial status unknown` | 还没有签发记录 | 继续轮询或提交申请 |
| `too_many_attempts` | 验证码错误 5 次 | 重新提交申请或重发验证码（会重置尝试次数） |
| `code_expired` | 验证码超过 10 分钟 | 重发或重新申请 |
| `mail_failed` | SMTP 配置/网络错误 | 检查 SMTP_HOST/PORT/USER/PASSWORD/TLS；本地用 `--dev` 看控制台验证码 |
| `rate_limited` | 固定窗口限流 | 按窗口等待（1 小时或 15 分钟）；服务端不返回 `Retry-After` |
| 客户端提示签名失败 | 公钥不匹配/文档被改/编码错误 | 确认构建注入的根公钥与门户 `signing_key.public_key` 一致 |
| 深链打不开 | 浏览器未注册 `pyshield://` | 手动输入试用码/激活码，或重新从客户端打开页面 |

关于 429：服务端当前没有返回 `Retry-After` 头；客户端应按窗口（1 小时或 15 分钟）做保守退避，不要立即重试。

---

## 17. 变更管理

### 17.1 冻结范围

以下路径和方法是桌面客户端稳定契约：

```text
GET|HEAD /api/version
GET      /health
GET|POST /api/trial/status
POST     /api/trial/claim
POST     /api/license/activate
POST     /api/license/status
GET      /api/license/revocations
```

只允许：

- 新增响应字段；
- 新增可选请求字段（旧客户端不传也能工作）；
- 修正文案，不改变 `code` 语义；
- 在明确版本升级时新增路径，不删除旧路径。

不允许：

- 改路径、HTTP 方法、字段名、字段类型；
- 改变 `status` 映射或 `revision` 语义；
- 把可选字段改成必填；
- 改变签名格式、签名前缀或 JSON 规范化方式而不升级格式版本。

### 17.2 门户内部 API

`/api/public/*`、`/api/admin/*` 与门户前端同版本发布，可以调整；但仍建议：

- 保持错误格式 `{error, code, errors}` 不变；
- 新增字段而不是重命名；
- 管理员操作继续走 CSRF 和审计；
- 任何涉及密钥、激活码明文、SMTP 的变更都要过安全审查。

### 17.3 发布检查

- [ ] `python -m license_portal selftest`
- [ ] `python -m unittest discover -s license-portal/tests -v`
- [ ] `cd license-portal/web && npm run typecheck && npm run build`
- [ ] 用测试序列号走完试用、领取、激活、续费、吊销
- [ ] 检查 `/api/license/revocations` 签名可被客户端公钥验证
- [ ] 确认生产使用 HTTPS、Secure Cookie、管理员 IP 白名单
- [ ] 确认日志中没有验证码、完整激活码、密码、私钥

---

## 附：一分钟速查

| 目的 | 调用 |
| --- | --- |
| 客户端查试用 | `GET /api/trial/status?serial=&machine=` |
| 客户端领文档 | `POST /api/trial/claim` |
| 客户端激活 | `POST /api/license/activate` |
| 客户端刷新 | `POST /api/license/status` |
| 客户端吊销列表 | `GET /api/license/revocations` |
| 网页提交试用 | `POST /api/public/trial/apply` |
| 网页验证邮箱 | `POST /api/public/trial/verify` |
| 网页兑换 | `POST /api/public/redeem` |
| 管理员登录 | `POST /api/admin/login` |
| 管理员发码 | `POST /api/admin/keys` |
| 管理员续费 | `POST /api/admin/licenses/<serial>/renew` |
| 管理员升级 | `POST /api/admin/licenses/<serial>/upgrade` |
| 管理员吊销 | `POST /api/admin/licenses/<serial>/revoke` |

管理员的所有非 GET 请求都要带 `X-CSRF-Token`；桌面端的 `/api/trial/*` 和 `/api/license/*` 不需要会话，涉及授权记录的接口必须带正确的 `serial` 和 `machine`（吊销列表除外）。

# 云端授权校验与构建扣次接口（已定稿）

> 状态：**已实现并通过本地测试**。桌面端 `pyshield/cloud_license.py` 与授权服务
> `license-portal/license_portal/client_api.py` 按本文契约通信。
> 原有 5 个桌面接口（`/api/trial/status|claim`、`/api/license/activate|status|revocations`）保持不变。

## 1. 目标

1. **心跳校验**：客户端每 1–5 分钟随机向云端校验授权 ID（如 `TRIAL-PS-0WEA-2QRQ-SMPV`）、
   序列号（`PS-0WEA-2QRQ-SMPV`）、机器码、版本与修订号。**任何异常（含断网）立即禁止构建**，
   直到下一次校验成功。
2. **构建扣次**：每次构建开始前同步向云端扣减 1 次额度，幂等防重扣；扣次失败则中止构建。
3. **传输加密**：三个接口的请求体与响应体均为 PSX1 加密信封，明文 never 上线。

## 2. 端点总览

| 端点 | 方法 | 用途 | 是否扣次 |
| --- | --- | --- | --- |
| `/api/license/verify` | POST | 心跳绑定校验（1–5 分钟随机） | 否 |
| `/api/license/preflight` | POST | 构建前置校验 | 否 |
| `/api/license/consume` | POST | 构建扣次（幂等） | 是 |

管理端（会话 + CSRF 保护）：

| 端点 | 方法 | 用途 |
| --- | --- | --- |
| `/api/admin/client-checks?serial=&kind=&ok=&limit=` | GET | 校验记录列表（kind=verify/preflight/consume，ok=0/1，limit≤1000） |
| `/api/admin/licenses/<serial>/build-quota` | POST | 调整配额 `{"limit": 整数或null, "reset": bool}` |
| `/api/admin/licenses/<serial>` | GET | 详情新增 `builds_used/builds_period/builds_limit/last_seen_at/client_checks` |

管理界面新增「云端校验」菜单（`/admin/checks`），授权详情页新增「云端构建配额」卡片与调整弹窗。

## 3. 传输加密

### 3.1 线格式

```jsonc
// 请求（外层 JSON，serial 明文用于路由，业务字段全部在信封内）
{"serial": "PS-0WEA-2QRQ-SMPV", "data": "<base85(PSX1 信封)>"}

// 成功响应
{"ok": true, "data": "<base85(PSX1 信封)>"}

// 失败响应（明文 JSON，客户端据此 fail-closed，不需要密钥）
{"ok": false, "code": "machine_mismatch", "error": "机器码与授权记录不一致"}
```

- 信封：`pyshield.crypto.seal / open_envelope`（PSX1，ChaCha20 + HMAC-SHA256，`cipher="chacha-pure"`，纯 Python 无第三方依赖）。
- 编码：`pyshield.crypto.b85encode / b85decode`（RFC 1924 base85）。
- 附加认证数据（AAD），把密文绑定到端点与序列号：
  - 请求：`pyshield-cloud:{endpoint}:{serial}`，如 `pyshield-cloud:verify:PS-0WEA-2QRQ-SMPV`
  - 响应：请求 AAD 追加 `:response`
- 信封内明文上限 16 KiB；响应上限 1 MiB。

### 3.2 加密密钥（每授权一密，不在网络上传输）

双方用相同材料各自推导 32 字节会话密钥：

```
guard_key = HKDF-SHA256(
    ikm  = f"{serial}|{machine}|{display_code}",   # UTF-8
    salt = b"pyshield-cloud-guard-v1",
    info = b"client-api",
    length = 32,
)
```

| 材料 | 来源 |
| --- | --- |
| `serial` | 本机序列号，安装时生成并写入 `install.json` |
| `machine` | 机器指纹（小写 hex），`fingerprint.machine_fingerprint()` |
| `display_code` | 云端下发的展示码：试用 = `trial_code`；付费 = 激活码前缀 `key_prefix`。claim / activate / status 响应中的 `trial_code` 字段即此值 |

- 客户端把展示码记在 `install.json` 的 `codes[serial]`（`trial_license.remember_code`，私有权限存储）。
- 首次缺失时通过既有明文接口 `POST /api/license/status` 引导获取一次（`trial_code` 字段），随后记住。
- 服务端从 `licenses` 表读取 `serial/machine/display_code` 推导同一密钥（`security.derive_guard_key`）。
- **没有共享密钥文件、没有密钥下发接口**；展示码本身已经在授权发放时交给客户端。

计算示例（两侧一致）：

```python
from pyshield import crypto
key = crypto.hkdf_sha256(
    f"{serial}|{machine}|{display_code}".encode("utf-8"),
    salt=b"pyshield-cloud-guard-v1", info=b"client-api", length=32,
)
```

### 3.3 信封内请求字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `serial` | str | 必须与外层一致 |
| `machine` | str | 机器码，必须与授权记录一致 |
| `license_id` | str | 云端授权 ID |
| `edition` | str | `trial` / `pro` / `enterprise`，可省略 |
| `revision` | int | 本地授权修订号 |
| `nonce` | str | 8–64 位 hex，每次请求唯一（客户端用 `secrets.token_hex(16)`） |
| `client_time` | int | Unix 秒，与服务器偏差默认 ≤300 秒 |
| `idempotency_key` | str | **仅 consume**，≤128 字符，建议用构建任务 UUID |
| `project_hash` | str | 可选，consume 审计用，截断 64 字符 |

### 3.4 信封内响应字段

公共字段：`ok`、`serial`、`license_id`、`edition`、`revision`（服务端当前值）、`expires`、
`quota_limit`、`quota_used`、`quota_period`、`remaining_builds`（limit=0 时为 `null` 表示不限）、
`server_time`、`client_time`、`nonce`（原样回显，客户端必须核对）。

| 端点 | 附加字段 |
| --- | --- |
| verify | `status`：`active` 或 `update_required`；`next_check_after`：60–300 随机秒；`update_required` 时附 `document`（新签名授权文件，客户端校验签名后安装） |
| preflight | `allowed`、`reason`；修订号落后时附 `document` |
| consume | `charged`、`duplicate`；重复幂等键返回首次扣次结果，不重复扣 |

`license_id` 不一致或 `revision` 落后但授权仍有效时，verify 返回 `update_required` + 新文档，
而不是直接拒绝；客户端安装新文档后继续运行。

## 4. 配额模型

- 试用版：`quota_period = "total"`，总量制不重置（默认 3 次，`PYSHELD_PORTAL_TRIAL_BUILDS` 可调）。
- 付费版：`quota_period = "YYYY-MM"`，跨月自动清零重计。
- 上限：管理员 `builds_limit` 覆盖 > 版本 `max_builds_per_month`；**0 = 不限次数**，`null` = 跟随版本默认。
- 扣次流水：`build_charges` 表 `UNIQUE(serial, idempotency_key)`；重复请求返回 `charged=false, duplicate=true`。
- 构建失败不退还（与本地计次策略一致，确认弹窗已告知）。

## 5. 错误码（fail-closed）

| code | HTTP | 含义 |
| --- | --- | --- |
| `missing_envelope` | 400 | 缺少加密请求体 |
| `license_not_found` | 404 | 序列号无授权记录 |
| `bad_envelope` | 400 | 解密/认证失败（密钥不符或密文被篡改） |
| `payload_too_large` | 413 | 信封内明文超过 16 KiB |
| `bad_payload` | 400 | 内层不是合法 JSON 对象 |
| `machine_mismatch` | 403 | 机器码与授权记录不一致 |
| `serial_mismatch` | 400 | 内层序列号与外层不一致 |
| `edition_mismatch` | 409 | 版本与授权记录不一致 |
| `license_revoked` | 403 | 授权已吊销 |
| `license_expired` | 403 | 授权已过期（含宽限期） |
| `clock_skew` | 400 | 客户端时间偏差过大 |
| `invalid_nonce` | 400 | nonce 格式非法 |
| `replay_detected` | 409 | nonce 已使用（重放） |
| `invalid_idempotency_key` | 400 | consume 缺少或超长幂等键 |
| `quota_exhausted` | 409 | 构建次数已用完 |
| `method_not_allowed` | 405 | 非 POST |

所有拒绝都会写入 `client_checks`（保留最近 5000 条），管理端「云端校验」页可查。

## 6. 客户端行为

- 开关：`PYSHELD_CLOUD_API=1` 强制启用；GUI 对**云端绑定的授权**（签名载荷含 `serial`）自动启用，
  离线导入的授权不受影响。地址优先级：`PYSHELD_CLOUD_API_BASE` > GUI 设置的试用/激活服务器 > 构建内置站点。
- 守护线程 `start_watchdog`：启动后约 2 秒首检，随后按 `next_check_after`（钳制在 60–300 秒）随机巡检。
- **失败即封锁**：网络不可达、解密失败、任何错误码、nonce 回显不符 → `mark_blocked`，
  `ensure_allowed()` 抛错，构建入口拒绝；界面顶部显示红色横幅与原因。
- 恢复：下一次心跳成功自动解除封锁；`update_required` 时先安装新签名文档。
- 构建路径：`_prepare` 先 `ensure_allowed` + `preflight`（不扣次）；`start` 在本地计次之前
  `consume(idempotency_key=uuid4)`，云端扣次失败直接中止。
- 无宽限离线构建：断网即视为异常（与需求“任何不对的情况不允许使用”一致）。

## 7. 服务端配置与防重放

| 项 | 默认 | 环境变量 |
| --- | --- | --- |
| 时钟偏差容忍 | 300 秒（30–3600） | `PYSHELD_PORTAL_GUARD_CLOCK_SKEW` |
| nonce 保留 | 600 秒，过期自动清理 | — |
| 校验记录保留 | 最近 5000 条 | — |

数据库自动迁移（`user_version` 1→2）：`licenses` 增加
`builds_used/builds_period/builds_limit/last_seen_at/last_seen_ip`，
新增 `guard_nonces`、`build_charges`、`client_checks` 表。旧库启动即升级，无需手工操作。

## 8. 安全说明

- 生产环境仍必须 HTTPS（Nginx 终结 TLS）；信封加密保护的是业务字段与防篡改，不替代 TLS。
- 密钥由授权绑定材料推导，抓包无法得到密钥；改包会导致 AAD/认证失败（`bad_envelope`）。
- nonce + 时间窗双因素防重放；幂等键防重复扣次。
- 展示码、签名私钥、SMTP 授权码均不落仓库/文档/日志。

## 9. 本地验证记录

- `license-portal/tests/test_client_api.py`：26 例（加解密往返、各类拒绝、重放、时钟偏差、
  幂等扣次、配额耗尽、月度重置、管理端列表与配额调整）。
- `tests/test_cloud_license.py`：9 例（真实门户端到端：claim 记码、verify/preflight/consume、
  展示码引导、吊销/断网 fail-closed、守护线程封锁与自动恢复）。
- 全量回归：门户 134 例、客户端 76 例全部通过；管理前端 `tsc + vite build` 通过，
  浏览器实测「云端校验」页与配额调整弹窗。
# PyShield Phase 3：商业化运营能力

Phase 0-2 已经具备“可签发、可激活、可撤销、可打包”的最小闭环。Phase 3 的目标不是继续加密码学特性，而是把 PyShield 从“一个带授权的工具”变成“一套可以持续卖、持续计量、持续审计的商业系统”。

四条主线：

| 主线 | 解决的问题 | 主要使用者 |
| --- | --- | --- |
| HSM/KMS 签发服务 | 根私钥不落地、签发可审计、可轮换 | 授权方内部 |
| 托管构建 | 客户没有可信构建机，或授权方需要控制签发 | 客户 / 交付团队 |
| OEM 计量与版税 | 按设备、按激活或按收入分成结算 | 软件厂商、渠道 |
| 客户门户与审计报表 | 自助续期、机器管理、账单与合规证据 | 客户、财务、审计 |

## 1. 目标架构

```text
客户浏览器
   │ HTTPS / OIDC
   ▼
客户门户 (Portal) ──────► 控制面 API (Control Plane)
                              │  ├─ PostgreSQL：租户、授权、机器、合同、账单
                              │  ├─ Redis：会话、限流、任务队列
                              │  ├─ S3/对象存储：构建产物、报表、审计归档
                              │  └─ 计费系统（Stripe/Chargebee）
                              ▼
                        签发服务 (Signing Service)
                              │ PKCS#11 / mTLS
                              ▼
                        HSM / KMS（私钥不可导出）

构建请求 ─► 构建编排器 ─► 隔离 Worker（容器/微虚机）
                              ├─ pyshield builder
                              ├─ 产物签名 + 证明（cosign/Sigstore）
                              └─ 产物写入对象存储

PyShieldBuilder ─► 激活 API ─► PostgreSQL（设备/心跳/撤销）
```

关键原则：

- 根私钥只存在于 HSM 或离线介质，门户、控制面、构建 Worker 都拿不到。
- 签名服务只接受“类型化请求”，不提供任意字节签名。
- 客户源码在托管构建中短驻留、默认不落盘、任务结束即销毁。
- 授权、激活、构建、撤销、开票全部产生不可篡改的审计事件。
- 商业条款（价格、版税率）只存在后台，不写进客户端可读的授权文件。

## 2. HSM/KMS 签发服务

### 2.1 密钥分层

推荐两级结构：

| 层级 | 位置 | 用途 | 轮换 |
| --- | --- | --- | --- |
| 根密钥 Root | 离线 HSM / 离线机器，2-of-3 分片备份 | 签发 Issuer 证书、吊销 Issuer | 3-5 年或泄露时 |
| 签发密钥 Issuer | 在线 HSM / KMS，私钥不可导出 | 签发工具授权、激活响应、撤销列表 | 1-2 年 |
| 租户构建密钥 | HSM 中按租户隔离 | 签发受保护产物的 `pyshield.lic` | 按租户或按年 |

当前授权格式只有单个 `signature` 字段，没有 `key_id` 和证书链。落地时按阶段演进：

1. **MVP**：HSM 直接持有现有根密钥，构建器继续内置对应公钥；先解决“私钥不落地”。
2. **可轮换**：授权 payload 增加 `key_id`，构建器内置受信公钥列表（旧+新），支持平滑轮换。
3. **证书链**：根密钥签发 Issuer 证书，授权携带 `issuer` 与 `key_id`，客户端验证“根 → Issuer → 授权”链；这是格式 `pyshield-tool-license/2`，需要客户端同步升级。

Ed25519 选型注意：YubiHSM 2、Thales Luna、Entrust nShield 等 HSM 支持 Ed25519；部分云 KMS 对 Ed25519 的支持有限。选型前先用目标 HSM 跑通 `sign/verify` 和密钥导入导出限制。若云 KMS 不支持 Ed25519，不要为了上云改算法，优先使用云主机挂 HSM，或用 SoftHSM 做预发、物理 HSM 做生产。

### 2.2 签发服务接口

内部服务，仅控制面可调用，mTLS + OIDC 双认证：

```http
POST /v1/sign
{
  "type": "tool-license",              // tool-license | activation-response | revocation | output-license
  "key_id": "issuer-2026-01",
  "payload": { ... },                  // 已按类型校验的业务字段
  "request_id": "b7c1...",             // 幂等键
  "reason": "renewal for PS-2026-000123",
  "approval_id": "APR-2026-0042"       // 高风险操作必填
}
```

返回：

```json
{
  "signature": "base85...",
  "key_id": "issuer-2026-01",
  "audit_id": "EVT-2026-...",
  "signed_at": "2026-11-13T10:00:00Z"
}
```

服务端职责：

- 按 `type` 使用固定域分隔前缀重新构造待签名字节，拒绝客户端传入的裸字节。
- 校验业务策略：版本允许的功能、最长期限、配额上限、绑定模式、客户状态、是否欠费。
- 高风险操作（吊销、Enterprise/OEM 授权、超长有效期）要求双人审批。
- 幂等：同一 `request_id` 重放返回同一签名，不重复计数。
- 限流与配额：按调用方、按客户、按操作类型。
- 审计：请求人、客户、`license_id`、payload 哈希、审批单、结果、耗时。
- 密钥轮换期间同时支持新旧 `key_id`，并记录每个授权使用的密钥。

### 2.3 改造现有代码

当前 `pyshield/license_admin.py` 的 `issue_entitlement()` / `revoke()` 直接接收 `signing_key: bytes`。建议拆成两层：

```python
class Signer(Protocol):
    key_id: str
    def sign(self, prefix: bytes, payload: dict) -> tuple[str, str]: ...  # signature, key_id

class LocalSigner:      # 开发、离线应急
    ...
class RemoteHsmSigner:  # 生产
    ...
```

- `build_entitlement_payload(...)` 只负责字段校验和规范化，不接触私钥。
- `issue_entitlement(signer, ...)` 调 `signer.sign(...)`。
- `license_server.py` 的 `/v1/issue`、`/v1/revoke`、`/v1/activate` 全部改为调用 Signer，不再在进程内保存 `root_key`。
- 参考服务保留 `LocalSigner` 用于单机部署；生产部署用 `RemoteHsmSigner`。

## 3. 托管构建

### 3.1 适用场景

- 客户不愿或不能在本地准备 Python 构建机、Cython、MSVC。
- 授权方需要把“产物许可证签发”留在自己侧，按构建收费。
- OEM 客户需要每次出货都经过计量。

源码是客户最敏感的资产，托管构建必须按“零信任、短驻留、可审计”设计。不能承诺“绝对安全”，但要做到：默认不持久化源码、任务隔离、访问留痕。

### 3.2 构建流程

1. 控制面创建构建任务，返回预签名上传地址。
2. 客户上传源码压缩包（客户端可先加密，密钥随任务一次性传入）。
3. Worker 在独立容器/微虚机中拉取代码，校验哈希与构建清单。
4. 使用固定摘要的构建镜像执行 `shield.py build`，输入参数来自已签名的构建配置。
5. 产物写入对象存储，生成 SHA-256、SBOM、构建证明；可选 cosign 签名。
6. 产物许可证由 HSM 签发；构建密钥按策略加密保存或仅保存在托管侧。
7. 任务结束销毁工作目录、临时密钥和缓存；保留审计元数据，不保留源码。

### 3.3 隔离与配额

- 每次构建一个独立容器；Kubernetes Job + gVisor/Firecracker，禁止特权、禁止宿主挂载。
- 网络默认拒绝，仅允许对象存储和内部依赖镜像。
- CPU、内存、磁盘、时长、产物体积上限；超时强杀。
- 只读根文件系统，临时盘加密，任务结束擦除。
- Worker 不持有根密钥；产物签名通过短期令牌调用签发服务。
- 构建日志脱敏，禁止打印源码内容、环境变量和密钥。

### 3.4 API 与计费

```http
POST   /v1/builds                 创建任务
GET    /v1/builds/{id}            查询状态与阶段
POST   /v1/builds/{id}/cancel     取消
GET    /v1/builds/{id}/artifacts  下载产物与证明
POST   /v1/builds/{id}/reissue    托管续签产物许可证
```

按“构建次数 + 产物大小/时长”计量，和工具授权的 `max_builds_per_month` 解耦：前者是商业计费，后者是本地配额。托管构建成功后写入 `usage_events`，供账单和审计使用。

### 3.5 分阶段落地

1. 单租户托管：给 1-2 个战略客户用，验证构建镜像、产物签名和交付流程。
2. 多租户：租户隔离、配额、对象存储前缀隔离、审计。
3. 托管续签：客户不需要拿构建密钥，通过门户调用签发服务续期。
4. 构建证明：SBOM、cosign、可复现构建记录，作为企业采购的合规材料。

## 4. OEM 计量与版税

### 4.1 计费模型

| 模型 | 计量事件 | 适用 |
| --- | --- | --- |
| 按激活 | 每次设备激活 | 按设备收费的软件 |
| 按设备 | 去重后的设备数 | 年度授权、装机量统计 |
| 按构建 | 每次出货构建 | 按版本/批次交付 |
| 收入分成 | 客户申报收入 + 审计 | 大型 OEM、渠道 |
| 最低年费 | 合同保底 + 超额计量 | 降低授权方风险 |

授权文件里只放不透明的 `account_id` / `contract_id`，不写费率。费率、保底、账期、货币在控制面维护，避免客户从授权文件反推商业条款。

### 4.2 计量与对账

- 激活 API 记录 `activation_id`、`license_id`、`machine`、`product_id`、`account_id`、时间、IP/地区（按合规最小化）。
- 心跳记录在线设备与最后活跃时间；离线设备按激活记录计。
- 构建服务记录每次出货构建，绑定 `build_id` 和产物哈希。
- 每月生成 usage 汇总：新增激活、去重设备、构建次数、超额数量、争议项。
- 与客户申报对账；差异超过阈值触发人工审核。
- 发票、贷项、调整单全部进入审计日志。

### 4.3 信任边界

OEM 若完全离线自建，授权方无法强制计量。可用的缓解手段：

- 关键签发（出货许可证、激活响应）必须走授权方 HSM/在线服务，OEM 无法自行铸造。
- 离线场景使用“预付费额度 + 签名用量报告 + 定期审计”，合同约定审计权。
- 心跳与激活数据做异常检测：同一授权跨地区激增、设备复用、额度长期打满。
- 高价值客户优先卖托管构建或私有构建节点，而不是裸 exe。

## 5. 客户门户

### 5.1 角色与功能

| 角色 | 功能 |
| --- | --- |
| 客户管理员 | 席位与成员、授权总览、续期、机器解绑、发票、审计导出 |
| 开发者 | 下载构建器、构建历史、离线激活申请、配额查看 |
| 财务 | 账单、发票、付款方式、用量报表 |
| 授权方支持 | 客户检索、授权重发、临时延期、工单 |
| 授权方管理员 | 签发、吊销、合同、费率、审批、全局审计 |

MVP 功能：登录、租户、授权列表与状态、机器列表/解绑、构建器下载、离线激活申请与响应下载、发票列表、审计日志查询。

### 5.2 技术选型

- 后端：FastAPI + SQLAlchemy + Alembic + PostgreSQL；Redis 做会话/限流/队列。
- 前端：Next.js/React 或服务端模板；企业客户优先支持 SSO。
- 认证：OIDC（Keycloak/Auth0/Entra ID），客户可配 SAML；强制 MFA。
- 租户隔离：`tenant_id` + PostgreSQL Row Level Security；对象存储按前缀隔离。
- 权限：RBAC + 审批流；所有管理操作写审计。
- 文件：预签名 URL，不把产物或源码暴露在公网桶。

### 5.3 核心数据表

```text
accounts(id, name, status, billing_id, created_at)
users(id, account_id, email, role, mfa_enabled, last_login_at)
entitlements(id, account_id, license_id, edition, features, seats,
             max_activations, max_builds, expires_at, status, key_id)
machines(id, entitlement_id, fingerprint_hash, first_seen, last_seen, status)
builds(id, account_id, entitlement_id, status, artifact_sha256, created_at)
contracts(id, account_id, model, rate, currency, minimum, period, status)
usage_events(id, account_id, contract_id, event_type, quantity, occurred_at, source)
invoices(id, account_id, period, amount, currency, status, external_id)
audit_events(id, tenant_id, actor, action, object_type, object_id,
             result, ip, payload_hash, prev_hash, hash, occurred_at)
```

指纹只存哈希或 HMAC，不存原始硬件标识，降低隐私风险。

## 6. 审计报表

### 6.1 事件模型

每条事件至少包含：

```json
{
  "event_id": "EVT-2026-000001",
  "occurred_at": "2026-11-13T10:00:00Z",
  "tenant_id": "acct_123",
  "actor": "user:alice@example.com",
  "action": "license.issue",
  "object_type": "entitlement",
  "object_id": "PS-2026-000123",
  "result": "success",
  "ip": "203.0.113.10",
  "payload_hash": "sha256:...",
  "prev_hash": "sha256:...",
  "hash": "sha256:..."
}
```

- 按租户/天做哈希链，定时把链头锚定到 S3 Object Lock（WORM）或透明日志。
- 审计写入只追加，应用账号无删除权限；数据库开启审计扩展或逻辑复制到独立库。
- 保留期按合同：一般 1-3 年，金融/医疗客户可到 7 年；个人信息做最小化和假名化。
- 导出：JSONL/CSV（机器可读）、PDF（对账/合规包）。
- SIEM：Syslog/OTel 推送；告警规则至少包括：
  - 同一授权短时间多地区激活；
  - 已撤销授权仍 heartbeat；
  - 试用产物进入生产构建；
  - 构建量突增或长期打满配额；
  - 签发服务异常调用、审批绕过尝试；
  - OEM 申报与激活数据偏差超阈值。

### 6.2 报表清单

- 授权台账：客户、版本、功能、到期、席位、状态。
- 激活报表：按客户/产品/地区/时间。
- 构建报表：构建量、失败率、产物大小、配额使用。
- 试用转化：试用开始、构建、转付费。
- 续费预警：到期前 30/7 天。
- OEM 版税：计量、对账、发票、调整。
- 安全审计：吊销、异常激活、签发操作。

## 7. 实施顺序与验收

按依赖关系推进，3A 是所有后续工作的基础：

| 阶段 | 内容 | 预估 | 验收标准 |
| --- | --- | --- | --- |
| 3A | HSM 签发服务、Signer 抽象、审计事件库 | 2-3 周 | 根密钥不出 HSM；签发/吊销/激活全部经服务；每次签名可追溯到审批单 |
| 3B | 生产激活服务（Postgres）、门户 MVP | 4-6 周 | 客户可登录、看授权/机器、解绑、下载构建器、离线激活；激活数据不再存 JSON 文件 |
| 3C | 托管构建 MVP | 4-6 周 | 单租户端到端构建成功；产物有哈希/SBOM/证明；源码任务结束不残留 |
| 3D | OEM 计量、计费与版税报表 | 3-4 周 | 激活/构建事件可按合同聚合；月度账单可导出；对账差异有处理流程 |

两人团队约 10-14 周；单人建议先做 3A + 3B 的最小闭环，托管构建和 OEM 按订单再启动。

**最小可行路线（推荐）**：

1. HSM 托管根密钥，签发服务上线。
2. 激活服务从 JSON 文件迁移到 PostgreSQL，加审计。
3. 门户只做“只读 + 解绑 + 下载 + 离线激活”，先解决客服成本。
4. 有托管构建订单时再做 Worker 和产物证明。
5. 有 OEM 合同时再做计量和账单，不要提前建复杂计费系统。

## 8. 当前仓库改造清单

| 文件/模块 | 改造 |
| --- | --- |
| `pyshield/license_admin.py` | 拆分 payload 构造与签名；引入 `Signer` 协议 |
| `pyshield/license_server.py` | 迁移到 FastAPI/Postgres；调用签发服务；加审计、幂等、租户认证 |
| `pyshield/activation.py` | 支持 `key_id`、证书链、门户端点 |
| `pyshield/tool_license.py` | 增加 `key_id`、`account_id`、`contract_id`；保持向后兼容 |
| `tools/build_commercial.py` | 注入受信公钥列表；支持 CI 中的 key_id 选择 |
| 新增 `services/signing` | HSM 签名服务（PKCS#11 客户端 + 策略 + 审批 + 审计） |
| 新增 `services/control-plane` | 租户、授权、机器、构建、合同、发票 API |
| 新增 `services/build-worker` | 隔离构建 Worker 与产物证明 |
| 新增 `services/portal` | 客户门户前端 |
| 新增 `deploy/` | 容器镜像、K8s manifests、HSM 连接配置、备份与演练脚本 |

## 9. 风险与边界

- HSM 防的是私钥泄露，不防客户端补丁；客户端仍可能被逆向或绕过校验。
- 托管构建无法做到“源码绝对不泄露”，只能通过隔离、短驻留、审计和合同降低风险。
- 完全离线的 OEM 计量不可强制可信，必须用预付费、签名报告、审计权和在线签发组合约束。
- 云 KMS 的 Ed25519 支持不一致，选型前必须做兼容性验证。
- 引入证书链会改变授权格式，必须保留旧格式验证路径和一个完整的过渡版本。
- 客户门户涉及个人信息和账单数据，需同步准备隐私政策、数据处理协议和税务/发票流程。
- 不要在没有订单验证前投入完整计费系统；先用人工账单 + 用量报表跑通 2-3 个客户。

## 10. 与 Phase 0-2 的关系

- Phase 1 的工具授权格式、功能开关、配额和撤销机制继续有效，Phase 3 只替换“密钥放在哪里”和“状态存在哪里”。
- Phase 2 的 DPAPI/HMAC 本地状态继续用于客户端防误改；服务端用数据库和审计替代 JSON 文件。
- `docs/commercial.md` 描述的离线激活、心跳、撤销流程在 Phase 3 中保持兼容，门户只是把同样的操作搬到线上。

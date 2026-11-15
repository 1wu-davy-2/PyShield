# PyShield 商业授权与交付

本文说明 PyShield 自身作为商业软件的授权模型、版本划分、激活流程、构建水印和防破解边界。它不替代 [LICENSE](../LICENSE)，两者冲突时以 LICENSE 为准。

## 1. 两层授权

PyShield 涉及两种完全不同的许可证，不能混用：

| 层级 | 授权对象 | 文件 | 签发者 |
| --- | --- | --- | --- |
| 工具授权 | 购买 PyShield 的开发者/公司 | `pyshield-tool-license` | PyShield 根密钥 |
| 产物授权 | 客户交付给最终用户的受保护程序 | `pyshield.lic` | 构建密钥 |

工具授权决定“谁能使用 PyShield 构建、能用哪些功能、能构建多少次”；产物授权决定“受保护程序能在哪台机器、运行到什么时候”。两者使用不同的密钥、不同的格式和不同的校验代码。

## 2. 版本与功能

功能由授权文件中的 `features` 精确控制，版本名称只是预设组合。

| 功能键 | 含义 | 建议版本 |
| --- | --- | --- |
| `gui` | 桌面构建器 | Pro / Enterprise |
| `native-runtime` | `--runtime-native` 原生 runtime | Pro / Enterprise |
| `offline-activation` | 离线申请/响应文件激活 | Enterprise |
| `license-issuance` | 使用 `issue` 续签产物许可证 | Pro / Enterprise |
| `ci` | CI/流水线节点构建 | Enterprise |
| `oem` | OEM 再分发授权 | OEM |

参考版本：

- **Trial**：14 天、10 次构建，禁用原生 runtime 和产物签发，产物带试用水印。
- **Pro**：单席位年订阅，含 GUI、原生 runtime、产物签发，每月构建配额。
- **Enterprise**：多席位、离线激活、CI token、撤销列表、优先支持，可配 HSM 签发。
- **OEM**：面向软件厂商的再分发授权，按交付产品或设备分成。

## 3. 工具授权文件

工具授权是根密钥签名的 JSON 信封：

```json
{
  "format": "pyshield-tool-license/1",
  "payload": {
    "license_id": "PS-2026-000123",
    "customer": "ACME Co., Ltd.",
    "edition": "pro",
    "features": ["gui", "native-runtime", "license-issuance"],
    "seats": 1,
    "max_activations": 1,
    "max_builds_per_month": 100,
    "bind_mode": "any",
    "fingerprints": [],
    "activation_required": true,
    "issued": "2026-11-13",
    "not_before": "2026-11-13",
    "expires": "2027-11-12",
    "grace_days": 14,
    "revocation_epoch": 0
  },
  "signature": "base85-ed25519-signature"
}
```

字段说明：

- `bind_mode`：`any` 表示可激活到任意机器；`fixed` 表示只能在 `fingerprints` 列出的机器使用。
- `activation_required`：为 `true` 时，`any` 授权必须先激活成机器绑定授权才能构建。
- `max_activations`：在线激活服务允许的机器数量，由服务端执行。
- `max_builds_per_month`：本地构建配额；`0` 表示不限制。
- `grace_days`：到期后的宽限天数，宽限期内允许构建但状态标记为过期。
- `revocation_epoch`：预留的撤销代次，当前撤销以 `license_id` 列表为准。

根公钥通过以下顺序加载：

1. 环境变量 `PYSHELD_TOOL_PUBLIC_KEY`（Base85）；
2. `pyshield/_tool_key.py` 中的 `PUBLIC_KEY_B85`（商业构建时注入）；
3. 都没有时按「未激活」处理：已有本地授权则以未验签方式加载（仅限源码运行），否则要求先申请试用。

商业发布包必须注入公钥，不能依赖环境变量。

## 4. 激活流程

### 在线激活

```bash
python shield.py license activate --license customer-tool-license.json \
  --server https://license.example.com
```

客户端把签名授权和本机指纹发送到 `/v1/activate`，服务端校验签名、到期、撤销和激活数量，返回绑定到该机器的授权。客户端保存到 Windows 的 `%APPDATA%\PyShield\tool-license.json`（其他平台为 `~/.pyshield/tool-license.json`），可用 `PYSHELD_TOOL_LICENSE` 指定文件、`PYSHELD_HOME` 指定状态目录。

### 离线激活

客户机生成申请文件：

```bash
python shield.py license request --license customer-tool-license.json \
  --out activation-request.json
```

把申请文件交给授权方，授权方用离线根密钥签发响应：

```bash
python -m pyshield.license_admin activation-response \
  --key root.key --request activation-request.json --out activation-response.json
```

客户机导入响应：

```bash
python shield.py license apply --response activation-response.json
```

离线响应是绑定到申请机器指纹的签名授权，复制到其他机器无效。

### 客户端常用命令

```bash
python shield.py license status                 # 查看当前模式、配额、到期日
python shield.py license install --license x.json
python shield.py license activate --license x.json --server https://license.example.com
python shield.py license heartbeat --server https://license.example.com
python shield.py license revocations --url --server https://license.example.com
python shield.py license deactivate             # 删除本机授权文件
```

桌面构建器在“设置 → 工具授权”中提供同样的导入、在线激活、离线请求/响应、心跳、撤销同步和解除本机授权操作。

### 参考激活服务

```bash
python -m pyshield.license_server --root-key root-keys/root.key \
  --host 127.0.0.1 --port 8787 --data-dir license-data \
  --admin-token "$PYSHELD_LICENSE_ADMIN_TOKEN"
```

生产环境必须放在 TLS 反向代理之后；`/v1/issue` 和 `/v1/revoke` 需要 Bearer token。`--root-key` 指向的私钥不要放在公网机器上，推荐用 HSM 或内网签发机。

### CI 节点

CI 节点建议签发独立授权，`features` 包含 `ci`，`bind_mode` 为 `fixed` 并绑定构建机指纹。不要把开发者的交互式授权复制到流水线；席位和审计都会失真。流水线显式加 `--ci`，构建才会要求 `ci` 功能：

```bash
python shield.py build -p . -o dist-ci --ci
```

## 5. 试用与配额

- 未安装授权且已配置根公钥时进入试用模式。
- 试用状态保存在状态目录（Windows 为 `%APPDATA%\PyShield`，其他平台为 `~/.pyshield`，可用 `PYSHELD_HOME` 覆盖）下的 `trial.json`，使用机器指纹派生的 HMAC 保护。
- 试用默认 14 天、10 次构建，可用 `PYSHELD_TRIAL_DAYS`、`PYSHELD_TRIAL_BUILDS` 调整（仅用于测试和特殊交付）。
- 试用构建的 `.pyshield-build.json` 写入 `tool_license.trial = true`，便于识别。
- 正式授权的构建配额保存在同一状态目录的 `quota.json`，按月重置；撤销列表保存在 `revocations.json`。
- 试用状态和配额状态都经过机器指纹派生的 HMAC 校验，并在 Windows 上用 DPAPI 加密；手工编辑会失败关闭。
- GNU/Linux 等平台没有 DPAPI，使用 `0600` 权限加 HMAC；它只能提高篡改成本，不能阻止 root 级攻击。

## 6. 构建水印

每次构建在 `.pyshield-build.json` 写入：

```json
"tool_license": {
  "license_id": "PS-2026-000123",
  "customer": "ACME Co., Ltd.",
  "edition": "pro",
  "trial": false,
  "builds_used": 12
}
```

水印用于泄露追踪和试用识别，不参与产物运行校验。不要把客户敏感信息放进水印；如需更强追溯，可在授权中增加不透明的 `customer_id`。

## 7. 防破解边界

必须区分两种目标：

- **保护 PyShield 工具本身**：防止买家复制、补丁、超配额使用。
- **保护买家交付的产物**：防止买家的最终用户提取代码或绕过产物许可证。

PyShield 当前能提供的是“提高成本”，不是“绝对防止”：

- 根私钥不进入安装包；商业版只内置公钥。
- 授权校验应编译为原生代码，不能用单个 Python `if` 作为唯一防线。
- 商业版应使用 Nuitka 或 Cython 编译，不发布 `.py` 源码。
- 在线激活、心跳、撤销列表和短期授权让破解版本随时间失效。
- 按产物/设备收费时，构建签名必须放在服务端或 HSM，不能把签发私钥交给客户。
- 代码签名、安装包完整性、水印、EULA、审计条款属于必要但非技术性防线。

不要承诺“无法破解”。对高风险客户，优先销售托管构建、私有构建机或 HSM 签发，而不是单纯加固 exe。

## 8. 商业构建

生成根密钥（只做一次，离线保存）：

```bash
python -m pyshield.license_admin init --out root-keys
```

签发客户授权：

```bash
python -m pyshield.license_admin issue --key root-keys/root.key \
  --license-id PS-2026-000123 --customer "ACME Co., Ltd." \
  --edition pro --features gui,native-runtime,license-issuance \
  --max-builds 100 --expires 2027-11-12 --out acme-tool-license.json
```

注入公钥并构建商业 exe（推荐 Nuitka）：

```bash
python tools/build_commercial.py build --public-key root-keys/root.pub --backend nuitka
python tools/build_commercial.py build --public-key root-keys/root.pub --backend pyinstaller
```

只想先写入公钥而不打包：

```bash
python tools/build_commercial.py prepare --public-key root-keys/root.pub
```

`build_commercial.py` 会生成 `pyshield/_tool_key.py`，该文件已加入 `.gitignore`，不得提交。Nuitka 产物比 PyInstaller onefile 更难解包；PyInstaller 仅建议内部或低风险交付使用。发布前可用 `--signtool` 指向 Windows SDK 的 `signtool.exe` 做代码签名：

```bash
python tools/build_commercial.py build --public-key root-keys/root.pub \
  --backend nuitka --signtool "C:/Program Files (x86)/Windows Kits/10/bin/x64/signtool.exe"
```

注意：打包版本必须注入公钥。源码运行没有公钥时会读取本地授权但不验签；需要不限构建的开发调试时，用 `PYSHELD_DEV=1` 显式进入开发模式。

## 9. 运营清单

- 根私钥离线保存，至少两份异地备份，使用硬件加密盘或 HSM。
- 每个客户独立 `license_id`，授权文件按客户归档。
- 每年续期；到期前 30 天提醒。
- 撤销列表随版本或心跳分发，撤销后阻止新构建。
- 监控异常构建量、同一授权多机器激活、试用产物进入生产。
- 商业版发布前执行：签名、哈希、病毒扫描、安装包完整性测试。
- 保留授权签发和撤销日志，便于审计和纠纷处理。

### MCP 自动化构建的授权与额度

`PyShieldMCP.exe` 复用同一套工具授权与配额，不提供切换授权文件或开发模式的启动参数：

- 启动时必须能通过 `tool_license.check()`；trial/Pro/Enterprise 的 feature 限制与 CLI/GUI 一致；`runtime=native` 需要 `native-runtime` feature，`--ci` 需要 `ci` feature。
- `inspect`、`validate`、`status`、日志和静态校验不扣额度。
- `pyshield_start_build` 在严格预检通过后原子扣 1 次；只有目标项目代码错误（`PROJECT_CODE`）退还 1 次，环境/取消/超时/内部错误不退。
- 同一 `idempotency_key` / token 不重复扣；跨月退款以 credit 结转。
- MCP 不接受根私钥、许可证签发、激活/心跳等参数；这些能力仍只在受控的 CLI/授权服务中使用。

配额账本在 `tool_license.begin_build/refund_build`，使用跨进程锁和 sealed trial/quota 状态，避免并发超卖或重复退款。

## 10. 定价参考

以下仅为区间参考，按客户规模和替代方案调整：

- Pro：2,000–5,000 元/席位/年。
- Enterprise：50,000–200,000 元/年。
- OEM/按产物：每设备 0.5–5 元或收入分成。
- 托管构建：按构建次数或月度订阅。

真正的续费动力来自更新、支持、激活服务和合规背书，而不是单次 exe 销售。

## 11. 代码位置

- `pyshield/tool_license.py`：授权解析、校验、试用、配额、撤销、MCP 账本 `begin_build/refund_build`。
- `pyshield/build_spec.py` / `pyshield/build_service/`：MCP 严格预检、异步 job、失败分类、退款与审计。
- `pyshield/mcp_server/`：stdio MCP 协议、工具 schema、资源与提示词。
- `pyshield/activation.py`：在线/离线激活与心跳客户端。
- `pyshield/secure_store.py`：Windows DPAPI 与本地状态保护。
- `pyshield/license_admin.py`：根密钥、签发、撤销、离线响应。
- `pyshield/license_server.py`：参考激活服务（激活、心跳、撤销、管理签发）。
- `tools/build_commercial.py`：公钥注入、PyInstaller/Nuitka 打包、代码签名。
- `gui/backend.py`、`gui/web`：桌面构建器授权状态与激活入口。
- `tests/test_tool_license.py`、`tests/test_license_server.py`：授权、试用、配额、激活与撤销测试。
- `tests/test_build_ledger.py`、`tests/test_build_service.py`、`tests/test_mcp_*.py`：MCP 账本、预检、退款、协议、安全和执行门控测试。

后续运营能力（HSM/KMS 签发服务、托管构建、OEM 版税、客户门户与审计报表）的实施顺序与架构见 [Phase 3 路线图](phase3-roadmap.md)。

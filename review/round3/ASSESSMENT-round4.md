# PyShield Round 3 第四轮材料复核报告

- 评估日期：2026-11-15
- 复核对象：`ROUND3-INVITATION.md`、`ROUND3-FIX-RESPONSE.md` 以及第四轮材料（`develop @ ae718c4`，材料/文档 HEAD `53f2b50`）
- 测试环境：Windows 10 Pro 10.0.19045 x64，CPython 3.12.0，Django 5.2.8，PyInstaller 6.22.3
- 边界：沿用离线客户端前提；不要求“攻击者拿不到明文”，只评估提取成本、状态回滚、许可证校验和发布卫生。`master_key()`、运行时内存明文、双删重置、DPAPI 降级、无 Authenticode、无 TPM/服务端仍属已承认残余风险。

## 0. 结论：邀请函可用，但当前材料不建议直接发出

**邀请函本身结构清晰、哈希/CI/测试数字与材料基本一致；“修复说明”也大体准确。但当前 `round3-django-shielded.zip` 自带 `__pycache__`，且 `tools/check_release.py` 不会拒绝它。** 这是一个可复现的完整性/代码执行绕过：在可写的发布目录里改一个 `.pyc`，恶意代码会执行，而签名清单仍通过。建议先处理 PSH-R5-001/PSH-R5-002，重新打包并更新所有哈希，再发给审查方；否则审查方大概率会直接提交这个新问题。

已验证的事实：

- 四份交付物 SHA-256 与邀请函/`SHA256SUMS.txt` 全部匹配。
- 新挑战包 flag 可恢复：`PSH-ROUND3-e3b2caa377c88630`，SHA-256 `a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce`。
- CI run `43697777107` / `43697777144` / `43697777044` 在 `ae718c4` 上均 **success**（GitHub API 核对 `head_sha=ae718c4...`）。
- 全量测试 **180 tests OK (skipped=1)**；skip 为原生工具链缺失。
- PSH-R4-001/002/003 修复均验证有效；PSH-R4-004（发布卫生）只 **部分有效**，因为当前挑战 zip 仍有 `__pycache__`，且扫描器覆盖不足。

## 1. 新发现

### PSH-R5-001 挑战 zip 自带 `__pycache__`，且清单不覆盖 pycache；篡改 pyc 可绕过完整性校验并执行代码

- 严重度：**High**（发布目录可写时；若目录仅管理员可写，可降为 Medium）
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H`，约 **7.8**
- 受影响组件：`round3-django-shielded.zip` 中的 `demo/__pycache__`、`demo/management/__pycache__`、`sampleproj/__pycache__`；runtime 清单校验
- 受影响产物与 SHA-256：`round3-django-shielded.zip` `34c5e49f5cb5b23b4ea1a5f0e62514311e93a068377d4399701c8f85d9cb7954`
- 源码版本/提交：`develop @ ae718c4`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0、Django 5.2.8
- 前置条件/攻击者模型：能写发布目录的本地用户（per-user 安装常见）；无需管理员、调试器或内存 dump；挑战 zip 已自带 pycache，无需等待程序运行生成缓存
- 状态：新发现

#### 摘要

挑战包包含 10 个 `demo/`、`demo/management/`、`sampleproj/` 的 `__pycache__` 文件。`demo/__init__.py` 在签名清单（`code` 类）中，但它的 `__pycache__/__init__.cpython-312.pyc` 不在清单中。Python 导入时如果 pyc 头中的 source mtime/size 与 `.py` 一致，会优先使用 pyc。攻击者只要替换该 pyc（无需修改 `.py`），即可执行任意代码，而 `manage.py check` 仍显示清单和签名通过。

#### 根因

- `round3-django-shielded.zip` 内实际存在：

  ```text
  demo/__pycache__/__init__.cpython-312.pyc
  demo/__pycache__/apps.cpython-312.pyc
  demo/__pycache__/urls.cpython-312.pyc
  demo/__pycache__/views.cpython-312.pyc
  demo/management/__pycache__/...
  sampleproj/__pycache__/...
  ```

- `.pyshield-manifest.json` 有 23 项，包含 `demo/__init__.py`（category `code`），但 **没有任何 pycache/pyc 项**；`_scan_integrity()`（`_core.py:190-235`）只遍历清单中的文件。
- Python 3.12 对 timestamp-based pyc 只校验 `.py` 的 mtime/size，不校验 pyc 内容；攻击者可以读取源文件 stat 并生成匹配头。
- `builder.clean_pycache()`（`pyshield/builder.py:800-803`）存在，但挑战包是在构建后执行 `manage.py check` 再手工打包，缓存没有被清掉；`tools/check_release.py` 也不拒绝 `__pycache__`。
- `ROUND3-FIX-RESPONSE.md` 的“发布卫生”表把“扫描发布树/临时目录”作为已处理项，但没有 pycache 规则；`check_release.py` 只扫描 flag、私钥标记、可选 key needle，不扫描导入缓存。

#### 复现步骤

1. 解压 `round3-django-shielded.zip`。
2. 确认 `demo/__pycache__/__init__.cpython-312.pyc` 存在。
3. 读取 `demo/__init__.py` 的 mtime/size，生成一个新 pyc：

   ```text
   header = MAGIC + flags(0) + struct.pack('<I', source_mtime) + struct.pack('<I', source_size)
   body   = marshal.dumps(compile("print('PYSHELD_PYC_BYPASS')", 'demo/__init__.py', 'exec'))
   ```

4. 用小脚本覆盖 `demo/__pycache__/__init__.cpython-312.pyc`。
5. 运行 `python manage.py check`。

#### 最小 PoC

```powershell
python review\round3\poc-round4\pycache_tamper.py `
  review\round3\extracted\round3-django-shielded
```

`logs/pycache-tamper.log` 的实际输出：

```text
PYSHELD_PYC_BYPASS
System check identified no issues (0 silenced).
returncode: 0
OK: tampered __pycache__ executed while the signed manifest passed
```

#### 影响

- 在发布目录可写的部署中，攻击者可执行任意代码，绕过 `_scan_integrity()` 的“受保护文件/运行时被修改会发现”承诺。
- 可在应用启动后修改 `_shield_runtime` 的状态、时钟、许可证缓存或后续 TTL 校验；如果初始许可证已过期，恶意 `demo/__init__` 通常来不及在首次 license 校验前运行，所以不要把它表述为“任意过期许可证冷启动绕过”。但它是稳定的持久化代码执行/完整性绕过，而不是内存 dump 等已承认边界。
- 挑战 zip 当前携带 pycache，因此这是交付物本身的问题，不是“用户运行一次后才产生”的假设。

#### 修复建议

1. **立即**从挑战 zip 删除所有 `__pycache__`（以及 `demo/`、`sampleproj/` 下的 `.pyc`），重新打包并更新哈希。
2. 发布检查拒绝任何不在清单中的 `__pycache__`/`.pyc`；允许 `_shield_runtime/*.pyc`（清单内 runtime）但拒绝其他 pyc。
3. runtime 在 `_scan_integrity()` 增加“目录中是否存在未签名的 pycache/`.pyc`”检查；或将所有允许的 pycache 纳入签名清单并拒绝额外文件。
4. 启动引导在导入任何受保护模块前设置 `sys.pycache_prefix` 到临时目录，避免读取发布目录的 pycache；同时保持 `PYTHONDONTWRITEBYTECODE`/只读部署策略。
5. 在 CI 增加“篡改 pycache 应被拒绝”的回归测试；不要只测试正常 `manage.py check`。

#### 修复后验证

- 重跑 `pycache_tamper.py`，预期在导入阶段报 `LicenseError` 或明确的完整性失败，而不是打印 marker 并继续。
- 运行 `python tools/check_release.py <challenge.zip>`，预期拒绝 `__pycache__` 条目。
- 重建 zip 后再次确认 flag 可解、`manage.py check/reveal` 通过、`verify --json` 通过，并更新 `SHA256SUMS.txt`/邀请函哈希。

#### 残余风险

即使删除 pycache，能写发布目录的攻击者仍可修改未纳入清单的文件或整体替换 runtime（文档已承认）；本建议是把“意外的可执行缓存”从发布物和完整性模型中去掉。

---

### PSH-R5-002 `check_release.py` 对 master/build key 和 pycache 的扫描覆盖不足，修复说明的表述过宽

- 严重度：**Low**
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N`，约 **3.3**
- 受影响组件：`tools/check_release.py`；`ROUND3-FIX-RESPONSE.md` 的发布卫生表述
- 受影响产物与 SHA-256：不适用（工具/文档）；当前挑战 zip `34c5e49f...cb7954`
- 源码版本/提交：`develop @ ae718c4`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0
- 前置条件/攻击者模型：构建/发布流程依赖该检查器；本地用户
- 状态：部分修复

#### 摘要

`check_release.py` 的 `--temp` 分支给任意文件传入了空的 key needle 元组；只有 `*.shield-secret.json` 会按内容判断是否“真实 secret”。对 **zip** 目标，key needle 只来自同目录 sibling `*.shield-secret.json`，没有 sibling 时不会扫描 master/build key。它还不会拒绝 `__pycache__`，因此当前挑战 zip 的 PSH-R5-001 可以通过该检查器。修复说明里“扫描发布树中的 flag、master/build key、私钥标记和包内 secret”只对“带 sibling secret 的目录”大致成立，对 `--temp` 和普通 zip 不成立。

#### 根因

`tools/check_release.py`：

- `_scan_temp()`（约 `:132`）对临时目录内的普通文件调用 `_scan_bytes(..., needles=(), ...)`；master/build key 没有进入扫描。
- `_scan_target()`（约 `:104`）通过 `licensing.secret_path_for(target)` 找 sibling secret；zip 没有 sibling 时 `_secret_needles()` 返回空。
- `_scan_zip()`（约 `:84`）只扫描条目字节和 `.shield-secret.json` 文件名，不拒绝 `__pycache__`/`.pyc`。
- `_scan_bytes()` 只检查 `PSH-ROUND...`、PEM 私钥标记和传入的 needles；没有 needles 时无法发现裸 master/build key。

#### 复现步骤

1. 从授权 sample tree 派生 master/build key（测试用，不写入报告）。
2. 在 `%TEMP%\pyshield-check-gap-*` 写 `master.txt`，内容为 master/build key 的 hex。
3. 运行 `python tools/check_release.py --temp`，观察退出码 0 且 `TEMP scan: checked 1...`，无失败。
4. 生成一个没有 sibling secret 的 `pyshield-check-gap.zip`，内含同样 key 的 `master.txt`；运行 `python tools/check_release.py <zip>`，同样返回 0。
5. 运行 `python tools/check_release.py review/round3/round3-django-shielded.zip`，当前含 `__pycache__` 的 zip 也返回 `release check OK`。

#### 最小 PoC

```powershell
python review\round3\poc-round4\check_release_gap.py `
  review\round3\extracted\selfbuild-protected-sample
```

记录输出（`logs/check-release-gap.log`）：

```text
TEMP result: 0
TEMP scan: checked 2 pyshield*/psh* entries
release check OK: no challenge flag, build secret or private key material found
ZIP result: 0
release check OK: no challenge flag, build secret or private key material found
GAP CONFIRMED: a real test master/build key ... is not detected
```

#### 影响

- 当前实际交付物/临时目录没有发现主密钥泄露（独立扫描见 `logs/artifact-scan.log`、`logs/check-release-temp.log`），所以这不是在报告一个已发生的泄露。
- 但 `ROUND3-FIX-RESPONSE.md` 把 `check_release.py --temp` 的 0 作为发布卫生证据，属于过度保证；若未来构建机残留 master/build key in a plain file or zip without sibling secret，检查器会放行。
- 它也不阻止 PSH-R5-001 的 pycache 注入，因此不能作为“挑战包可安全发送”的依据。

#### 修复建议

1. 给 `check_release.py` 增加显式 `--secret <path>`（或从目标 sibling/构建日志发现），让 `--temp` 和 zip 扫描都能使用 master/build key needles。
2. `--temp` 扫描所有临时目录（至少所有 `*.shield-secret.json` 和文件名/内容疑似 secret 的文件），不要只依赖 `pyshield*`/`psh*` 前缀。
3. 拒绝发布树/zip 中不在清单内的 `__pycache__`/`.pyc`；对 runtime 的 `.pyc` 按 manifest 校验。
4. 修正文档：明确“检查器需要 sibling secret/`--secret` 才能扫 master/build key”；不要用 `--temp 0` 证明没有密钥。
5. 增加“带裸 master key 的 temp/zip 必须失败”“带 pycache 的 zip 必须失败”的回归测试。

#### 修复后验证

- `check_release_gap.py` 应返回非 0（两个场景都检测到）。
- `python tools/check_release.py review/round3/round3-django-shielded.zip` 应在看到 `__pycache__` 时失败。
- `python tools/check_release.py --temp` 在 `pyshield-check-gap` 场景下应失败，而不是仅计数。

#### 残余风险

即使修复检查器，构建机仍需专用账户/机器；发布卫生是流程控制，不是机密性边界。

## 2. 第四轮修复项验证

| 项目 | 结论 | 证据/说明 |
| --- | --- | --- |
| PSH-R4-001 首次绑定并发 | **有效** | 新代码 `_write_binding_record` 复用 `secure_store.write_private`，`_check_first_run_binding` 在整个 read-modify-write 外层持有 `_state_lock`；直接 16 进程 × 10 轮 0/160 失败，完整 `manage.py` 16×10 0/160 失败；无 `.tmp` 残留，primary/anchor `bound_at` 一致（`logs/logs-binding-direct.log`、`logs/logs-firstrun-concurrency.log`） |
| PSH-R4-002 legacy 一次性迁移 | **有效（主要路径）** | 默认拒绝；首次 opt-in 迁移成功并写主/锚点 marker；同一 env 下第二次 legacy 注入被拒绝；运行中删除状态后注入 legacy 被拒绝；legacy 未来日期被拒绝（`logs/logs-legacy-state.log`、`logs/legacy-midprocess.log`）。残余：keyed `last_seen=today+1` 仍按 1 天 skew 接受；删除 marker + 状态可重新迁移（文档已承认） |
| PSH-R4-003 onefile 文案 | **有效** | 新文案为“使用固定指纹或 onedir；PYSHELD_BINDING_FILE 不能使解压目录持久化”，不再建议在 onefile 下设置该变量；onefile/自定义 `_MEIPASS`/override `_MEI` 仍拒绝（`logs/logs-binding-guard.log`） |
| PSH-R4-004 发布卫生 | **部分有效** | 当前 `--temp` 为 0 项，敏感临时数据已清；新增 CI 扫描；但挑战 zip 含 `__pycache__`，`check_release.py` 不拒绝，master/build key 覆盖不足（PSH-R5-001/002） |
| PSH-R3-001 flag 不可解 | **有效** | 新挑战 build `1cc98e4796b92436`；`recover_flag.py` 解出 `PSH-ROUND3-e3b2caa377c88630`，SHA-256 匹配；zip 解压条目中 flag 值 0 命中；hash 只在 `ROUND3-CHALLENGE.md` 中出现（预期） |
| PSH-R3-002 时钟并发 | **有效** | 16 进程 × 5 轮 sample/challenge 均 0；16 进程启动/续期/TTL 各 3 轮 0；单份 read-only 写失败可继续；两份都失败仍 fail-closed（`logs/logs-clock-*.log`、`logs/logs-startup.log`、`logs/logs-clock-failure.log`） |
| PSH-R3-003 旧公开-MAC | **有效（主要路径）** | 默认拒绝；marker 生效；重复迁移/运行中重置拒绝；legacy 未来日期拒绝（`logs/logs-legacy-state.log`）。keyed +1 仍接受，属设计 skew |
| PSH-R3-004 onefile 绑定 | **有效** | onefile/自定义 `_MEIPASS`/override `_MEI` 拒绝；onedir 接受；`PYSHELD_BINDING_FILE` 不能关闭检查（`logs/logs-binding-guard.log`） |
| PSH-R3-005 构建机临时数据 | **有效（敏感数据）/部分（检查）** | 无新 flag/master/build key/真实 secret；`--temp` 0；但检查器覆盖不足（PSH-R5-002） |
| PSH-R3-006 原生 keyring | **部分验证/残余** | 源码仍导出 `master_key()`；CI run 43697777107 success（API）；本地无原生产物，strip/PDB/CodeView 未独立验证 |
| PSH-R3-007 自保护残余 | **残余（文档准确）** | 样本 28 stub、无 pycache、bundle 8 个模板无 keyring；selfbuild exe 无 `.py`/runtime datas，但 PYZ 中 keyring/master/business 可离线恢复（`logs/exe-selfbuild.log`、`logs/sample-audit.log`） |
| 格式 3 | **有效** | 新许可证 format 3、无 `build_key`；v1 盐解封被 `CryptoError` 拒绝（`logs/license-format.log`） |
| `_keyring` | **残余** | key 0.397 ms；13 模块全部 6.076 ms（热）/约 99 ms（冷）；自保护 exe 同（`logs/keyring-timing.log`、`logs/recover-flag.log`） |
| 单实例 | **有效（基本）** | 两个新 exe 第二次启动均退出码 0，只保留一个应用实例（`logs/single-instance-new.log`） |
| 测试 / CI | **有效** | 180 tests OK (skipped=1)；三个 run 均 success、head `ae718c4`（`logs/tests.log`、`logs/ci-runs.json`） |

## 3. 文档核对与发送前修改

| 文档声明 | 核对结果 | 建议 |
| --- | --- | --- |
| 四个产物 SHA-256 | 全部匹配 | 无需修改 |
| flag SHA / 可从 zip 恢复 | 匹配；flag 已解出 | 无需修改 |
| `develop @ ae718c4` | 代码提交正确；材料/文档 HEAD 为 `53f2b50`（docs-only） | 邀请函可加“审查材料 @ 53f2b50”以便复现 |
| CI 三个 run 均通过 | API 核对均为 success，head `ae718c4` | 无需修改 |
| 180 tests OK (skipped=1) | 匹配 | 无需修改 |
| 16 进程 × 10 轮首次绑定 | 匹配（0/160） | 无需修改 |
| 8 进程 × 2 轮时钟 | 本轮用 16×5 与 16×3 验证，0 失败；原声明不矛盾但证据较弱 | 可改为 16 进程 × 5 轮或引用新增测试 |
| legacy marker / 运行中拒绝 | 匹配 | 无需修改 |
| onefile 文案不再建议 override | 匹配 | 无需修改 |
| `check_release.py` 扫描 master/build key | **部分不成立**：`--temp` 无 key needles；无 sibling secret 的 zip 无 key needles | 修正文档/工具；见 PSH-R5-002 |
| `check_release.py --temp` 0 | 当前计数 0；但不能证明无 key | 写明检查范围，不要当作密钥卫生证明 |
| 样本无 `.pyc` | 匹配（sample zip 无 pycache） | 无需修改 |
| 挑战 zip 无明文 flag | 匹配（flag 值 0 命中） | 但 zip 含 `__pycache__`，需修复 PSH-R5-001 |
| 自保护 exe 无 `.py`/runtime datas | 匹配 | 说明 keyring/business 仍可离线恢复 |
| 原生 CI | API success；无本地产物 | 保持“以 CI 为准”，不要声称已独立验证 strip/PDB |
| 单实例 | 新 exe 复测通过 | 无需修改 |
| “发布卫生已处理” | 只部分成立（PSH-R5-001/002） | 先修再发；或在邀请函中明确新发现 |

**发送前最少修改**：

1. 删除挑战 zip 中所有 `__pycache__`/非 runtime `.pyc`，重新打包，更新 `SHA256SUMS.txt`、邀请函中的 zip SHA-256、README 大小。
2. 修复 `check_release.py`（pycache 拒绝、`--secret`/sibling 发现、`--temp` key needles），并把“0 项”改为带范围的表述。
3. 修复 runtime 的 pycache 完整性缺口，或至少在发布流程强制“发布树不得含任何未签名 pycache”；加篡改回归测试。
4. 邀请函可保留现有结构；加一行“审查材料 commit”并在哈希更新后重发。
5. 修复说明补充：挑战 zip 的 pycache 清理、check_release 覆盖范围；不要把 `--temp 0` 写成 master/build key 无泄露的证明。

## 4. 残余风险

| ID | 风险 | 说明 |
| --- | --- | --- |
| R-01 | pycache 完整性缺口 | 在修复前，任何带 `__pycache__` 的可写发布目录都可被替换 pyc 执行代码（PSH-R5-001） |
| R-02 | 发布检查覆盖不足 | `--temp`/无 sibling zip 不扫 master/build key，且不拒绝 pycache（PSH-R5-002） |
| R-03 | master key 可提取 | `_keyring.master_key()`；挑战 key 约 0.4 ms，13 模块约 6 ms（热） |
| R-04 | 运行内存明文 | code object/解密缓冲区；`_wipe` best effort |
| R-05 | 双删/删 marker 重置 | 时钟/绑定双记录 + legacy marker 被删除后可重置历史 |
| R-06 | keyed `last_seen=today+1` | 1 天 skew 内接受，记录日期冻结一天 |
| R-07 | 单份状态不可读 | fail-closed，可能导致启动失败（安全取舍） |
| R-08 | POSIX 锁无超时 | 持锁进程挂起可造成本地启动挂起 |
| R-09 | DPAPI 降级 | `PLAIN1` + HMAC，文档已写明 |
| R-10 | 自保护残余 | `integrity=off`、bundle 压缩、中间树明文；PYZ keyring/business 可恢复 |
| R-11 | 原生 keyring oracle | `master_key()` 可调用；strip/PDB 为 best effort；无 Authenticode/TPM/服务端 |
| R-12 | 300 秒重验不覆盖清单 | 清单只在首次加载扫描 |
| R-13 | 无 `_MEIPASS` 的 temp 源码部署 | first-run 绑定的 dist 在 `%TEMP%` 时不会被检测 |
| R-14 | 构建机 TEMP | 当前无敏感命中，但流程仍需专用账户/清理 |

## 5. 证据与复现

所有新 PoC/日志在 `review/round3/poc-round4/`；旧一轮脚本仍在 `review/round3/poc-fixed/`。样本只用于授权评估，不得再分发。

```powershell
# 当前 pycache 绕过
python review\round3\poc-round4\pycache_tamper.py `
  review\round3\extracted\round3-django-shielded

# check_release 的 key/pycache 覆盖缺口
python review\round3\poc-round4\check_release_gap.py `
  review\round3\extracted\selfbuild-protected-sample
python tools\check_release.py review\round3\round3-django-shielded.zip
python tools\check_release.py --temp

# 新 flag / keyring
python review\round3\poc-round4\recover_flag.py `
  review\round3\extracted\round3-django-shielded
python review\round3\poc-round4\keyring_timing.py `
  review\round3\extracted\round3-django-shielded 5

# R4 修复回归
python review\round3\poc-round4\binding_concurrency.py `
  review\round3\extracted\selfbuild-protected-sample <build-key-hex> 16 10
python review\round3\poc-round4\firstrun_build.py
python review\round3\poc-round4\firstrun_concurrency.py
python review\round3\poc-round4\legacy_state_tests.py `
  review\round3\extracted\selfbuild-protected-sample <build-key-hex>
python review\round3\poc-round4\binding_guard_tests.py `
  review\round3\extracted\selfbuild-protected-sample <build-key-hex>
```

关键日志：

- `logs/pycache-tamper.log`：篡改 pyc 后 marker 执行、`manage.py check` 仍通过。
- `logs/check-release-gap.log`：TEMP/zip 中测试 master/build key 未被检测。
- `logs/check-release-challenge.log`：当前含 pycache 的挑战 zip 仍 `release check OK`。
- `logs/recover-flag.log`、`logs/keyring-timing.log`：新 flag、build `1cc98e...`、master/build key SHA、耗时。
- `logs/logs-binding-direct.log`、`logs/logs-firstrun-concurrency.log`：16×10 0 失败。
- `logs/logs-clock-*.log`、`logs/logs-startup.log`、`logs/logs-clock-failure.log`：时钟/启动/TTL 0 失败及 fail-closed。
- `logs/logs-legacy-state.log`、`logs/legacy-midprocess.log`：marker、重复迁移、运行中拒绝。
- `logs/logs-binding-guard.log`：onefile/onedir/override 与修正后的文案。
- `logs/exe-selfbuild.log`、`logs/exe-main.log`、`logs/sample-audit.log`：exe/sample 提取结果。
- `logs/tests.log`：180 通过 / 1 跳过。
- `logs/ci-runs.json`：三个 CI run 均为 success，head `ae718c4`。

## 6. 建议

**先修 PSH-R5-001/002，再发送。** 这两项不需要改变离线客户端的产品边界，只需要：

1. 清理挑战 zip 的 pycache；
2. 让发布检查拒绝未签名 pycache 并能真正扫描 master/build key；
3. 在 runtime/启动流程处理 pycache 完整性缺口；
4. 用 `pycache_tamper.py`、`check_release_gap.py` 作为回归测试。

修复并重新打包后，邀请函的哈希/CI/测试数字会变化，需要同步更新。当前邀请函的评估目标、边界和报告格式是清楚的；不要把它改成“绝对安全”的表述。

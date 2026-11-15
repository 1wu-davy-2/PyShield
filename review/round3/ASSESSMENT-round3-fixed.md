# PyShield Round 3 修复版独立复核报告

- 评估日期：2026-11-15
- 审查对象：`develop @ 8adeccf`；审查材料 `develop @ 832be66`
- 复核材料：`review/round3/round3-django-shielded.zip`、`PyShieldBuilder-protected-sample-round3.zip`、`PyShieldBuilder.exe`、`dist-selfbuild/PyShieldBuilder-selfbuild.exe`
- 测试环境：Windows 10 Pro 10.0.19045 x64，CPython 3.12.0，Django 5.2.8，PyInstaller 6.22.3
- 方法：源码审阅、发布物离线解密、PyInstaller CArchive/PYZ 提取、16 进程并发测试、旧状态/绑定行为测试、自保护样本静态检查、单元测试（178 通过 / 1 跳过）、CI 记录核对
- 边界：沿用第二轮前提——客户端完全离线、exe 必须自带解密能力；不主张“攻击者拿不到明文”，只评估提取成本、状态回滚、许可证校验和发布卫生。`master_key()`、运行时内存明文、双删重置、DPAPI 降级、无 Authenticode、无 TPM/服务端仍属已承认残余风险。

## 0. 结论摘要

| # | 结论 | 级别 |
| --- | --- | --- |
| 1 | **flag 已修复**：从最终挑战 zip 可静态恢复 `demo.hidden_flag.FLAG`；解密后 `co_consts` 含该值，哈希匹配；zip 原始字节无明文 flag/hash | 有效 |
| 2 | `_keyring` 仍是低成本 oracle：1,679 字节 `_keyring.pyc` → `master_key()` 约 0.4–2.3 ms；13 个业务模块全部解密约 5.5–5.9 ms（热）/约 90–107 ms（冷，含导入）；自保护 exe 的 PYZ 同样可离线取到 master 并解密业务模块 | 残余风险（已承认） |
| 3 | 时钟双写/并发修复**有效**：16 进程 × 5 轮 0 失败；16 进程完整启动/续期/TTL 各 3 轮 0 失败；单份写失败不阻塞；两份都不可写仍 fail-closed | 有效 |
| 4 | **新问题**：首次运行绑定写入仍使用固定 `.tmp` 且没有跨进程锁；16 进程直接并发时 4/80 个 worker 因 `WinError 32`/`Permission denied` 启动失败（完整应用 16×5 未复现，重试可恢复） | Low |
| 5 | 旧公开-MAC 状态默认拒绝**有效**；显式 `PYSHELD_ALLOW_LEGACY_STATE=1` 迁移**不是一次性**：同一 env 下可反复写入旧状态；运行中还可绕过“状态消失”检查重置状态 | Low（部分修复） |
| 6 | onefile 绑定守卫**有效**：onefile/自定义 `_MEIPASS`/override 含 `_MEI` 均拒绝；设置 `PYSHELD_BINDING_FILE` 不能关闭检查。但 onefile 拒绝提示仍建议设置 `PYSHELD_BINDING_FILE`，该建议在 onefile 下无效（文案/UX 问题） | 有效 + Informational |
| 7 | 原生 runtime：源码层 strip/PDB/CodeView 逻辑仍在；`_keyring.master_key()` oracle 未变；本轮未提供本地原生产物，只能做 CI 记录与源码审阅，不能独立验证 `.pyd/.so` | 部分验证/残余 |
| 8 | 自保护样本：28 个 stub、`integrity=off`、bundle 仅 base64+zlib+zip、中间树明文；这些与文档一致。selfbuild exe 无 `.py`/runtime datas，但 PYZ 中 keyring/master/business 仍可离线恢复 | 残余（文档准确） |
| 9 | 主 exe 与自保护 exe 的基本单实例行为有效：第二次启动退出码 0；`PYSHELD_ALLOW_MULTI_INSTANCE=1` 是显式测试旁路 | 有效（非安全边界） |
| 10 | 构建机敏感临时数据已清理：TEMP 中未发现新 flag、挑战 master/build key 或真实 `*.shield-secret.json`；仍有多份旧 staging/CI 页面/样本副本（无敏感内容） | 部分（敏感数据有效） |

**本轮最关键的结论**：第三轮的阻断项（flag 不可解）已经修复；新增的实际风险集中在**首次运行绑定的并发写**和**旧状态 opt-in 迁移不是一次性**，两者都是可用性/状态回滚类，不是新的明文恢复或授权伪造路径。

## 1. 交付物、哈希与 flag

| 文件 | 大小 | SHA-256 | 校验 |
| --- | ---: | --- | --- |
| `PyShieldBuilder.exe` | 21,541,926 | `e3f85e630daa0fecf04d90b54c0ac80d50dc8140c3856ce4d25a732e66c416c1` | 匹配 |
| `dist-selfbuild/PyShieldBuilder-selfbuild.exe` | 40,562,131 | `4351f7d0a4c9cf9e64d3f662d3f495b3f714101892f4a1c4e70bf5f11a7d8f04` | 匹配 |
| `review/round3/PyShieldBuilder-protected-sample-round3.zip` | 430,520 | `ddb14d1b8403f4e0cb51afe7f5a02efc4ee8ad0a25a619ce67ccf6ad1c364320` | 匹配 |
| `review/round3/round3-django-shielded.zip` | 70,742 | `03fd153b94028f6c0180dcb53584866da3a414fbaa3e96ebaadb5ea19117a2fa` | 匹配 |

挑战工程元数据：build `719368cb21d3fce0`，13 个模块，`chacha`，`pyc` runtime，`integrity=protected`。许可证为 format 3、`bind_mode=any`、无 `build_key` 字段。

**恢复出的 flag**：

```text
PSH-ROUND3-e3b2caa377c88630
SHA-256 = a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce
```

哈希与 `review/round3/flag-sha256.txt` 一致。最终 zip 的原始字节扫描对 flag、flag 哈希、挑战 master、build key 均为 **0 命中**（`logs/artifact-scan.log`）。flag 只在解密后的 `demo.hidden_flag` 代码对象常量中。

## 2. 第三轮修复项验证矩阵

| 第三轮问题 | 验证结论 | 证据/备注 |
| --- | --- | --- |
| PSH-R3-001 最终挑战包不含 flag | **有效** | `recover_flag.py` 从 `_keyring.pyc` 恢复 master，HKDF 派生 build key，解密 `demo.hidden_flag`；`co_consts` 含 `PSH-ROUND3-e3b2caa377c88630`；哈希匹配；zip 原始字节 0 命中（`logs/recover-flag.log`、`logs/keyring-timing.log`、`logs/artifact-scan.log`） |
| PSH-R3-002 并发时钟写入失败 | **有效（时钟）** | 16 进程 × 5 轮（sample 与 challenge 两套 runtime）均 0 失败；16 进程完整启动/续期/TTL 各 3 轮 0 失败；无 `.tmp` 残留；单份 read-only 写失败两次调用均成功；两份 read-only fail-closed；anchor 删除可重建（`logs/clock-concurrency-*.log`、`logs/startup-concurrency-challenge.log`、`logs/clock-failure-tests.log`）。**注意**：绑定写入仍存在同类竞态，见 PSH-R4-001 |
| PSH-R3-003 旧公开-MAC 迁移降级 | **部分有效** | 无 env 时旧格式被拒绝；`PYSHELD_ALLOW_LEGACY_STATE=1` 可迁移并把两份改为 keyed；未来 >7 天拒绝；但同一 env 下可重复迁移，未实现“一次性”；运行中还可绕过“状态消失”检查（PSH-R4-002，`logs/legacy-state-tests.log`） |
| PSH-R3-004 onefile 绑定可绕过 | **有效（守卫）** | 非 onedir `_MEIPASS`、自定义解压目录、override 含 `_MEI` 均被拒绝；设置 `PYSHELD_BINDING_FILE` 不能关闭 onefile 检查；onedir `_MEIPASS`/`_internal` 正常；env 换路径仍可重置绑定（等价双删，已承认）。拒绝文案建议设 `PYSHELD_BINDING_FILE`，在 onefile 下无效（PSH-R4-003，`logs/binding-guard-tests.log`） |
| PSH-R3-005 构建机临时 flag/secret | **有效（敏感数据）** | TEMP 中未发现新 flag、挑战 master/build key、真实 `*.shield-secret.json`；仅有两份 2 字节测试 fixture。仍保留 64 个旧 staging/CI/exe 副本（无敏感内容，见 PSH-R4-004，`logs/temp-hygiene.log`） |
| PSH-R3-006 原生 keyring oracle / 无产物 | **部分验证/残余** | 源码仍导出 `master_key()`，CI 的 `diagnose_native_runtime.py` 自己断言它等于 master；`check_native_build.py` 只扫描完整 master 的 Base85/raw/hex，掩码分段通过。本轮没有本地 native 产物，无法独立验证 `.pyd/.so` 的 strip/PDB/CodeView；以 CI 记录为准 |
| PSH-R3-007 自保护残余 | **残余（文档准确）** | 样本 28 个 stub、无 secret/pyc/`runtime_template`；`_runtime_bundle` 解包为 8 个明文 runtime 模板（无 `_keyring.py`，含 `__PYSHELD_BUILD_ID__` 占位符）；selfbuild exe 的 CArchive 无 `.py`、无 runtime datas，但 PYZ 中 keyring/master/business 可离线恢复（`logs/sample-audit.log`、`logs/exe-selfbuild.log`） |
| 格式 3 运行时降级 | **有效** | 挑战许可证 format 3、无 `build_key`；v1 盐解封被 `CryptoError` 拒绝；运行时无 v3→v2 回退 |
| `_keyring` 替代直接 master 常量 | **有限/残余** | 去掉 `MASTER_KEY_B85`，字符串扫描无完整 key；但一次 `master_key()` 调用即可恢复；selfbuild exe 的 PYZ 中同样；属于文档已承认的成本项 |
| 300 秒 TTL 重验 | **有效（文档准确）** | `_state_for_load()` 双检查锁未发现 TTL 竞态；16 进程 reload/TTL 测试 0 失败；`_integrity` 仍只扫描一次，文档已写明 |
| 单实例启动（本轮新增） | **有效（基本）** | 主 exe 与 selfbuild exe 第一次启动保持运行，第二次启动退出码 0；进程树只保留一个应用实例（PyInstaller bootloader/子进程除外，`poc-fixed/single-instance.log`） |
| 最终 exe 与测试 | **有效** | 两个 exe 均提供且哈希匹配；`python -m unittest discover -s tests` → **178 tests OK (skipped=1)**，skip 为原生工具链；CI 页面快照显示 43676490706/729/789 均 success（本地无 `gh`，未下载 artifact） |

## 3. 漏洞记录

### PSH-R4-001 首次运行绑定写入仍使用固定 `.tmp`，并发启动会间歇失败

- 严重度：Low
- CVSS 3.1：`CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:N/I:N/A:L`，约 **2.5**
- 受影响组件：`_shield_runtime._core._write_binding_record` / `_check_first_run_binding`
- 受影响产物与 SHA-256：`round3-django-shielded.zip` `03fd153b94028f6c0180dcb53584866da3a414fbaa3e96ebaadb5ea19117a2fa`；本地 first-run 测试构建 `4832077b68153d3e`（非交付物）
- 源码版本/提交：`develop @ 8adeccf`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0
- 前置条件/攻击者模型：许可证 `bind_mode=first-run`；8–16 个进程同时启动并共享同一绑定目录；本地用户权限
- 状态：新发现（第三轮时钟并发修复未覆盖绑定写）

#### 摘要

第三轮把时钟状态写入改为唯一临时文件 + 共享冲突重试 + 跨进程锁，但首次运行绑定仍走自己的 `_write_binding_record()`：固定使用 `binding.json.tmp` / `binding.json.anchor.tmp`，`os.replace` 没有重试，primary 与 anchor 两次写入之间也没有锁。16 个进程同时首次启动时，部分 worker 会因临时文件共享冲突直接 `LicenseError`；完整应用启动测试未复现，但该竞态窗口真实存在。

#### 根因

`pyshield/runtime_template/_shield_runtime/_core.py:529-547`：

```python
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(record, ...), encoding="utf-8")
os.replace(temporary, path)
```

- `.tmp` 路径对所有进程相同；两个进程 `write_text`/`replace` 同一路径会得到 `WinError 32`（文件被另一个进程使用）或 `Permission denied`。
- `_check_first_run_binding()`（`:623-661`）在 primary 与 anchor 之间没有持有 `_state_lock()`；两次写入是独立的。
- 时钟使用的 `pyshield/secure_store.py:179-212` 已经实现 `mkstemp` 唯一临时名 + 重试，但绑定写没有复用它。
- `_read_binding_record()` 读取目标文件时，另一个进程的 `os.replace` 也可能失败；绑定写没有读取重试。

#### 复现步骤

1. 解压 `PyShieldBuilder-protected-sample-round3.zip`，从 `_shield_runtime/_keyring` 取得 build key（测试用）。
2. 将 `run/binding` 清空，设置 `PYSHELD_BINDING_FILE=<空目录>/binding.json`。
3. 同时启动 16 个 `binding_worker.py`（每个进程只调用一次 `_check_first_run_binding(build_key)`）。
4. 观察部分进程返回 `LicenseError ... WinError 32` 或 `Permission denied`；成功进程的记录最终一致，重试可恢复。

#### 最小 PoC

```powershell
python review\round3\poc-fixed\binding_concurrency.py `
  review\round3\extracted\sample\selfbuild-protected-sample <build-key-hex> 16 5
```

worker 的核心只有：

```python
sys.path.insert(0, TREE)
from _shield_runtime import _core
_core._check_first_run_binding(bytes.fromhex(os.environ["BK"]))
```

#### 影响

- 失败的 worker/进程以 `LicenseError` 退出，应用启动失败；对多 worker 的 Django/WSGI 首次部署是可用性问题。
- 当前观测中成功进程会重写两份记录，最终记录一致；理论上若 primary 与 anchor 的 `bound_at` 来自不同进程，会留下 `bindings disagree`，需要删除两份绑定记录才能恢复（本次未复现）。
- 不提供明文、机器绑定绕过或授权伪造能力；属于可用性/部署可靠性问题。

#### 证据

`logs/binding-concurrency-sample.log`：

```text
round 0: failures=1  ERR LicenseError ... [Errno 13] Permission denied: ...binding.json.anchor...
round 3: failures=1  ERR LicenseError ... [WinError 32] ...
round 4: failures=2  ... binding.json / binding.json.anchor ...
TOTAL (5 rounds x 16): 4/80 failures
```

`logs/firstrun-concurrency.log`：完整 `manage.py` 启动 16×5 全部 0 失败（进程在 license/import 阶段被拉开，未命中窗口）。`binding-concurrency` 的 recovery 轮（预置 primary、16 进程补 anchor）0 失败。

#### 修复建议

1. 让 `_write_binding_record()` 复用 `secure_store.write_private()`（唯一临时文件 + `_retry_oserror`），或实现同等的 `mkstemp` + 重试。
2. 在 `_check_first_run_binding()` 的整个 read-modify-write 外层使用 `_state_lock()` 或独立的绑定锁；锁路径建议基于 primary。
3. 用同一个 `bound_at` 原子写两份；失败时允许单份成功并在下次读时对齐（与时钟一致）。
4. 增加 16 进程 × ≥10 轮的 CI 回归测试，断言 0 失败、无 `.tmp` 残留、两份 `bound_at` 一致。

#### 修复后验证

重跑 `binding_concurrency.py <tree> <key> 16 10`，预期 `TOTAL_FAILURES 0`；再跑 `firstrun_concurrency.py` 16×10，预期 0 失败、`bindings disagree` 不出现。仅修复时钟锁不覆盖本问题。

#### 残余风险

即使修复，能删除两份绑定记录的人仍可重置 first-run 绑定（已承认边界）；`PYSHELD_BINDING_FILE` 换路径也可重置。

---

### PSH-R4-002 旧公开-MAC 的 opt-in 迁移不是一次性，且可在运行中绕过“状态消失”检查

- 严重度：Low
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N`，约 **3.3**
- 受影响组件：`_shield_runtime._core._read_clock_file` / `_allow_legacy_state`
- 受影响产物与 SHA-256：`round3-django-shielded.zip` `03fd153b94028f6c0180dcb53584866da3a414fbaa3e96ebaadb5ea19117a2fa`；`PyShieldBuilder-protected-sample-round3.zip` `ddb14d1b...364320`
- 源码版本/提交：`develop @ 8adeccf`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0
- 前置条件/攻击者模型：部署显式设置 `PYSHELD_ALLOW_LEGACY_STATE=1`；攻击者可写状态文件（本地用户）；若要绕过许可证到期，还需能回拨系统时间（通常需要管理员/root）
- 状态：部分修复

#### 摘要

默认配置下旧公开-MAC 状态已被拒绝，这是有效改进。但“仅允许一次性迁移”没有在代码中实现：只要 env 仍为 1，任何写状态目录的人都可以再次注入旧格式状态，运行时再次接受并改写成 keyed。运行中删除两份状态会触发 `state disappeared`，而写入旧格式状态可以绕过该检查并重置状态。

#### 根因

`_core.py:382-415` 在 keyed `open_state()` 失败后，只要 `_allow_legacy_state()` 为真，就调用无 secret 的 `open_state()`；`_allow_legacy_state()`（`:307-310`）仅检查环境变量，没有迁移标记/一次性文件。`_read_clock_locked()`（`:470-500`）发现 `legacy=True` 后重写 keyed 状态，但没有记录“已迁移”或删除 env。

旧格式的 MAC 是公开材料的 SHA-256（`secure_store.state_key(..., secret=None)`），不需要 build_key；因此只要 env 打开，旧格式就是可写入口。

#### 复现步骤

1. 用 sample tree 派生 build key，设置 `PYSHELD_STATE_DIR=<空目录>`。
2. 用 `secure_store.seal_state(payload, machine, "pyshield-clock")` 写两份旧格式状态。
3. 不设 env 调用 `_check_clock(build_key)` → `LicenseError`，提示设置 `PYSHELD_ALLOW_LEGACY_STATE=1`。
4. 设 env=1 再调用 → 成功，两份文件变为 keyed。
5. **再次**写入旧格式状态，env 仍为 1，再调用 → 仍然成功（无一次性限制）。
6. 运行中先成功调用一次（`_clock_started=True`），删除两份状态 → `state disappeared`；再写入旧格式 + env=1 → 调用成功并重置。

#### 最小 PoC

```powershell
python review\round3\poc-fixed\legacy_state_tests.py `
  review\round3\extracted\sample\selfbuild-protected-sample <build-key-hex>
```

`logs/legacy-state-tests.log` 中关键结果：

```text
default_reject_legacy   -> LicenseError, requires PYSHELD_ALLOW_LEGACY_STATE
opt_in_migrate          -> ok, both records keyed
opt_in_repeat           -> ok again (no one-time marker)
after_env_unset         -> rejected
future_keyed_30         -> LicenseError (implausible future date)
future_legacy_30        -> LicenseError
future_keyed_1          -> ok, last_seen=tomorrow (skew=1 day)
mixed_default           -> rejected
mixed_opt_in            -> migrated
```

运行中测试（`logs/legacy-midprocess.log`）：

```text
initial             2026-11-15
after_delete        LicenseError: clock state disappeared while the program was running
legacy_after_delete 2026-11-15   # env=1 + legacy file accepted
```

#### 影响

- 默认部署不受影响：无 env 时旧状态被拒绝。
- 显式 opt-in 的部署若长期保留 env，任何能写状态目录的本地用户可重复注入旧格式；结合系统时间回拨可重置/绕过时钟回滚检测。
- 能力上限仍接近“删除两份状态”的已承认边界，但它可以在运行中绕过 `state disappeared`，且不需要 build_key。没有直接恢复明文或伪造许可证签名。

#### 修复建议

1. 把迁移改成真正一次性：成功迁移后写一个 keyed marker（或删除专用 legacy-bootstrap 文件），后续即使 env=1 也拒绝旧格式。
2. 更简单：只提供一次性 CLI `pyshield migrate-state`，不要保留运行时 env 开关；或在迁移成功后由启动器 unset env。
3. 对 legacy 记录的 `last_seen` 限制在 `[today - grace, today]`（不要接受未来日期），并拒绝 `legacy=True` 时同进程 `_clock_started` 的重置。
4. 在 CI 中增加“env 保持 1 时第二次 legacy 注入必须失败”的测试。

#### 修复后验证

`legacy_state_tests.py` 预期：第一次 opt-in 迁移成功；第二次 legacy 注入必须 `LicenseError`；`future_keyed_1` 仍按 skew 语义处理或明确拒绝。运行中删除两份后再注入 legacy 应继续 `LicenseError`。

#### 残余风险

删除两份状态的重置边界无法在纯离线客户端消除；opt-in 迁移若被长期开启，仍会重新打开公开-MAC 写入路径。

---

### PSH-R4-003 onefile 拒绝提示建议了在 onefile 下无效的 `PYSHELD_BINDING_FILE` 变通

- 严重度：Informational
- CVSS 3.1：N/A
- 受影响组件：`_core._check_first_run_binding()` 的错误文案 / `_binding_paths_are_volatile()`
- 受影响产物与 SHA-256：`round3-django-shielded.zip` `03fd153b...a2fa`（runtime pyc）
- 源码版本/提交：`develop @ 8adeccf`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0
- 前置条件/攻击者模型：onefile first-run 绑定；无攻击能力，仅部署/运维
- 状态：新发现（文档/UX）

#### 摘要

onefile 守卫本身有效：`_MEIPASS` 非 onedir 时直接返回 volatile，`PYSHELD_BINDING_FILE` 不会改变结果。但错误提示仍写着“or set `PYSHELD_BINDING_FILE` to a persistent path”；在 onefile 下照做仍然失败，容易误导部署人员。

#### 根因

`_core.py:604-621`：

```python
base = getattr(sys, "_MEIPASS", None)
if base:
    ...
    if not _is_onedir_meipass(extraction):
        return True          # early return: override 不会被检查
override = os.environ.get(BINDING_FILE_ENV)
...
```

`_check_first_run_binding()`（`:623-630`）的提示文案却把 `PYSHELD_BINDING_FILE` 列为可选方案。

#### 复现步骤

1. 设置 `sys._MEIPASS=<任意非 onedir 目录>`（模拟 onefile）。
2. 设置 `PYSHELD_BINDING_FILE=<持久目录>/binding.json`。
3. 调用 `_binding_paths_are_volatile()` → `True`；调用 `_check_first_run_binding()` → `LicenseError`，文案仍建议设置 `PYSHELD_BINDING_FILE`。
4. 将 `_MEIPASS` 改为 `sys.executable.parent`（onedir 模拟）后同一 override 才被接受。

#### 最小 PoC

```powershell
python review\round3\poc-fixed\binding_guard_tests.py `
  review\round3\extracted\sample\selfbuild-protected-sample <build-key-hex>
```

关键输出：

```text
onefile_custom_mei_override_normal: volatile=true, binding=LicenseError
  message: ... use a fixed fingerprint, build onedir, or set PYSHELD_BINDING_FILE to a persistent path
onedir_meipass_override_normal:     volatile=false, binding=ok
```

#### 影响

- 不构成安全绕过；onefile 仍然 fail-closed。
- 运维人员按提示设置 env 后仍失败，可能误判为 bug 或降低对 first-run 绑定的信任。

#### 修复建议

把文案改为“onefile 下 `PYSHELD_BINDING_FILE` 不能使绑定持久化；请使用固定指纹或 onedir 构建”，或明确说明 override 只在非 onefile/onedir 路径生效。若产品希望 onefile 也支持持久绑定，应在 `_binding_paths_are_volatile()` 中先检查 override 是否指向非 `_MEI` 的持久路径，但这会改变当前安全决定，需要单独评审。

#### 修复后验证

用上面的 PoC 重跑，确认 onefile 提示不再建议无效的 override；onedir 仍可绑定。

#### 残余风险

onefile 下 first-run 绑定本身无法持久；这是设计边界。

---

### PSH-R4-004 构建机仍保留多份 staging/CI 副本（敏感数据已清理）

- 严重度：Informational
- CVSS 3.1：N/A
- 受影响组件：构建机 `%TEMP%` 发布卫生
- 受影响产物与 SHA-256：不适用（构建机环境）
- 源码版本/提交：`develop @ 8adeccf`
- 测试环境：Windows 10 Pro 19045 x64
- 前置条件/攻击者模型：能读取构建机 `%TEMP%` 的本机用户
- 状态：观察/发布卫生（非产品漏洞）

#### 摘要

第三轮报告中的高敏感残留（挑战源码、含 master/build/signing key 的 `*.shield-secret.json`）已不再出现：TEMP 扫描对新 flag、挑战 master/build key、真实 secret 文件均为 0 命中。但仍保留 64 个 `pyshield*`/`psh*` 入口，包括旧 sample staging 目录、CI HTML 页面、`psh-release-exe.exe` 主 exe 副本、smoke 目录等；它们不含本轮敏感值，但说明“旧构建目录”清理不彻底。

#### 根因

构建/测试脚本使用 `tempfile.mkdtemp`/固定名目录后没有统一清理；CI 下载页面和本地 smoke 目录也留在 `%TEMP%`。没有发现真实 secret，因此这不是第三轮 PSH-R3-005 的重复漏洞。

#### 复现步骤

```powershell
python review\round3\poc-fixed\temp_hygiene.py
```

输出：`matching_entries` 64，`sensitive_hits` 空，`real_secret_files` 空；仅两份 2 字节的 `old.shield-secret.json` 测试 fixture。

#### 最小 PoC

`temp_hygiene.py`（扫描名称匹配 `pyshield*`/`psh*` 的入口，并对文件/子树做 flag、master、build key 字节扫描）。

#### 影响

- 无 flag/master/build key/真实 secret 泄露。
- 旧 sample 目录可能包含各自 build 的 keyring（评审样本，不是客户发布物）；若构建机共享，仍建议清理。

#### 修复建议

1. 构建/测试用 `TemporaryDirectory` 或统一 `finally` 清理；CI 下载页面放在专用工作目录。
2. 发布前用脚本扫描 `%TEMP%`、构建输出、`*.shield-secret.json`，命中即失败。
3. 不要把 `psh-release-exe.exe` 等副本长期留在 TEMP。

#### 修复后验证

重跑 `temp_hygiene.py`，预期 `matching_entries` 仅剩正在运行所需的临时目录，`sensitive_hits`/`real_secret_files` 为空。

#### 残余风险

构建机本机用户始终可读取其 TEMP；敏感构建应在专用账户/机器上完成。

## 4. 残余风险

| ID | 残余风险 | 说明 |
| --- | --- | --- |
| R-01 | master key 可恢复 | `_keyring.pyc` 1,679 B；`master_key()` 约 0.4–2.3 ms；13 个模块全部解密约 5.5–5.9 ms（热）/约 90–107 ms（冷）；自保护 exe 的 PYZ 同样可恢复 |
| R-02 | 运行时内存明文 | code object、解密缓冲区在进程内存中；`_wipe` 只是 best effort |
| R-03 | 双删重置 | 同时删除时钟/绑定两份记录可重置本地历史；`PYSHELD_BINDING_FILE` 换路径同理 |
| R-04 | 提取 master 后可伪造状态/绑定 MAC | 任何状态文件在拿到 build key 后都可重签 |
| R-05 | legacy opt-in 长期开启 | 重复注入旧格式可重置/回滚状态（PSH-R4-002） |
| R-06 | 单份状态不可读 | 一份状态是目录/权限拒绝时 fail-closed，可能造成启动失败；这是安全取舍，不是绕过 |
| R-07 | POSIX 锁无超时 | `fcntl.flock` 阻塞等待；挂起的持锁进程可造成本地启动挂起（Windows 有 5 s 超时） |
| R-08 | DPAPI 降级 PLAIN1 | DPAPI 失败仍降级为明文包装（带 HMAC），文档已写明 |
| R-09 | 自保护 exe 残余 | `integrity=off`、bundle 仅压缩、中间树明文；PYZ 中 keyring/master/business 可离线恢复 |
| R-10 | 原生 keyring oracle | 原生扩展仍导出 `master_key()`；strip/PDB/CodeView 是 best effort；无 Authenticode/TPM/服务端 |
| R-11 | 300 秒重验不覆盖完整性 | 清单只在进程首次加载扫描；文档已写明 |
| R-12 | 构建机 staging 残留 | 敏感值已清；旧 sample/CI 副本仍存在（PSH-R4-004） |
| R-13 | 单实例本地 DoS/旁路 | `Local\` mutex 可被同会话进程占用；`PYSHELD_ALLOW_MULTI_INSTANCE=1` 是显式旁路；不构成跨用户提权 |
| R-14 | 未来日期 +1 天 | `STATE_MAX_FUTURE_DAYS=7`、`CLOCK_SKEW_DAYS=1`；`last_seen=today+1` 会被接受并冻结一天，属于设计 skew |
| R-15 | 源码部署在临时目录 | 无 `_MEIPASS` 时 first-run 绑定不会检测 dist 是否位于 `%TEMP%`；临时目录被清理后绑定丢失 |
| R-16 | 每用户状态 | 新 OS 用户可重置；文档建议机器级 anchor 或服务端时间 |
| R-17 | 无 Authenticode/TPM/服务端 | 已承认的离线客户端边界 |

## 5. 修复优先级

1. **P0（可用性）**：把 `_write_binding_record()` 切到 `secure_store.write_private()`，并给 `_check_first_run_binding()` 加跨进程锁；用 16 进程 × 10 轮 CI 回归。
2. **P1（状态降级）**：实现真正的一次性 legacy 迁移（marker/CLI），或迁移成功后要求移除 env；拒绝运行中 legacy 重置。
3. **P2（部署体验）**：修正 onefile 错误文案；考虑无 `_MEIPASS` 时检测 `%TEMP%` 路径。
4. **P3（原生/自保护残余）**：继续明确文档；如要进一步提高成本，可考虑不导出 `master_key()`、增加原生产物指纹/符号验证、Authenticode。
5. **P4（卫生）**：CI/本地统一清理 TEMP staging；发布扫描 `*.shield-secret.json`、flag、master/build key。
6. **回归**：保留 flag 构建期断言（解密后 `co_consts` 含 flag）和 `flag-sha256.txt` 校验。

## 6. 证据与复现命令

所有脚本与日志在 `review/round3/poc-fixed/`；样本只用于授权评估，不得再分发。

```powershell
# flag
python recover_flag.py <challenge-tree>

# keyring 成本（5 次取中位数）
python keyring_timing.py <challenge-tree> 5

# 时钟 / 启动 / TTL 并发
python clock_concurrency.py <tree> <build-key-hex> 16 5
python startup_concurrency.py <challenge-tree> 16 3
python clock_failure_tests.py <sample-tree> <build-key-hex>

# 绑定并发 / onefile 守卫
python binding_concurrency.py <sample-tree> <build-key-hex> 16 5
python binding_guard_tests.py <sample-tree> <build-key-hex>

# 旧状态
python legacy_state_tests.py <sample-tree> <build-key-hex>

# exe / 样本
python exe_extract.py PyShieldBuilder.exe main
python exe_extract.py dist-selfbuild\PyShieldBuilder-selfbuild.exe selfbuild
python sample_audit.py <sample-tree>
python artifact_scan.py
python temp_hygiene.py

# 单元测试
python -m unittest discover -s tests
```

关键日志：

- `logs/recover-flag.log`：flag 与哈希、key/build_key 的 SHA、耗时。
- `logs/keyring-timing.log`：5 次运行与中位数。
- `logs/clock-concurrency-sample.log`、`logs/clock-concurrency-challenge.log`、`logs/startup-concurrency-challenge.log`：0 失败。
- `logs/binding-concurrency-sample.log`：直接并发 4/80 失败。
- `logs/firstrun-concurrency.log`：完整应用 16×5 0 失败。
- `logs/legacy-state-tests.log`：默认拒绝、opt-in 迁移、重复迁移、未来日期。
- `logs/legacy-midprocess.log`：运行中删除状态 vs 注入 legacy 状态。
- `logs/license-v1-metadata.log`：format 3、无 `build_key`、v1 拒绝。
- `logs/binding-guard-tests.log`：onefile/onedir/override 守卫。
- `logs/clock-failure-tests.log`：单份写失败/双份失败/不可读/fail-closed。
- `logs/exe-selfbuild.log`、`logs/exe-main.log`：CArchive/PYZ、keyring、master/build key SHA、bundle。
- `logs/sample-audit.log`：28 个 stub、bundle 8 项、无 keyring。
- `logs/challenge-check.log`、`logs/challenge-reveal.log`、`logs/challenge-verify.log`：`manage.py check`、`reveal` 与 `_shield_runtime verify --json`（21 个文件）均通过；`reveal` 只打印 `protected demo command OK`，不打印 flag。
- `logs/artifact-scan.log`、`logs/temp-hygiene.log`：发布卫生。artifact scan 的 sample tree 中 `MASTER_KEY_B85`/`shield_secret` 命中是 `verify_sample.py` 的检查字面量和 UI 文案，不是真实 key 字节；zip/exe 的 flag/master/build-key 命中为 0。
- `logs/exe-main.log`：主 exe 无 keyring/`_core`，无 flag/master/build-key/private-key 字符串。
- `logs/tests.log`：178 通过 / 1 跳过。

报告中的哈希、build ID、时间均为本机 2026-11-15 的复核结果；原文材料提供的 exe/zip 哈希全部匹配。

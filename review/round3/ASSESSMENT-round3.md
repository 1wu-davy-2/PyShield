# PyShield Round 3 独立安全评估报告

- 评估日期：2026-11-15
- 审查对象：`develop @ 9bd4b9f`；行为代码与样本构建于 `08f400a`
- 评估环境：Windows 10 Pro 10.0.19045 x64，CPython 3.12.0，Django 5.2.8，PyInstaller 6.22.3
- 方法：源码审阅、发布物解密 PoC、onefile PYZ 离线提取、时钟/绑定行为测试、8 进程并发测试、CI/构建脚本审阅、项目单元测试（172 通过 / 1 跳过）
- 前提：沿用第二轮边界——客户端完全离线、exe 必须自带解密能力；不要求机密性保证，只评估提取成本、状态回滚、许可证校验和发布卫生

## 0. 本轮最重要的结论：最终挑战包不含 flag

**从 `round3-django-shielded.zip`（SHA-256 `3ec82a84…304a2`）无法恢复 `demo.hidden_flag.FLAG`。**

- 解密最终包后，`demo.hidden_flag` 的代码对象只有：
  - `co_names = ('__doc__', 'RuntimeError', 'FLAG')`
  - `co_consts = ('This module deliberately refuses to run; the flag is encrypted in the stub payload.', 'hidden_flag is not an executable module')`
  - 反汇编只有 `__doc__` 赋值和 `raise`，没有任何 `FLAG` 赋值。
- 构建机上的源码是：

  ```python
  """This module deliberately refuses to run; the flag is encrypted in the stub payload."""

  raise RuntimeError("hidden_flag is not an executable module")

  FLAG = "PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8"
  ```

- `FLAG = …` 位于无条件 `raise` 之后；CPython 3.12 在编译期删除不可达代码。用该源码重新 `compile()` 得到的 `co_code`/`co_linetable` 与最终发布物**逐字节一致**，而 `co_consts` 中没有 flag（`logs/flag-rootcause.log`）。
- 最终 zip 的全文件原始字节扫描也没有 flag 或其 SHA-256（`logs/flag-scan.log`）。
- 我们确实得到了 flag 值，但它来自**构建机 `%TEMP%` 中的源码**和**更早的一次构建**（build `1d39fa1523ef46b0`，其加密 stub 的代码对象 const 中有 `FLAG`），不是来自最终交付 zip：

  ```text
  PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8
  SHA-256 = 46b5119e53dcb631ca73426deb23910af129b589c8a9f618da1663aa9448e3bc（匹配）
  ```

- 这是**挑战构建缺陷**，不是 PyShield 的产品漏洞。若本轮要求“必须从 zip 恢复”，则最终挑战不可解；需要修复后重发样本（见 PSH-R3-001）。

## 1. 结论摘要

| # | 结论 | 级别 |
| --- | --- | --- |
| 1 | 最终 zip 不含 flag；源码中 flag 在 `raise` 后被编译器删除；flag 仅存在于构建机/早期构建 | Informational（阻塞挑战） |
| 2 | `_keyring` 没有提高实质提取成本：pyc 中一次 `master_key()` 调用即可；自保护 exe 的 PYZ 中同样一次调用即可，且可离线解密业务模块 | 残余风险（已承认边界） |
| 3 | 时钟双写自愈本身有效；但多进程并发启动存在状态写入竞态，可导致 fail-closed 启动失败 | Medium（新发现） |
| 4 | 旧公开-MAC 状态迁移形成一次性伪造/降级路径：不需要 build_key 即可写入任意 `last_seen`，运行时随后会把它重新封装成 keyed 状态 | Medium（部分修复） |
| 5 | onefile 首次绑定保护是启发式；`PYSHELD_BINDING_FILE` 或非标准 `_MEIPASS` 布局可绕过；first-run 绑定仍可被删除两份记录重置 | Low（部分修复） |
| 6 | 构建机 `%TEMP%` 遗留挑战源码和两份构建密钥（含 master/build/signing key）；诊断输出与 flag 均可被本机用户读取 | Low（发布卫生） |
| 7 | 原生 runtime 的 keyring 仍是可调用的 oracle；strip/PDB 只有源码级验证，未提供原生产物 | Informational（部分验证） |
| 8 | 自保护样本 28 个 stub、无 secret/pyc/runtime_template、许可证 format 3、`integrity=off`、bundle 仅压缩；这些残余属性与文档一致 | Informational（残余） |

## 2. 第二轮修复项验证矩阵

| 第二轮问题 | 验证结论 | 证据/备注 |
| --- | --- | --- |
| 格式 3 运行时降级 | **有效** | 最终与早期样本运行时只实现 v3；许可证 `format=3`、无 `build_key`；v1 盐解封被 `CryptoError` 拒绝（`logs/poc-final.log`、`logs/poc-early.log`、`logs/sample-keyring.log`）。`licensing.open_license()` 仍有 v3→v2 兼容回退，但只用于签发/兼容工具侧，运行时不可达，不构成运行时降级 |
| 时钟双写部分失败会永久拒绝启动 | **部分有效** | 日期不一致时取 `max` 并重写两份；单份写失败不阻塞；两份都失败仍 raise。新增并发竞态（PSH-R3-002）；一份不可读时抛出 `SecureStoreError` 而非 `LicenseError` |
| 状态 MAC 密钥公开，可伪造 | **部分有效** | keyed MAC 本身正确（`secret` 参与 HMAC）；但 `_read_clock_file` 保留旧公开-MAC 迁移，写入旧格式即可让运行时接受并重新封装（PSH-R3-003）；提取 master 后仍可伪造（R-01） |
| 300 秒重验不覆盖完整性 | **文档修正，代码未改** | `_state_for_load()` 双重检查锁正确，未发现 TTL 竞态；`_integrity` 仍只扫描一次，文档表述准确 |
| 每用户状态 → 新 OS 用户重置 | **残余** | 未修复；文档已说明多用户场景应使用 `PYSHELD_STATE_ANCHOR_DIR` 或服务端时间 |
| 自保护 `integrity=off`、bundle 仅压缩 | **残余，文档准确** | 样本 28 个 stub，无 secret/pyc/runtime_template；`_runtime_bundle.py` 为 stub；bundle 生成逻辑仍是 base64+zlib+zip；`integrity=off` 已在文档标注 |
| onefile + first-run 绑定写入 `_MEIPASS` | **部分有效** | 默认 `_MEI*` 临时目录会被拒绝；`PYSHELD_BINDING_FILE` 会关闭该保护；非标准 `_MEIPASS` 布局不被识别；first-run 仍可被删除两份记录重置（PSH-R3-004） |
| DPAPI 失败静默降级 PLAIN | **残余** | 与文档一致；`PLAIN1` 前缀仍被无条件接受 |
| `_wipe` 只清 bytearray | **残余** | best effort，未声称内存加密 |
| 最终 exe 未随材料提供 | **有效** | `PyShieldBuilder.exe`、`PyShieldBuilder-selfbuild.exe` 均提供，SHA-256 与 `SHA256SUMS.txt` 一致 |
| `_keyring` 替代 `MASTER_KEY_B85` | **有限** | 去掉直接常量、`strings` 不再命中；但函数调用即可重建；自保护 exe 的 PYZ 中同样可直接取到（R-01） |
| 原生 runtime（strip/PDB/keyring） | **部分验证** | 源码实现 strip/CodeView 清理；CI 的 `diagnose_native_runtime.py` 自己断言 `_keyring.master_key() == master`；未提供原生产物，无法独立复现（PSH-R3-006） |

## 3. 漏洞记录

### PSH-R3-001 最终挑战包不含 flag（挑战构建缺陷）

- 严重度：Informational（不是产品漏洞；阻塞本轮挑战目标）
- CVSS 3.1：N/A
- 受影响组件：`review/round3/round3-django-shielded.zip` → `demo/hidden_flag.py`（加密 stub）
- 受影响产物与 SHA-256：`3ec82a849b58e126b660f4632f45c209a5571a943139753867b036bcb46304a2`
- 源码版本/提交：样本构建 `08f400a`；文档 `9bd4b9f`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0、Django 5.2.8
- 前置条件/攻击者模型：读取最终 zip；无需执行程序
- 状态：新发现（挑战/构建缺陷）

#### 摘要

最终 `demo.hidden_flag` 的加密代码对象中不存在 flag 常量；源码把 `FLAG = …` 放在无条件 `raise` 之后，CPython 3.12 在编译期删除了该不可达赋值。最终 zip 全文件也没有 flag 或哈希。flag 值可从构建机临时目录的源码、或更早的构建（`1d39fa1523ef46b0`）恢复，哈希匹配。

#### 根因

构建机源码（`%TEMP%\pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py`）：

```python
"""This module deliberately refuses to run; the flag is encrypted in the stub payload."""

raise RuntimeError("hidden_flag is not an executable module")

FLAG = "PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8"
```

最终包解密后的代码对象与用上述源码重新编译的结果一致：

```text
compiled names: ('__doc__', 'RuntimeError', 'FLAG')
compiled consts: (docstring, 'hidden_flag is not an executable module')
payload names:   ('__doc__', 'RuntimeError', 'FLAG')
payload consts:  (docstring, 'hidden_flag is not an executable module')
co_code equal: True
co_linetable equal: True
flag string in consts: False
```

反汇编（`logs/flag-rootcause.log`）：

```text
  1  LOAD_CONST docstring
     STORE_NAME __doc__
  3  LOAD_NAME RuntimeError
     LOAD_CONST 'hidden_flag is not an executable module'
     CALL 1
     RAISE_VARARGS 1
```

也就是说，`FLAG` 只因为符号表收集而留在 `co_names`，值从未进入字节码或常量表。运行时即使解密成功，也无法从代码对象得到 flag。

#### 复现步骤

1. 解压 `round3-django-shielded.zip`。
2. 执行 `python review/round3/poc/poc_keyring_decrypt.py <解压目录>`（或本报告附带的 `logs/poc-final.log`）。
3. 观察 `demo.hidden_flag`：`names` 含 `FLAG`，但没有 `FLAG_CONST`。
4. 执行 `python review/round3/poc/flag_rootcause.py`（需把脚本中的最终树/源码路径改成本机路径）；它会把构建机源码编译结果与发布物代码对象逐字节比较。
5. 执行 `python review/round3/poc/scan_flag.py`；最终 zip 的 flag/hash 命中数为 0。
6. 对早期构建（build `1d39fa1523ef46b0`）执行同一 PoC，`FLAG_CONST` 出现，值为 `PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8`。

#### 最小 PoC

- `review/round3/poc/poc_keyring_decrypt.py`：从 `_keyring.pyc` 恢复 master，派生 build_key，解密全部 13 个模块。
- `review/round3/poc/flag_rootcause.py`：证明 `raise` 之后的赋值被 CPython 删除。
- `review/round3/poc/scan_flag.py`：证明最终 zip 不含 flag。
- 日志：`logs/poc-final.log`、`logs/poc-early.log`、`logs/flag-rootcause.log`、`logs/flag-scan.log`。

#### 影响

- 评审方无法按约定“从最终 zip 恢复 flag”；若强行提交结果，只能依赖构建机临时文件或早期构建，属于**非交付物路径**。
- 挑战可能被误判为已解决；厂商无法用该样本验证“加密模块中确实含有目标数据”。
- 这不是 PyShield 的机密性缺陷；flag 本身是测试值。

#### 证据

- 最终 zip：`3ec82a84…304a2`，flag/hash 命中 0。
- 最终代码对象：`co_names=('__doc__','RuntimeError','FLAG')`，`co_consts` 无 flag。
- 早期构建：`co_names=('FLAG',)`，`co_consts=('PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8',)`。
- 构建机源码路径见上；其 flag 的 SHA-256 为 `46b5119e53dcb631ca73426deb23910af129b589c8a9f618da1663aa9448e3bc`，与 `flag-sha256.txt` 一致。

#### 修复建议

1. 把 `FLAG = …` 放到 `raise` **之前**，或删除无条件 `raise` 后改为运行时可捕获的拒绝逻辑；确保赋值是可达的。
2. 构建/发布流水线增加断言：解密 `demo.hidden_flag` 后必须能在 `co_consts`（或嵌套 code object）中找到 flag；同时保证发布目录中没有明文 flag。
3. 重新生成 zip、`SHA256SUMS.txt`、`flag-sha256.txt` 和文档哈希。
4. 清理构建机 `%TEMP%` 中的挑战源码和早期构建（见 PSH-R3-005）。

#### 修复后验证

- 对新的 zip 执行同一 PoC，`demo.hidden_flag` 的 `co_consts` 中必须出现 flag；
- 对最终发布目录做原始字节扫描，flag 明文命中数必须为 0（允许存在于加密 stub 中）；
- 构建机扫描不得再发现 `hidden_flag.py` 源文件或 `*.shield-secret.json`。

#### 残余风险

构建机本地用户始终可以读取构建目录；如果构建机与评审机不隔离，flag 仍可能从构建残留泄漏。这不是最终交付物的问题，但会破坏挑战的可验证性。

### PSH-R3-002 并发时钟状态访问导致 fail-closed 启动失败

- 严重度：Medium
- CVSS 3.1：`CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:N/I:N/A:H`，约 4.6
- 受影响组件：`pyshield/runtime_template/_shield_runtime/_core.py`（`_read_clock`、`_write_clock`、`_read_clock_file`）；`pyshield/secure_store.py`（`write_private`、`read_private`）
- 受影响产物与 SHA-256：最终 Django zip `3ec82a84…304a2`；自保护 exe `3a3d0ecf…adcd2a`；样本 runtime `ce91e97cfd39c34e` 也可复现
- 源码版本/提交：`08f400a`
- 测试环境：同上；Windows 文件共享语义
- 前置条件/攻击者模型：本机用户；两个或更多进程同时导入受保护模块（Django 多 worker、Gunicorn、多实例启动）；对状态目录有写权限
- 状态：新发现

#### 摘要

多个进程同时执行首次时钟写入/自愈时，会因共享固定的 `.tmp` 文件名和缺少跨进程锁而发生读取/替换冲突。Windows 上表现为 `WinError 32` / `Permission denied`；一个进程可能两次写入都失败，于是 `_write_clock` 抛出 `LicenseError`，导致受保护模块导入失败、进程启动失败。8 进程 × 5 轮测试中 2/40 个 worker 失败。

#### 根因

`pyshield/secure_store.py:152-164`：

```python
temp = target.with_name(target.name + ".tmp")   # 固定临时文件名
with open(temp, "wb") as handle: ...
os.replace(temp, target)
```

`pyshield/runtime_template/_shield_runtime/_core.py:343-375` 的 `_write_clock` 顺序写主文件和锚点，单份失败会累计；两次都失败时 `written == 0` 并 raise。`_read_clock`/`_write_clock` 没有跨进程锁，两个进程会同时使用同一个 `*.state.tmp`，读取方也可能在 `os.replace` 期间看到共享冲突。

`_read_clock_file`（`_core.py:308-336`）只捕获 `open_state` 的 `SecureStoreError`，没有捕获 `read_private` 抛出的 `SecureStoreError`，因此竞争中的读取错误会以 `SecureStoreError` 逃逸，而不是统一的 `LicenseError`。

#### 复现步骤

1. 解压自保护样本，得到明文 runtime（或使用任意 `--runtime-pyc` 构建）。
2. 运行：

   ```powershell
   python review/round3/poc/race_driver.py
   ```

   该脚本启动 8 个 worker，每个 worker 调用 `_core._check_clock(secret)`，共 5 轮。
3. 观察输出：大多数轮次 8/8 成功；部分轮次出现 1–2 个失败，错误类似：

   ```text
   SecureStoreError cannot read ...\ce91e97cfd39c34e.state:
     [Errno 13] Permission denied
   LicenseError PyShield cannot record clock state in ...\ce91e97cfd39c34e.state:
     [Errno 13] Permission denied ...state.tmp -> ...state
   ```

4. 单进程多线程直接调用 `_read_clock` 也能复现 `WinError 32`（线程测试见 `logs/state-tests.log`）；生产代码在单进程内由 `_lock` 串行化，但**跨进程没有锁**。

#### 最小 PoC

`review/round3/poc/race_driver.py` + `state_race_worker.py`；日志 `logs/race.log`（2/40 失败）和 `logs/state-tests.log`（线程级 `WinError 32`）。

#### 影响

- 受保护模块导入失败、Django worker 启动失败、多实例启动失败；属于可用性问题，不是授权绕过。
- 失败是 fail-closed 的：不会放行未授权模块，但会让正常部署间歇性不可用。
- 首次运行、跨日首次启动、单份状态被删除后的恢复、许可证续期后的重验都会触发写入，因此不是只在“损坏状态”下发生。

#### 证据

- `logs/race.log`：8 进程 × 5 轮，2/40 失败。
- `logs/state-tests.log`：并发线程测试失败，错误含 `WinError 32` 和 `.tmp -> .state`。
- 失败进程返回非零；其余进程成功，说明是共享临时文件/替换冲突，而非权限配置错误。

#### 修复建议

1. `write_private` 使用唯一临时文件名（`tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)`），写完 `fsync` 后 `os.replace`。
2. 对主/锚点的“读-改-写”加跨进程锁（Windows `msvcrt.locking`，POSIX `fcntl.flock`），或至少对 `os.replace`/`read_private` 的共享冲突做有限重试 + 抖动。
3. 把 `_read_clock_file` 中 `read_private` 的 `SecureStoreError` 统一包装为 `LicenseError`；对**可恢复**的共享冲突先重试，再决定 fail-closed。
4. `_write_clock` 在两份都失败时，若错误是瞬态共享冲突（而非只读目录），重试而不是立即 `LicenseError`。
5. 增加 CI 用例：8–16 个进程同时首次启动，断言全部成功且两份状态最终一致。

#### 修复后验证

重复 `race_driver.py` 至少 20 轮（160 个 worker），要求零失败；检查两份状态日期一致、无 `.tmp` 残留；模拟只读目录时仍 fail-closed。

#### 残余风险

真正不可写的状态目录仍会拒绝启动（设计如此，文档已说明 `PYSHELD_ALLOW_VOLATILE_STATE=1` 的降级）。本问题只覆盖“可写但并发”的场景。

### PSH-R3-003 旧公开-MAC 状态迁移形成一次性伪造/降级

- 严重度：Medium
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N`，约 5.5
- 受影响组件：`_shield_runtime/_core.py::_read_clock_file` 的 legacy 回退；`pyshield/secure_store.py::state_key(secret=None)`
- 受影响产物与 SHA-256：最终 Django zip `3ec82a84…304a2`；所有带时钟状态的 pyc/native 构建
- 源码版本/提交：`08f400a`
- 前置条件/攻击者模型：本机用户；对 `~/.pyshield/*.state` 和锚点目录有写权限（与“删除两份状态”前提相同）
- 状态：部分修复

#### 摘要

新状态使用 `HMAC(build_key, …)`，但 `_read_clock_file` 在 keyed 校验失败时会回退到旧的公开-MAC 格式，并在 `_read_clock` 中把旧记录重新封装成 keyed 记录。攻击者不需要 build_key，只要写一份旧格式状态（MAC 密钥是 `sha256("pyshield-state-key-v1|purpose|machine")`，完全公开），运行时就会接受一次并把伪造日期“洗白”成新格式。

#### 根因

`_core.py:308-336`：

```python
try:
    record = _secure_store.open_state(data, machine, STATE_PURPOSE, secret)
except _secure_store.SecureStoreError:
    try:
        record = _secure_store.open_state(data, machine, STATE_PURPOSE)  # 公开 MAC
        legacy = True
    except _secure_store.SecureStoreError as exc:
        raise LicenseError(...) from exc
```

`_read_clock`（`:378-407`）在发现 `primary_legacy or anchor_legacy` 时调用 `_write_clock(seen, paths, secret)`，把攻击者选定的 `seen` 写成 keyed 状态。`secure_store.state_key`（`:108-112`）在没有 `secret` 时就是公开的 SHA-256。

#### 复现步骤

1. 在测试脚本中写入两份旧格式状态：

   ```python
   payload = {"format": 2, "build_id": BUILD_ID, "last_seen": "2020-01-01"}
   data = secure_store.seal_state(payload, machine, "pyshield-clock")  # 无 secret
   secure_store.write_private(primary, data)
   secure_store.write_private(anchor, data)
   ```

2. 调用 `_core._read_clock(build_key)`。
3. 观察：返回 `2020-01-01`，且两份文件随后都能用 `open_state(..., build_key)` 通过——旧值被迁移为 keyed 状态。
4. `logs/state-tests.log` 的 `legacy state accepted once` 和 `legacy state rewritten keyed` 两项即此路径。

#### 最小 PoC

`review/round3/poc/state_tests.py` 第 6 节；日志 `logs/state-tests.log`。

#### 影响

- 攻击者可把 `last_seen` 重置为任意过去日期，然后回拨系统时钟，绕过许可证到期/绑定历史（与“删除两份状态”同类的已知边界）。
- 也可写入未来日期，使 `_check_clock` 的 `today < seen - 1` 检查持续失败，形成**持久化**的启动拒绝（比删除两份状态的“重置”更隐蔽）。
- 不直接泄露明文或密钥；能力上限仍受“能写状态目录”约束，因此不高于已承认的“双删重置”边界，但它**部分推翻了 keyed MAC 的防篡改声明**。

#### 证据

- `logs/state-tests.log`：keyed MAC 正确拒绝无 secret；legacy 记录被接受并重写 keyed。
- `_core.py:314-326`、`:399-407`；`secure_store.py:108-112`。

#### 修复建议

1. 移除 legacy 回退，或在构建/许可证中增加显式 `allow_legacy_state` 迁移开关；高安全部署默认关闭。
2. 把迁移限制为“两个文件都是旧格式且来自同一 build_id”的一次性操作，并写入迁移标记；更彻底的做法是要求重装/重新初始化状态。
3. 对 `last_seen` 设置合理上限（例如不得晚于今天 + `CLOCK_SKEW_DAYS` 的若干倍），避免未来日期造成长期 DoS。
4. 文档明确：keyed MAC 只阻止“没有写权限的篡改”；有写权限的攻击者仍等价于双删重置。

#### 修复后验证

写入旧格式状态后，运行时必须拒绝（或仅在显式迁移开关下接受一次并留下审计标记）；未来日期的旧状态不能导致持续启动失败；keyed 状态仍能正常自愈。

#### 残余风险

同时删除两份状态仍可重置历史；这是离线客户端无法消除的边界。

### PSH-R3-004 Onefile 首次绑定保护是启发式，可被环境变量/布局绕过

- 严重度：Low
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N`，约 3.3
- 受影响组件：`_core.py::_binding_paths_are_volatile`、`_binding_paths`、`PYSHELD_BINDING_FILE` 迁移路径
- 受影响产物与 SHA-256：所有使用 `bind_mode=first-run` 的构建；本轮交付样本均为 `bind_mode=any`，因此未在交付物上直接触发
- 源码版本/提交：`08f400a`
- 前置条件/攻击者模型：本机用户；可控制启动环境变量或部署方式；许可证为 first-run 绑定
- 状态：部分修复

#### 摘要

默认 PyInstaller onefile 的 `_MEIPASS`（位于系统临时目录、名字以 `_MEI` 开头）会被拒绝，符合设计。但 `PYSHELD_BINDING_FILE` 一旦设置，`_binding_paths_are_volatile()` 直接返回 `False`，保护被关闭；若 `_MEIPASS` 不在 `tempfile.gettempdir()` 下或名字不以 `_MEI` 开头（自定义运行时目录/非标准 bootloader），也会漏检。首次绑定记录仍可通过删除两份记录重置（已知边界）。

#### 根因

`_core.py:489-500`：

```python
if os.environ.get(BINDING_FILE_ENV):
    return False
base = getattr(sys, "_MEIPASS", None)
...
return path.parent == temp and path.name.startswith("_MEI")
```

这是一个运行时启发式，不是构建期约束；`PYSHELD_BINDING_FILE` 是文档中的迁移路径，但同样可被用来关闭检查。`_check_first_run_binding` 在两侧记录都缺失且 `_binding_started` 为 False 时会重新绑定，因此删除两份记录即可重置（第二轮已承认）。

#### 复现步骤

1. 使用自保护样本 runtime，调用 `_binding_paths_are_volatile()`：
   - `sys._MEIPASS = <temp>\ _MEIfoo` → `True`；
   - 设置 `PYSHELD_BINDING_FILE=<任意路径>` → `False`。
2. 调用 `_check_first_run_binding(build_key)`：
   - 默认临时 `_MEIPASS` 下 → 抛 `LicenseError`；
   - 设置 `PYSHELD_BINDING_FILE` 后 → 成功写入 primary + `.anchor`；
   - 删除 anchor → 恢复；删除两份 → 重置为首次运行。
3. 把 `sys._MEIPASS` 设为一个非临时父目录的 `_MEIfake` 路径 → `_binding_paths_are_volatile()` 返回 `False`（漏检）。
4. 结果见 `logs/state-tests.log`。

#### 最小 PoC

`review/round3/poc/state_tests.py` 第 8 节。

#### 影响

- 非标准 onefile 布局下 first-run 绑定可能静默不持久（每次启动都重新绑定），部署者会误以为绑定生效。
- 具有环境变量控制权的用户可以绕过“拒绝 onefile”的检查；但 first-run 绑定本来就可以通过删除两份记录重置，因此没有超出已承认边界的新授权能力。
- 不影响 fixed/any 绑定模式。

#### 修复建议

1. 在**构建期**拒绝 `bind_mode=first-run` + onefile，或强制要求显式提供持久 `PYSHELD_BINDING_FILE`；不要只依赖运行时路径启发式。
2. 把 `PYSHELD_BINDING_FILE` 的优先级与 onefile 检测解耦：即使设置该变量，也仍应验证目标路径是否位于持久目录（例如排除 `_MEI*` 临时目录）。
3. 文档明确：first-run 绑定不是防复制的安全边界；需要更强绑定用 fixed 指纹或服务端。
4. 增加测试覆盖自定义 `--runtime-tmpdir`、`TEMP` 变化、相对路径和目录不可写。

#### 修复后验证

在 onefile + first-run 构建中，默认拒绝；设置持久 `PYSHELD_BINDING_FILE` 后允许并在重启后保持；设置到 `_MEI*`/临时目录仍拒绝；删除两份记录的已知重置行为不变。

#### 残余风险

first-run 绑定可被双删重置；这是离线客户端的设计边界。

### PSH-R3-005 构建机 `%TEMP%` 遗留挑战源码与构建密钥

- 严重度：Low（本机发布卫生；若构建机共享则升为 Medium）
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N`，约 3.3（本轮遗留的是测试密钥/测试 flag；若该目录包含正式构建密钥，C:H 时为 5.5）
- 受影响组件：构建流程，不是某个交付产物
- 受影响产物与 SHA-256：无（本机临时文件）
- 源码版本/提交：`08f400a`
- 前置条件/攻击者模型：对构建机 `%TEMP%` 有读权限的本地用户/备份/支持包采集
- 状态：新发现

#### 摘要

构建机临时目录仍保留：

1. 挑战源码 `...\pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py`，其中含明文 flag；
2. 两个诊断构建的完整 `*.shield-secret.json`：`psh-diag-out.shield-secret.json`（build `23e2ef21346f4db1`）和 `psh-diag2-out.shield-secret.json`（build `4e126e1cdd6d3833`），字段包含 `master_key`、`build_key`、`signing_key`（各 40 字符 Base85）；
3. 自保护构建的 `staging/` 明文源码树和 `protected/` 受保护树。

诊断输出的 build_id 未出现在本轮交付的两个 exe 中（已扫描），因此不等于已发布产物密钥泄漏；但这类残留会破坏构建机与评审机的隔离，并使 PSH-R3-001 的 flag 可被本机用户直接读取。

#### 根因

构建/诊断脚本使用 `%TEMP%` 作为工作目录，失败/取消/调试路径没有统一清理；`*.shield-secret.json` 是构建密钥文件，设计上应在构建完成后删除，但诊断构建保留了它们。

#### 复现步骤

```powershell
Get-ChildItem $env:TEMP -Recurse -Filter "*.shield-secret.json"
Get-ChildItem $env:TEMP -Recurse -Filter "hidden_flag.py"
```

检查 `psh-diag-out.shield-secret.json` 的字段（master/build/signing key 均存在，值不在此报告展开）。

#### 最小 PoC

```powershell
python - <<'PY'
import json
from pathlib import Path
p = Path.home() / "AppData/Local/Temp/psh-diag-out.shield-secret.json"
d = json.loads(p.read_text())
print(d["build_id"], sorted(d), [len(d[k]) for k in ("master_key","build_key","signing_key")])
PY
```

#### 影响

- 本机用户/备份/支持包可读取 flag；挑战的“仅能解密样本”隔离被破坏。
- 若诊断输出被分发或构建机被共享，持有 secret 的人可解密对应输出、伪造许可证/状态。
- 本轮已扫描交付 exe：secret 中的 build_id 不在主 exe/自保护 exe 中出现；因此未发现已发布产物密钥泄漏。

#### 证据

- 路径：`C:\Users\ww\AppData\Local\Temp\psh-diag-out.shield-secret.json`、`psh-diag2-out.shield-secret.json`、`pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py`。
- secret 字段：`master_key`/`build_key`/`signing_key` 长度均为 40；build_id 见上。
- 交付 exe 扫描：两个 build_id 命中数 0。

#### 修复建议

1. 构建/诊断使用专用工作目录（非 `%TEMP%`），设置受限 ACL；构建结束（含失败/取消）后统一清理。
2. `*.shield-secret.json` 在构建成功、失败和取消路径都 `unlink`；CI 增加“构建后临时目录无 secret”的断言。
3. 发布前扫描 `%TEMP%`、当前目录、输出目录中的 `hidden_flag.py`、flag 字符串、`*.shield-secret.json`。
4. 将构建机与评审机隔离；不要把构建工作区打包进支持材料。

#### 修复后验证

在构建机执行失败/取消/成功三种路径后，`%TEMP%` 中不得出现 `*.shield-secret.json`、挑战源码或 flag 明文；发布目录扫描命中 0。

#### 残余风险

本机管理员始终可以读取构建进程的内存/磁盘；这属于构建机信任边界，不是产品可修复项。

### PSH-R3-006 原生 runtime 的 keyring 仍是可调用 oracle；strip/PDB 仅有源码级验证

- 严重度：Informational（残余风险/部分验证）
- CVSS 3.1：N/A
- 受影响组件：`tools/diagnose_native_runtime.py:48`、`tools/check_native_build.py:73-91`、`pyshield/builder.py:582-662`、`.github/workflows/native-runtime.yml`
- 受影响产物与 SHA-256：本轮未提供原生产物；CI 记录 `08f400a` run 43665646763 / 43666681749
- 源码版本/提交：`08f400a`
- 前置条件/攻击者模型：读取原生产物；本地/离线
- 状态：部分验证

#### 摘要

源码层实现了 `strip --strip-unneeded`、删除 `.pdb/.ilk/.exp/.lib`、清空 PE debug/CodeView directory；CI 的 `check_native_build.py` 扫描“完整 master key 的 Base85/raw/hex”并检查 `.py`/sidecar。**但**同一套 CI 的 `diagnose_native_runtime.py:48` 又直接断言：

```python
assert _keyring.master_key() == master, "keyring master mismatch"
```

这说明原生扩展把 `master_key()` 作为 Python 可调用函数导出；`strip` 不会删除 Cython 的方法表/函数名字符串。因此：

- “无完整 master key 常量”是字面事实（掩码分段不在扫描 needle 中）；
- master 仍可通过一次导入 + 一次调用恢复，和 pyc 的 oracle 等价；
- 没有提供原生产物，本机也没有 MSVC/LLVM strip 工具链，无法独立验证 `.pyd/.so` 的符号、CodeView、PDB 路径清理。

#### 复现步骤（若拿到 CI 原生产物）

1. 解压 `native-runtime-windows-latest` / `native-runtime-ubuntu-24.04`。
2. `python -c "from _shield_runtime import _keyring; print(_keyring.master_key().hex())"`。
3. 用返回的 master 派生 build_key，按 `poc_keyring_decrypt.py` 的路径解密业务模块。
4. 对 `.pyd/.so` 执行 `strings`/`llvm-objdump` 检查 PDB/CodeView；`check_native_build.py --phase layout` 只检查 sidecar 扩展名，不检查内嵌 CodeView 字符串。

#### 最小 PoC

`review/round3/poc/poc_keyring_decrypt.py`（对原生 runtime 目录同样调用 `_keyring.master_key()`）；`poc_exe_extract.py` 已证明 pyc/selfbuild exe 的同一 oracle。

#### 影响

- 原生 runtime 主要提高的是静态符号/PDB/`.py` 明文成本，不是密钥提取成本；文档已把 master 提取列为残余风险，这一点一致。
- 若对外表述“无完整主密钥”而不说明可调用 oracle，容易被误读为机密性保证。
- 未发现新的远程/供应链可利用路径。

#### 证据

- `tools/diagnose_native_runtime.py:41-49`：`from _shield_runtime import _crypto, _core, _keyring`，并断言 `_keyring.master_key() == master`。
- `.github/workflows/native-runtime.yml:80-81`：CI 执行该诊断。
- `tools/check_native_build.py:78-89`：只扫描完整 key 的三种表示。
- `pyshield/builder.py:542-570`、`:573-579`：strip/清理实现。
- 本机无原生构建工具链；未独立下载 CI artifact（GitHub Actions artifact 需要认证）。

#### 修复建议

1. 文档统一表述：native 只消除 `_core.py` 中的直接常量、`.py` 明文和部分符号；`master_key()` 仍是 oracle，等同于已接受的 master 提取边界。
2. 若目标是真正的密钥保密，转向 TPM/硬件密钥/服务端解锁；仅靠 native 编译无法实现。
3. CI 增加 `master_key()` 导出的显式检查/记录，避免把“无字符串常量”误当成“无法提取”。
4. 对 native 产物补充 `strings`/符号/CodeView 检查，并把 artifact 一并提供给审查方。

#### 修复后验证

native 产物中不应有完整 key 常量；`master_key()` 的行为应在文档中明确；strip/CodeView 检查应在 CI 中对实际产物执行，而不只检查 sidecar 文件名。

#### 残余风险

master 提取、运行时明文、客户端许可证校验、未签名 exe、无 TPM/服务端——均为已承认边界。

### PSH-R3-007 自保护样本残余属性复核（integrity=off / bundle 仅压缩 / 中间树明文）

- 严重度：Informational（残余风险；文档准确）
- CVSS 3.1：N/A
- 受影响组件：`dist-selfbuild/PyShieldBuilder-selfbuild.exe`、`PyShieldBuilder-protected-sample-round3.zip`
- 受影响产物与 SHA-256：`3a3d0ecf…adcd2a`、`7a87d55e…00b23`
- 源码版本/提交：`08f400a`
- 状态：残余风险（非新漏洞）

#### 摘要

- 样本树：28 个业务模块全部为 PyShield stub；48 个文件、701,139 字节；无 `.pyc`、`__pycache__`、`*.shield-secret.json`、`runtime_template/`；`_runtime_bundle.py` 为 stub；`_shield_runtime` 为明文源码 + `_keyring.py`（掩码分段）；许可证 format 3、无 `build_key`（`logs/sample-verify.log`、`logs/sample-keyring.log`）。
- `_runtime_bundle.py` 的生成逻辑（仓库构建器）是 `base64.b64decode` + `zlib.decompress` + `zipfile`，不是加密；文档已说明。
- selfbuild 样本的 `.pyshield-build.json` 为 `integrity: "off"`；`_scan_integrity` 直接返回空报告，因此 stub/runtime 被替换不会被检测。
- 自保护 exe：`CArchiveReader` 显示顶层只有 `PYZ.pyz` 和 `pyshield.lic` 等，没有 `_shield_runtime/*.py` 明文 datas；但 PYZ 内含 `_shield_runtime._keyring`，离线执行该代码对象即可恢复 master（build `26a4b0cc151aa56b`），并用它解密 `pyshield.builder` / `gui.backend`（`logs/exe-poc.log`）。这说明“无明文 datas”成立，但“业务/密钥不可提取”不成立。
- 中间树（`--stage-only`）确实含明文 runtime 和可重建的 keyring；SAMPLE-NOTICE 已警告不要发布该目录。

#### 复现步骤

1. 解压样本 zip，运行 `verify_sample.py`。
2. 用 `sample_keyring.py` 打开许可证、解密 `pyshield.builder`。
3. 用 `poc_exe_extract.py` 对 selfbuild exe 执行离线 PYZ 提取。
4. 检查 `.pyshield-build.json` 的 `integrity` 字段。

#### 最小 PoC

`review/round3/poc/sample_keyring.py`、`poc_exe_extract.py`；日志 `logs/sample-verify.log`、`logs/sample-keyring.log`、`logs/exe-poc.log`。

#### 影响

这些是已承认的自保护边界：`integrity=off` 不防篡改、bundle 不保密、中间树不可发布、exe 内 master 可提取。没有发现超出文档的新问题；如果对外宣称“自保护 exe 无法提取源码/密钥”，则与事实不符。

#### 证据

样本 28 个 stub、48 个文件；许可证 format 3/无 build_key/v1 拒绝；selfbuild exe PYZ 中 `_shield_runtime._keyring` 可执行并返回 master；业务模块可解密。

#### 修复建议

1. 保持文档中的“成本项/非机密性边界”表述；不要在产品页写“源码不落地/无法还原”。
2. 若需要完整性，把 selfbuild 的 `integrity` 改为 `protected` 或至少 `full`（注意构建产物中的哈希/清单自身也要被包含）。
3. 若需要更强保护，使用 `--runtime-native`、Authenticode、TPM/服务端；这些不能消除离线明文，但能提高验证/提取门槛。

#### 修复后验证

重跑 `verify_sample.py`、`sample_keyring.py`、`poc_exe_extract.py`；确认文档与实测一致；若启用 integrity，篡改 stub 应被拒绝。

#### 残余风险

运行时明文、双删重置、客户端许可证校验、未签名 exe、无 TPM/服务端，均为离线客户端边界。

## 4. 残余风险与已知边界（不计入漏洞）

### R-01 master key 提取成本（本轮重点验证）

**步骤（从发布目录到业务明文）**：

1. 读 `_shield_runtime/_keyring.pyc`（1,646 字节），跳过 16 字节 pyc 头，`marshal.loads`，`exec`，调用 `master_key()` → 32 字节 master。**1.4 ms**（进程内，含 pyc 解析/执行）。
2. 读 `.pyshield-build.json` 的 `build_id`；`HKDF(master, salt="pyshield-build-key-v1", info=build_id)` → build_key。含 `_crypto` 导入约 **52 ms**。
3. 对每个 stub：Base85 解码 → `open_envelope` → `zlib.decompress` → `marshal.loads`；13 个模块合计 **2.4 ms**，单模块中位数 **0.17 ms**。
4. 端到端新进程（含 Python 启动）**257 ms**（`logs/poc-final.log`）。

**自保护 exe**：用 PyInstaller `CArchiveReader` 读 `PYZ.pyz`，取出 `_shield_runtime._keyring` 代码对象并执行 → master；build `26a4b0cc151aa56b`；master 指纹（SHA-256）`a0be8e2e99a03aae7a37be0f05bb29dfb37ce16a13052fa8c90cb9ca8a66331d`；成功解密 `pyshield.builder`、`gui.backend`（`logs/exe-poc.log`）。全程不运行 exe。

**结论**：keyring 只消除了“可被 `strings`/正则直接命中的完整常量”；对静态分析者而言，成本仍是一次函数调用。与第二轮相比，**没有实质提高**（第二轮是 `MASTER_KEY_B85` 正则 + HKDF；本轮是 `master_key()` + HKDF）。文档（`docs/security-hardening.md:55`、`:153`）已承认这是成本项；建议把“提高逆向成本”限定为“阻止旧 PoC 的直接常量扫描”，不要暗示抗静态分析。

**native**：CI 自己的 `diagnose_native_runtime.py:48` 断言 `_keyring.master_key() == master`，说明 native 扩展同样导出 oracle；`check_native_build.py` 只扫描完整 key，掩码分段通过。strip/PDB 清理是符号成本，不是密钥成本。

### R-02 运行时内存明文

解密后的 code object、`marshal`/`zlib` 中间结果不可避免出现在内存；`_wipe` 只清 `bytearray`，`bytes` 副本无法清。调试器/内存 dump 可获取。与文档一致。

### R-03 双份状态都被删除

可重置时钟/绑定历史；这是离线客户端的设计边界。PSH-R3-003 只是让该能力更隐蔽，不是新的授权绕过。

### R-04 客户端许可证校验

可 patch runtime/许可证判断；离线无法阻止。需要服务端/硬件才能改变边界。

### R-05 未签名 exe / 无 Authenticode

`PyShieldBuilder.exe`、selfbuild exe 均未签名；SmartScreen 提示；无法验证发布者。CI 也未提供签名链路。

### R-06 无 TPM/服务端解锁

主密钥必须在客户端可用；TPM/服务端是文档中的后续方向，本轮未实现。

### R-07 DPAPI 失败静默降级 PLAIN

`secure_store.protect()` 在 Windows DPAPI 失败时回退 `PLAIN1`；`unprotect()` 无条件接受该前缀。状态仍有 HMAC，但 keyed MAC 的 secret 可提取，且旧公开-MAC 迁移存在（PSH-R3-003）。

### R-08 300 秒重验不覆盖完整性

`_integrity` 在进程内只扫描一次；启动后替换、尚未导入的 stub 不会被重扫；`verify` 手动命令可发现，但长驻进程不会自动发现。文档已准确说明；如需覆盖，应把缓存改为带 TTL，并评估大目录哈希开销。

### R-09 每用户状态 / 新 OS 用户重置

主状态 `~/.pyshield`、锚点 `%LOCALAPPDATA%\PyShield\state` 按用户隔离；新 OS 用户得到全新时钟。`PYSHELD_STATE_DIR` 设置后锚点退化为主文件同目录 `.anchor`，双记录分离度下降。文档已说明多用户场景应使用 `PYSHELD_STATE_ANCHOR_DIR` 或服务端时间。

### R-10 自保护中间树 / integrity=off / bundle 压缩

`--stage-only` 树含明文 runtime、`_keyring.py`、业务 stub 的加密载荷；`_runtime_bundle.py` 仅 base64+zlib+zip；selfbuild 的 `integrity=off`。三者均已在文档标注；不能作为发布物。

### R-11 `_wipe` best effort

只清 `bytearray`，`open_envelope` 返回的 `bytes`、`zlib.decompress` 的 `bytes` 副本未清；不构成内存保密。

### R-12 状态读取异常类型 / 单份不可读

`_read_clock_file` 对 `read_private` 的 `SecureStoreError` 未包装为 `LicenseError`；一份状态不可读时启动失败（fail-closed）。已在 PSH-R3-002 的修复建议中覆盖。

### R-13 `assert_no_master_key` / CI key scan 的局限

只扫描完整 Base85/raw/hex 主密钥；`_keyring` 的掩码分段和 native 方法表均通过。该检查可防止“直接常量回归”，不是密钥提取防护。

## 5. 修复优先级建议

1. **修复挑战包（阻塞）**：把 `FLAG` 放到 `raise` 之前；重新构建；用解密 PoC 断言 `co_consts` 含 flag；清理 `%TEMP%`；重算所有哈希（PSH-R3-001、PSH-R3-005）。
2. **修复时钟并发竞态**：唯一临时文件名 + 跨进程锁/重试；把 `read_private` 的 `SecureStoreError` 统一包装；增加 8–16 进程 CI 测试（PSH-R3-002）。
3. **收紧旧状态迁移**：默认移除或加显式开关；限制未来日期；文档明确“keyed MAC 不防有写权限的攻击者”（PSH-R3-003）。
4. **把 onefile 绑定改为构建期约束**：拒绝 first-run + onefile，或强制持久 `PYSHELD_BINDING_FILE`；不要只依赖 `_MEIPASS` 启发式（PSH-R3-004）。
5. **构建机卫生**：专用工作目录、ACL、失败路径清理 secret、发布前扫描（PSH-R3-005）。
6. **文档表述**：keyring/native 只提高“直接常量扫描”成本；master 提取、integrity TTL、双删重置是残余风险，不要写成已解决（R-01、R-08、R-13、PSH-R3-006）。
7. **原生产物可验证性**：CI 提供 artifact 或哈希+下载方式；对实际 `.pyd/.so` 执行 strings/CodeView 检查（PSH-R3-006）。

## 6. 附录

### 6.1 交付物哈希（已复核）

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| `PyShieldBuilder.exe` | 49,237,881 B | `486fd67e4e23efd30c582529437076b6690d38232a4ff24662b96c334644b0f9` |
| `dist-selfbuild/PyShieldBuilder-selfbuild.exe` | 40,552,051 B | `3a3d0ecfacd5fe2c02c0203f60ac6158ebe69ea85fae3a2526a86fd5f9adcd2a` |
| `review/round3/PyShieldBuilder-protected-sample-round3.zip` | 423,760 B | `7a87d55eb55443c99019b0678a530833f8a7c5589c8ad40c6a8dcfefc9f00b23` |
| `review/round3/round3-django-shielded.zip` | 57,513 B | `3ec82a849b58e126b660f4632f45c209a5571a943139753867b036bcb46304a2` |

### 6.2 关键构建 ID / 密钥指纹（测试用）

| 对象 | build_id | master SHA-256 |
| --- | --- | --- |
| 最终 Django 挑战 | `c6f18270508c66ce` | `c4b42cb18f0a8f9e6c6cac2e81243f22ee29edfd3aaf6c3a94a1671faa7fc93e` |
| 早期 Django 挑战（含 flag） | `1d39fa1523ef46b0` | `270caa9cb7e4782e749ee62ae46bbd0fd24ee25f4ced168a6c88cb6b74dcc96b` |
| 自保护样本树 | `ce91e97cfd39c34e` | `c9fe5baadd294d3c735665bcfe57dd4ac55647518977adf065f93b43e23ea514` |
| 自保护 exe | `26a4b0cc151aa56b` | `a0be8e2e99a03aae7a37be0f05bb29dfb37ce16a13052fa8c90cb9ca8a66331d` |

最终 Django 挑战的完整 master 未在报告中展开；PoC 可通过 `_keyring.pyc` 直接恢复。自保护 exe 的完整 master 见 `logs/exe-poc.log`（测试构建）。

### 6.3 复现命令

```powershell
# 1. 最终 / 早期挑战：keyring -> master -> 解密
python review/round3/poc/poc_keyring_decrypt.py <解压目录>

# 2. 自保护 exe 离线提取
python review/round3/poc/poc_exe_extract.py dist-selfbuild\PyShieldBuilder-selfbuild.exe

# 3. 时钟/绑定行为
python review/round3/poc/state_tests.py

# 4. 多进程竞态
python review/round3/poc/race_driver.py

# 5. flag 根因与缺失证明
python review/round3/poc/flag_rootcause.py
python review/round3/poc/scan_flag.py

# 6. 基础验证
python -m unittest discover -s tests
python tools\embed_runtime.py --check
```

### 6.4 结论边界

本报告只评估在“客户端离线、exe 自带解密能力”前提下的静态/动态提取成本、状态回滚、许可证校验与发布卫生。它不声称 PyShield 能阻止有能力的本地攻击者最终获取明文或主密钥；master 提取、运行时明文、双删重置、客户端校验、未签名 exe、无 TPM/服务端均为已承认的残余风险，不应作为新漏洞重复计数。

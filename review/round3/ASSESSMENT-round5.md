# PyShield Round 3 第五轮修复复核报告（审计范文）

- 评估日期：2026-11-15
- 复核对象：源码 `develop @ d98b48e`（PSH-R5 主提交 `9365307`）；第五轮产物（构建器、自保护构建器、静态样本、挑战包）；`ROUND3-FIX-RESPONSE.md`、`ROUND3-INVITATION.md`、`review/round3/README.md`
- 测试环境：Windows 10 Pro 10.0.19045 x64；CPython 3.12.0（产物运行）、CPython 3.13.14（工具与单元测试）；Django 5.2.8；PyInstaller 6.22.3；本地 Cython 可用
- 边界：沿用离线客户端前提；不要求“攻击者拿不到明文”，只评估提取成本、状态回滚、许可证校验和发布卫生。`master_key()` 可调用、运行时内存明文、双删重置、DPAPI 降级、无 Authenticode、无 TPM/服务端仍是已承认的残余风险，本轮不重复计为漏洞。
- 性质：本报告是**修复后的复核**，不是独立第三方审计。结论只覆盖本轮可比对的产物与源码。

## 0. 结论

**PSH-R5-001（High，未签名字节码缓存绕过清单）已修复验证；PSH-R5-002（Low，发布检查覆盖不足）已修复验证（覆盖与表述），保留一条能力边界。** 未发现新的可利用问题；本轮自查出的 1 条 Informational（runtime pyc 的 `co_filename` 泄露构建机临时路径）已在提交内修掉。

| 编号 | 上轮级别 | 本轮状态 | 一句话结论 |
| --- | --- | --- | --- |
| PSH-R5-001 | High（约 7.8） | **已修复验证** | 挑战包不再携带 `__pycache__`；伪造 pyc 在导入前被 `unsigned bytecode cache` 拒绝，marker 未执行；正常运行不产生缓存 |
| PSH-R5-002 | Low（约 3.3） | **已修复验证（含残余）** | `--temp`/裸 zip 无密钥时 fail-closed；`--secret` 可检出裸 master/build key；带 `__pycache__` 的 zip/目录被拒绝；随机密钥仍无法按形状识别 |
| PSH-R5-003 | Informational | **已修复** | runtime pyc 的 `co_filename` 改为 `_shield_runtime/_core.py`，traceback 不再暴露构建机路径 |

已核对的事实：

- 挑战包 `round3-django-shielded.zip`：66,750 B，SHA-256 `a1ee83fa0ee9d7ae765f17e9fb12b371643945fc75d1bed9e507e5566f122320`，build `0d13b86aeefd81f8`，27 个条目，`__pycache__` 0 条。
- 静态样本 `PyShieldBuilder-protected-sample-round3.zip`：547,238 B，SHA-256 `984c701c8ef2a8b22753054d1098a7c7abfe6bacfcf69624ae18cc52c3966ac4`，build `692a5f580935dfaf`，41 个业务模块 stub，runtime 源码包含 `_isolate_bytecode`/`unsigned bytecode cache`。
- `PyShieldBuilder-r5.exe`：49,305,274 B，SHA-256 `504e89cc242714e2304e8898a639c5b885a98f5b96a0c784a6f6f8b901310200`（本机构建）。
- `PyShieldBuilder-selfbuild.exe`：40,711,334 B，SHA-256 `7dce130c9857d6899ac854041fddd1358c5953a53a798e63209531abc30c541e`（本轮重建）。
- flag 仍可离线恢复：`PSH-ROUND3-e3b2caa377c88630`，SHA-256 `a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce`，本轮耗时约 85 ms（key 3.4 / derive 27.1 / decrypt 54.4）。
- 全量测试：**245 tests OK (skipped=5)**。
- `d98b48e` **尚未触发 CI**；两枚 exe 均为本机构建，与第四轮 CI 产物不是同一 hash。

## 1. 修复项复核

### 1.1 PSH-R5-001 挑战包携带 `__pycache__`，伪造成员可绕过签名清单

- 严重度：**High**（发布目录可写时；目录仅管理员可写可降为 Medium）
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H`，约 **7.8**（沿用上轮评分，攻击复杂度与影响未变）
- 受影响组件：`round3-django-shielded.zip` 的 `demo/`、`demo/management/`、`sampleproj/` 缓存；`manifest.collect_files`；runtime `_scan_integrity`
- 受影响产物与 SHA-256：`round3-django-shielded.zip` `a1ee83fa…2320`（修复版；上轮受影响版本为 `34c5e49f…7954`）
- 源码版本/提交：`develop @ d98b48e`
- 测试环境：Windows 10 Pro 19045 x64、CPython 3.12.0、Django 5.2.8
- 前置条件/攻击者模型：能写发布目录的本地用户；无需管理员、调试器或内存 dump
- 状态：**已修复验证**

#### 摘要

上轮问题成立的前提是“发布物自带可执行缓存，而清单不覆盖 pyc”。本轮从三层同时关闭：发布物不含缓存、runtime 不读取发布目录缓存、完整性扫描把未签名缓存视为失败。

#### 根因（修复前）

- CPython 对 timestamp-based `.pyc` 只比较源文件 `mtime`/`size`，不校验内容；`demo/__init__.py` 在清单中，而 `demo/__pycache__/__init__.cpython-312.pyc` 不在。
- `manifest.collect_files()` 当时把 `__pycache__` 归入可变文件直接跳过；`builder.clean_pycache()` 只在构建末尾清理，打包步骤又手工把缓存带回 zip；`check_release.py` 不拒绝缓存。

#### 修复实现

| 层 | 代码 | 行为 |
| --- | --- | --- |
| 清单生成 | `pyshield/manifest.py::collect_files(forbid_bytecode=True)` | 写清单前扫描发布树，发现 `__pycache__` 或非 runtime `.pyc/.pyo` 直接 `ManifestError`；`_shield_runtime/` 下的 sourceless pyc 例外 |
| 清单校验 | `manifest.py::verify_files(check_bytecode=True)` | 除哈希外，报告未列入清单的字节码；`builder verify` 同步生效 |
| 构建收尾 | `builder.py::clean_pycache()` + `assert_no_bytecode_caches()` | 清理后再次断言；仍有残留（被占用/清理失败）则构建失败 |
| 导入隔离 | `runtime/_core.py::_isolate_bytecode()` | runtime 导入时把 `sys.pycache_prefix` 指向 temp 下不可预测且不存在的路径，并置 `sys.dont_write_bytecode = True`；之后导入既不读发布目录 pyc，也不写入 |
| 完整性 | `runtime/_core.py::_scan_bytecode_caches()` | 在首个受保护模块加载前，把 `_shield_runtime/` 之外的任何 `.pyc/.pyo` 报为 `unsigned bytecode cache` |
| 发布检查 | `tools/check_release.py` | 目录/zip 中出现 `__pycache__` 或未列入该包签名清单的 `.pyc` 直接失败 |

#### 复现步骤（攻击者视角）

1. 解压或复制挑战包到可写目录。
2. 读取 `demo/__init__.py` 的 `mtime`/`size`。
3. 生成 `demo/__pycache__/__init__.cpython-312.pyc`：头为 `MAGIC + flags(0) + mtime + size`，body 为 `marshal.dumps(compile("print('PYSHELD_PYC_BYPASS_R5')", ...))`。
4. 运行 `python manage.py check`。

#### 最小 PoC

```powershell
py -3.12 review\round3\poc-round5\pycache_tamper_r5.py `
  review\round3\extracted\round3-django-shielded
```

实测（`logs/pycache-tamper-r5.log`）：

```text
planted demo/__pycache__/__init__.cpython-312.pyc 232 bytes
... _core.py", line 299, in _enforce_integrity
_shield_runtime._core.LicenseError: PyShield package integrity check failed
(demo/__pycache__/__init__.cpython-312.pyc: unsigned bytecode cache); ...
returncode: 1
OK: forged pyc was rejected before it could execute
```

marker `PYSHELD_PYC_BYPASS_R5` 未出现在 stdout/stderr，说明缓存没有在拒绝之前执行。

#### 覆盖与绕过尝试

| 场景 | 期望 | 实测 | 依据 |
| --- | --- | --- | --- |
| 发布物自带缓存 | 不存在 | zip 27 条目、`__pycache__` 0 条 | `zipfile` 枚举 + `check_release.py` |
| 运行后产生缓存 | 不产生 | `pycache dirs: 0` | `logs/runtime-run.log`（含 `check`/`reveal`/`verify`） |
| 目录内伪造 pyc | 失败、不执行 | rc=1，`unsigned bytecode cache` | `logs/pycache-tamper-r5.log` |
| `PYTHONPYCACHEPREFIX` 指向投毒目录 | 被 runtime 覆盖、不执行 | rc=0，正常 check，marker 未出现 | `logs/prefix-hijack-r5.log` |
| `_shield_runtime/` 内替换/投毒 | 不在本项承诺内 | 未阻断（设计如此） | §4 R-01 |
| `integrity=off` 构建 | 不做清单校验 | 不在本项承诺内 | 文档已声明 |

未实测但由代码路径可判定：预置 `sys.pycache_prefix` 目标目录需要猜中 `os.urandom(8)` 名称；同一用户若能改 runtime 本身，等价于替换校验器（已承认边界）。

#### 影响

修复前：可写发布目录中的伪造 pyc 可执行任意代码，绕过“受保护文件被改会发现”的承诺，且交付物本身携带缓存。修复后：伪造缓存在正常布局下既不被执行，也会在首个受保护模块加载前触发完整性失败；发布物不再包含可执行缓存。

#### 残余风险

- 能替换 `_shield_runtime` 的攻击者仍可绕过所有清单校验（离线客户端的结构性边界）。
- `integrity=off` 的构建（自保护 exe）不执行清单扫描，只保留导入隔离。

---

### 1.2 PSH-R5-002 `check_release.py` 对 master/build key 与 pycache 覆盖不足，表述过宽

- 严重度：**Low**（沿用；工具/文档问题，未发现已发生的密钥泄露）
- CVSS 3.1：`CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N`，约 **3.3**
- 受影响组件：`tools/check_release.py`；`ROUND3-FIX-RESPONSE.md` 的发布卫生表述
- 受影响产物与 SHA-256：不适用（工具/文档）；挑战包 `a1ee83fa…2320`
- 源码版本/提交：`develop @ d98b48e`
- 前置条件/攻击者模型：构建/发布流程依赖该检查器；本地用户
- 状态：**已修复验证（覆盖与语义）；随机密钥不可判定是能力边界**

#### 摘要

上轮的两个缺口：`--temp` 给普通文件传空 needle；无 sibling secret 的 zip 不扫 master/build key；且两者都会打印无范围的 “release check OK”。本轮改为“要么真扫，要么显式失败”，并加入 `--secret` 与 TEMP 内真实 secret 的自动发现。

#### 修复实现

- 新增 `--secret PATH`（可重复）；needle 由 `master_key`/`build_key` 的 base85、原始字节、hex 三种形式组成。
- 目标扫描：优先使用 `--secret`，其次使用 `licensing.secret_path_for(target)` 的 sibling `*.shield-secret.json`。
- `--temp`：扫描 `pyshield*`/`psh*` 条目 + 顶层 `*.shield-secret.json`；在条目内递归发现真实 secret 并用于整轮扫描。
- 无 needle 时：目标扫描失败并提示 `--secret`；`--temp` 直接失败（`cannot verify master/build key leakage in TEMP`）。仅 `--skip-key-scan` 可显式接受缺口，且成功信息会注明“key scan skipped”。
- 目标树/zip：拒绝 `__pycache__` 条目；`.pyc/.pyo` 只有在包内 `.pyshield-manifest.json` 列出时才允许（runtime 的 sourceless pyc）。

#### 复现与判定

```powershell
py -3.12 review\round3\poc-round5\check_release_gap_r5.py `
  review\round3\extracted\selfbuild-protected-sample
```

实测（`logs/check-release-gap-r5.log`）：

| 场景 | 期望 rc | 实测 rc | 关键输出 |
| --- | --- | --- | --- |
| `--temp`，TEMP 有裸密钥，无 key 来源 | 1 | 1 | `cannot verify master/build key leakage in TEMP` |
| `--temp --secret <secret>` | 1 | 1 | `build key material in …\master.txt` |
| 裸 zip，无 sibling secret | 1 | 1 | `cannot verify … (pass --secret …)` |
| 裸 zip `--secret` | 1 | 1 | `build key material in …zip!/master.txt` |
| 挑战包（`--skip-key-scan`） | 0 | 0 | `release check OK … (master/build key scan skipped)` |
| 挑战包 + 人工加入 `__pycache__` 条目 | 1 | 1 | `bytecode cache directory inside release: …` |

#### 影响

修复前：构建机残留裸密钥、或 zip 无 sibling secret 时，检查器返回 0，且文档把 `--temp 0` 当作密钥卫生证据，属于过度保证。修复后：不能验证就失败，成功信息带范围；带缓存的包一律拒绝。

#### 残余风险

- 随机 32 字节密钥没有可识别形状；既无 `--secret` 也无 sibling secret 的裸 zip 只能报“无法验证”，不能给出“没有泄露”的结论。这是能力边界，不应写成已解决。
- 构建机仍需专用账户与清理流程；发布卫生是流程控制，不是机密性边界。

---

### 1.3 PSH-R5-003（Informational，已修复）runtime pyc 的 `co_filename` 暴露构建机路径

- 严重度：Informational
- 组件：`builder.compile_runtime()`；所有 pyc runtime 构建
- 状态：**已修复**

上轮产物中 `_shield_runtime/_core.pyc` 的 `co_filename` 是构建机临时输出路径（例如 `review\round3\.round3-django-shielded.<random>\...\_core.py`），一旦运行期抛异常会出现在 traceback 中，暴露构建目录布局与输出名。本轮把 `dfile` 改为相对路径 `_shield_runtime/<name>.py`。

验证（`logs/runtime-run.log`）：

```text
runtime co_filename: _shield_runtime/_core.py
```

同类问题仍存在于 PyInstaller 内置二进制的符号/路径（§4 R-15），不在本项范围。

## 2. 材料与声明核对

| 声明 | 核对结果 | 说明 |
| --- | --- | --- |
| 挑战包 SHA-256 | 匹配 `a1ee83fa…2320` | 66,750 B；27 条目 |
| 静态样本 SHA-256 | 匹配 `984c701c…6ac4` | 547,238 B；41 stub；build `692a5f58…` |
| `PyShieldBuilder-r5.exe` SHA-256 | 匹配 `504e89cc…0200` | 49,305,274 B；本机构建 |
| 自保护 exe SHA-256 | 匹配 `7dce130c…541e` | 40,711,334 B；本轮重建 |
| flag 可从 zip 恢复 | 是 | `PSH-ROUND3-e3b2caa377c88630`；hash `a036bf16…c4ce`；约 85 ms |
| zip 内无明文 flag | 是 | 解密后 `co_consts` 含 flag，zip 条目 0 命中 |
| zip 内无 `__pycache__` | 是 | 0 条；`check_release.py` 通过 |
| 受保护示例可运行 | 是 | `check` rc=0、`reveal` 输出 `protected demo command OK`、`verify --json` 21 文件 |
| 运行后不产生缓存 | 是 | `pycache dirs: 0` |
| 全量测试 | 245 OK / 5 skipped | 本地 CPython 3.13.14 |
| CI | **未运行** | `9365307` 未推送；invitation 已注明 |
| 文档表述 | 一致 | `--temp 0` 不再作为密钥证明；补充了 `--secret`/`--skip-key-scan` 语义 |

## 3. 回归范围

本轮改动集中在 `manifest`、`builder` 的发布/清理路径与 runtime 的导入/扫描路径；绑定、时钟、许可证、状态迁移代码未改动。因此：

| 项目 | 本轮是否复测 | 依据 |
| --- | --- | --- |
| 首次绑定 16×10 并发 | 未单独重跑 | `test_concurrent_first_run_binding_does_not_fail` 在全量测试中通过；代码未改动 |
| 时钟 8×2 并发 / TTL | 未单独重跑 | `test_concurrent_clock_writes_do_not_fail` 等通过；代码未改动 |
| legacy marker / 运行中拒绝 | 未重跑 | 代码未改动；上轮结论沿用 |
| onefile `_MEIPASS` 拒绝 | 未重跑 | 代码未改动；上轮结论沿用 |
| 原生 runtime strip/PDB | 未重跑 | 本机无工具链；仍需 CI |
| `_keyring` 提取耗时 | 未测 | 上轮 0.4 ms/6 ms 仍适用；本轮未改 keyring |
| 自保护 exe 静态检查 | 重建但未做符号级复核 | 仅确认可构建、无 `.py` 数据文件（构建日志） |

这不是“全部回归”，而是“改动面回归 + 全量单测”。对外表述应避免写成“所有历史问题都已复测”。

## 4. 残余风险

| ID | 风险 | 说明 |
| --- | --- | --- |
| R-01 | 字节码隔离可被同用户绕过 | 替换 `_shield_runtime`、预置 prefix 目录或修改解释器都可绕过；本项只保证“未签名缓存不执行” |
| R-02 | 随机密钥不可判定 | 无 `--secret`/sibling secret 的裸 zip 只能 fail-closed，不能证明无泄露 |
| R-03 | master key 可提取 | `_keyring.master_key()` 仍可调用 |
| R-04 | 运行内存明文 | code object/解密缓冲；`_wipe` best effort |
| R-05 | 双删/删 marker 重置 | 时钟/绑定双记录 + legacy marker 被删除后可重置 |
| R-06 | keyed `last_seen=today+1` | 1 天 skew 内接受 |
| R-07 | 单份状态不可读 | fail-closed，可能拒绝启动 |
| R-08 | POSIX 锁无超时 | 持锁进程挂起可造成启动挂起 |
| R-09 | DPAPI 降级 | `PLAIN1` + HMAC |
| R-10 | 自保护残余 | `integrity=off`、bundle 仅压缩、中间树明文 |
| R-11 | 原生 keyring oracle | `master_key()` 可调用；无 Authenticode/TPM/服务端 |
| R-12 | 300 秒重验不覆盖清单 | 清单只在首次加载扫描 |
| R-13 | 无 `_MEIPASS` 的 temp 部署 | first-run 绑定可能随临时目录丢失 |
| R-14 | 构建机 TEMP | 当前无敏感命中，仍需专用账户/清理 |
| R-15 | exe 未签名且本轮为本机构建 | 与 CI 产物 hash 不同；无 Authenticode；内置二进制的符号路径未逐一审计 |
| R-16 | 源 runtime 模式的 `_shield_runtime/__pycache__` | `_scan_bytecode_caches` 跳过整个 runtime 包；该目录下的缓存不被报告（与“替换 runtime 属已承认边界”一致） |

## 5. 证据与复现

```powershell
# 1) 运行与完整性
cd review\round3\extracted\round3-django-shielded
python manage.py check
python manage.py reveal
python -m _shield_runtime verify --json

# 2) 伪造 pyc（应失败且不执行）
py -3.12 review\round3\poc-round5\pycache_tamper_r5.py .

# 3) PYTHONPYCACHEPREFIX 投毒（应被忽略）
py -3.12 review\round3\poc-round5\prefix_hijack_r5.py .

# 4) 发布检查语义
py -3.12 review\round3\poc-round5\check_release_gap_r5.py `
  review\round3\extracted\selfbuild-protected-sample
python tools\check_release.py --skip-key-scan review\round3\round3-django-shielded.zip

# 5) flag 恢复
py -3.12 review\round3\poc-round4\recover_flag.py .
```

日志：`poc-round5/logs/pycache-tamper-r5.log`、`prefix-hijack-r5.log`、`check-release-gap-r5.log`、`check-release-artifacts.log`、`runtime-run.log`、`recover-flag-r5.log`。

## 6. 发送前检查清单

1. **推送 `d98b48e` 触发 CI**，把 native/build-develop/selfbuild 的 run 号补进邀请函；若改用 CI 产物，重新计算 exe 哈希。
2. 确认发送的 exe 与源码一致：本报告核对的 `PyShieldBuilder-r5.exe` 是本机构建，`PyShieldBuilder.exe` 仍是 `ae718c4` 的旧 CI 产物（不含修复），不要混发。
3. 重跑 `pycache_tamper_r5.py`、`prefix_hijack_r5.py`、`check_release_gap_r5.py`，把日志随包附上。
4. 不要再用 `--temp 0` 作为“无密钥泄露”的证明；需要密钥结论时提供 `--secret`。
5. 保留“离线客户端无法消除”的残余风险表述，不写成“已彻底解决”。

## 附录：本报告的写作结构（可复用）

1. **结论先行**：编号 / 上轮级别 / 本轮状态 / 一句话结论，四列表格。
2. **已核对事实**：产物哈希、build id、flag、测试数、CI 状态，只写可验证的数字。
3. **逐项复核**：每条沿用漏洞格式（严重度、CVSS、组件、产物、提交、环境、前置条件、状态、摘要、根因、复现、PoC、影响、残余）。
4. **修复实现表**：层 / 代码位置 / 行为，便于审查方按图索骥。
5. **绕过尝试表**：场景 / 期望 / 实测 / 依据，明确区分“实测”和“代码推断”。
6. **材料与声明核对表**：把邀请函里的每个数字都对一遍。
7. **回归范围**：明确“复测了什么、没复测什么、为什么”。
8. **残余风险表**：编号化，避免与漏洞重复计数。
9. **发送前清单**：可执行动作，而不是结论口号。

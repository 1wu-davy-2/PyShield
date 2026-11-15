# Round 3 修复说明与自评

- 源码：`develop @ 8adeccf`
- 日期：2026-11-15
- 性质：**开发方自评，不是独立审计报告**。独立结论以审查方按 `VULNERABILITY-REPORT-FORMAT.md` 提交的报告为准。
- 测试：`python -m unittest discover -s tests` → **178 tests OK (skipped=1)**；原生 runtime 测试因当前机器缺少 C 编译器和 strip 工具被跳过。

## 0. 第三轮报告修复（2026-11-15）

| 第三轮问题 | 处理 | 证据/限制 |
| --- | --- | --- |
| PSH-R3-001 最终挑战包不含 flag | 将 `FLAG` 移到无条件 `raise` 之前，重建挑战包；解密后断言 `demo.hidden_flag` 的 `co_consts` 含 flag；发布 zip 原始字节扫描无明文 flag/hash | `review/round3/round3-django-shielded.zip`；`poc_keyring_decrypt.py` 显示 `FLAG_CONST`；新 flag 哈希见 `flag-sha256.txt` |
| PSH-R3-002 并发时钟写入失败 | `write_private` 使用唯一临时文件并重试 Windows 共享冲突；时钟读改写加跨进程锁；`read_private` 错误统一包装为 `LicenseError`；新增 8 进程并发测试 | `pyshield/secure_store.py`、`_core.py::_state_lock`；`test_concurrent_clock_writes_do_not_fail` |
| PSH-R3-003 旧公开-MAC 状态迁移降级 | 默认拒绝 legacy 状态；仅在显式 `PYSHELD_ALLOW_LEGACY_STATE=1` 时允许一次性迁移；未来日期超过 7 天拒绝 | `_core.py::_allow_legacy_state`、`STATE_MAX_FUTURE_DAYS`；`test_legacy_clock_state_requires_explicit_opt_in`、`test_implausible_future_clock_state_is_rejected` |
| PSH-R3-004 onefile 绑定可绕过 | 任何非 onedir `_MEIPASS` 都视为临时；即使设置 `PYSHELD_BINDING_FILE`，也不能关闭检查；override 路径含 `_MEI*` 同样拒绝 | `_core.py::_binding_paths_are_volatile`；`test_binding_override_does_not_disable_onefile_guard` |
| PSH-R3-005 构建机临时残留 | 重建后清理挑战源码、诊断 secret 和旧构建目录；发布材料不包含 secret | 已在构建机清理；新 zip 扫描无 secret |
| PSH-R3-006 原生 keyring oracle | 不把原生编译表述为密钥保密；文档明确 `master_key()` 仍可调用 | 文档更新；未提供原生产物 |
| PSH-R3-007 自保护残余 | 保留 `integrity=off`、bundle 仅压缩、中间树明文的既有边界说明 | 样本重新构建并更新哈希 |

## 1. 对第二轮问题的逐项处理

| 第二轮问题 | 处理 | 证据/限制 |
| --- | --- | --- |
| 格式 3 运行时无降级，但旧链路只是多一步 HKDF | 保留格式 3；主密钥不再以单个 `MASTER_KEY_B85` 常量写入 `_core.py`；改为拆分/XOR 掩码的 `_keyring`；构建时扫描并拒绝完整密钥 | `pyshield/builder.py` 的 `render_keyring`/`assert_no_master_key`；`tests/test_builder.py::P0HardeningTests`；仍可从 `_keyring.master_key()` 或内存重建，见残余风险 |
| 时钟主/锚点部分写入失败会永久拒绝启动 | 双写允许单份成功；下次读取取较新日期并重写两份；第三轮增加唯一临时文件、共享冲突重试和跨进程锁 | `_core.py::_write_clock/_read_clock`；`test_clock_mismatch_self_heals_to_newer_value`；`test_concurrent_clock_writes_do_not_fail`；两份都不可写且未设置 volatile 时仍拒绝启动 |
| 状态 MAC 密钥公开，可伪造 last_seen/绑定，应改表述 | 时钟 MAC 改为 `HMAC(build_key, ...)`；绑定原本已使用 `build_key` 派生；文档改为“防误损坏/防单点丢失”，明确提取 master 后仍可伪造 | `pyshield/secure_store.py::state_key/seal_state/open_state`；`test_state_secret_changes_mac`；旧公开-MAC 状态改为显式 opt-in 迁移 |
| 300 秒重验不覆盖完整性清单 | 不改缓存策略，明确写入文档：清单只在进程首次加载时扫描一次 | `README.md`、`docs/security-hardening.md`、`docs/runbook.md`、`docs/implementation.md` |
| 每用户状态 → 新 OS 用户可重置时钟 | 默认仍按用户隔离；文档明确多用户场景应把 `PYSHELD_STATE_ANCHOR_DIR` 指向机器级 ACL 目录，或改用服务端时间 | 未做机器级目录自动迁移，避免无权限/ACL 复杂度和破坏现有部署 |
| 自保护 `integrity=off`、bundle 仅压缩 | 文档明确：自保护 exe 不做清单校验；`_runtime_bundle.py` 是 base64+zlib+zip，解包成本约一次解压 | `docs/selfbuild-poc.md`、`docs/security-hardening.md` |
| onefile + first-run 绑定写入 `_MEIPASS` | runtime 将非 onedir `_MEIPASS` 一律视为临时；即使设置 `PYSHELD_BINDING_FILE` 也不能关闭检查；提示改用固定指纹、onedir 或持久路径 | `_core.py::_binding_paths_are_volatile`；`test_binding_override_does_not_disable_onefile_guard` |
| DPAPI 失败静默降级 PLAIN | 文档明确写出降级行为；状态仍带 HMAC | 未改为强制失败，避免服务账户/无 DPAPI 环境直接不可用 |
| `_wipe` 只清 bytearray，bytes 未清 | 保留 best-effort 表述；文档列入残余风险 | 未声称内存加密 |
| 最终 exe 未随材料提供 | 本轮提供重新构建的 `PyShieldBuilder.exe` 与自保护 exe，并给出 SHA-256 | 见 `review/round3/README.md` |

## 2. P0 加固落地情况

1. **原生 runtime 默认（Pro/Enterprise）**
   - CLI：无显式 `--runtime-*` 且授权允许 `native-runtime`、本机具备 Cython/C 编译器/strip 时自动选择 native；缺工具链回退 pyc 并打印原因。
   - GUI：新增原生运行时选项；Pro/Enterprise 且工具链可用时默认选中；Trial/未授权锁定并回退字节码；设置页同样显示不可用原因。
2. **符号与 PDB/CodeView**
   - 原生扩展构建后执行 `strip --strip-unneeded`（或 `llvm-strip`/`objcopy`），删除 `.pdb/.ilk/.exp/.lib`，对 PE 清空 debug directory/CodeView 指针。
   - strip 失败会使构建失败；不再静默发布未清理的原生二进制。
   - PyInstaller spec 关闭 UPX；构建器打包 Cython/setuptools 以支持 GUI 原生编译。
3. **主密钥不再是可直接 strings 的常量**
   - `_core.py` 只调用 `_keyring.master_key()`；
   - `_keyring.py` 里是 8 段掩码十六进制，运行时重建；
   - `assert_no_master_key()` 在发布前扫描 runtime 目录，发现完整 Base85/原始/hex 主密钥即失败。

## 3. 验证记录

- 全量单元测试：178 通过，1 跳过（原生工具链缺失）。
- 冻结 exe 的隐藏 setup 子进程：已验证可导入 Cython、执行 `cythonize` 并正常退出（windowed bootloader 下需重定向 stdout/stderr）。
- 主 exe 冒烟：启动后存活 8 秒；自保护 exe 同样存活 8 秒。
- 受保护 Django 示例：`manage.py check` 通过，`manage.py reveal` 输出 `protected demo command OK`；`demo.hidden_flag` 直接导入按设计抛出 `RuntimeError`；解密后 `co_consts` 中存在 `FLAG`，发布物中未发现明文 flag；flag 哈希见 `SHA256SUMS.txt`。
- 受保护源码样本：28 个业务模块全部为 stub；无构建 secret、无 `.pyc`、无 `runtime_template`；`_core.py` 不含 `MASTER_KEY_B85`；`_keyring.py` 存在。
- 并发时钟：8 进程 × 2 轮测试全部成功，两份状态最终一致，无 `.tmp` 残留。
- 单实例启动：主 exe 与自保护 exe 同时启动时，第二个进程退出并尝试转发深链；任务管理器只保留一个应用实例（PyInstaller onefile 的 bootloader/子进程除外）。`PYSHELD_ALLOW_MULTI_INSTANCE=1` 仅为自动化并行测试入口。
- 原生 runtime 端到端构建：已由 GitHub Actions `native-runtime` 矩阵在 Windows 和 Ubuntu 上通过（提交 `8adeccf`，run `43675650384`；上一轮 `9bd4b9f` run `43666681749`）：
  - Windows 使用 Cython + MSVC + LLVM strip，验证 `.pyd` 导入、无 `.py` runtime、无 PDB 侧车、无完整主密钥；
  - Ubuntu 使用 Cython + gcc + binutils strip，验证 `.so` 导入、无 `.py` runtime、无完整主密钥；
  - 两个平台都运行了受保护 `manage.py check` 和 `_shield_runtime verify --json`，并上传原生产物。
  - 本机仍无 C 编译器/strip，因此本地不能重复该端到端构建；以 CI 结果作为本轮证据。
- 桌面构建器 CI：`build-develop` run `43675650496` 通过；Windows 单文件 exe 从 `ci-develop-latest` 下载并替换本地文件，SHA-256 见 `SHA256SUMS.txt`。自保护 exe 由 `selfbuild-poc` run `43675650448` 通过，本地使用同提交构建产物。

## 4. 仍需审查方重点挑战的点

- `_keyring` 的掩码分段是否仍可低成本恢复 master；`master_key()` 是否可以直接调用（预期仍是可接受的残余边界）。
- 旧公开-MAC 状态在默认配置下是否确实被拒绝；显式 opt-in 迁移是否仍留下可滥用路径。
- 双写自愈在 8–16 进程并发、单份不可写、锚点目录权限变化下是否仍安全。
- 原生 runtime 的 strip 是否破坏 Python 导入；CodeView 清理是否彻底；`.pyd` 是否仍含完整主密钥。
- onefile 绑定检测在自定义 `_MEIPASS`、`PYSHELD_BINDING_FILE` 和 onedir 布局下是否仍可绕过。
- 受保护 Django 示例的 flag 是否能在不运行程序的情况下从发布目录恢复。

## 5. 明确的残余风险

- 离线客户端无法阻止有能力的攻击者最终拿到明文和主密钥；
- 同时删除两份状态仍可重置本地历史；
- 状态 MAC 和绑定 MAC 在 master 被提取后都可伪造；
- 旧状态迁移开关若长期保留，会重新打开公开-MAC 伪造路径；
- 状态目录不可写时锁文件无法创建，退化为无锁读写（仍使用唯一临时文件和重试）；
- 5 分钟重验不覆盖完整性清单；
- 自保护 exe 不做清单校验，bundle 只是压缩；
- DPAPI 失败会降级为 `PLAIN1`；
- PyInstaller 内置二进制仍可能有 PDB 路径/符号；未接入 Authenticode。

这些是设计边界，不应被表述为“本轮已完全修复”。

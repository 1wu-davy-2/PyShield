# PyShieldBuilder 第二轮加固 — 独立复核结论

- 复核日期：2026-11-14
- 材料：`review/PyShieldBuilder-protected-sample.zip`（SHA-256 `1493e7e1…a566a5`，与 `SHA256SUMS.txt` 一致）、`review/REVIEW-BRIEF.md`、`docs/security-hardening.md`、源码
- 方法：源码逐行核对 + 样本实际解包/解密 + 159 个单元测试 + `tools/embed_runtime.py --check`
- 前提：按你们给定的边界评估——客户端离线、exe 自带解密能力；只讨论"提高成本/收紧回滚路径"是否成立，不按"绝对安全"评判

## 0. 结论摘要

| 关注点 | 结论 |
|---|---|
| 格式 3 降级 | **不存在运行时降级路径**。`_core.py` 只实现 v3，样本实测 v1 信封被 `CryptoError` 拒绝。但旧攻击链仍成立：master key 在发布物里，`build_key = HKDF(master, "pyshield-build-key-v1", build_id)`，只多一步 HKDF。 |
| 双记录绑定/时钟 | **堵住了单点删除**；**没有堵住伪造**（时钟锚点 HMAC 密钥完全公开；绑定 MAC 密钥可从 master key 派生）。另有"双写部分失败 → 永久不一致 → 拒绝启动"的可用性风险。 |
| 300 秒重验 | **没有发现新的绕过或竞争**，双重检查锁实现正确。但重验只覆盖许可证/绑定/时钟；**完整性清单仍是进程内一次性缓存**。 |
| 自保护样本 | 26 个包模块 + `manage.py` = 27 个 stub，无 `.pyc`、无 secret、无 `runtime_template`；许可证 format 3 且无 `build_key`。spec 未把 runtime 当 datas。**但最终 exe 未随材料提供，无法直接验证产物**；`_runtime_bundle.py` 只是 base64+zlib+zip（3 行可还原），且自保护构建 `integrity="off"`。 |
| 密钥隔离 | 样本 build_id `33d9f7faed18a913`，master/signing 均为该次构建随机生成；CI 每次全新构建；代码中未发现跨构建复用。发布 exe 只给了哈希、未给文件，无法做样本/正式包对比。 |

## 1. 格式 3 与降级路径

**运行时侧：无降级。** 证据：

- `runtime_template/_shield_runtime/_core.py:37` `LICENSE_FORMAT = 3`；`:39-41` 只定义 v3 盐/AAD；`:115-118` 只派生 v3 license key；`:550-562` 只用 v3 解封并强制 `payload["format"] == 3`。
- 样本 `_shield_runtime/_core.py` 中不存在 `pyshield-license-v1` 字符串，实测 v1 盐解封返回 `CryptoError`。
- `licensing.open_license()`（`pyshield/licensing.py:245-284`）确实有 v3→v2 回退，但它是**构建侧/签发侧**工具，运行时从未调用它；不会形成运行时降级。

**但旧链路只是加了一步 HKDF，不是结构性修复。** 用样本实测（不启动程序、不验签名、不修改文件）：

```
build_id=33d9f7faed18a913
license v3: OPENED format=3 build_key=False
license v1: rejected (CryptoError)
decrypted gui.backend: gui/backend.py
decrypted pyshield.builder: pyshield/builder.py
```

路径：`_shield_runtime/_core.py`（最终 exe 中为 PYZ 里的 pyc，字符串常量明文）→ `MASTER_KEY_B85` + `BUILD_ID` → `HKDF(master, BUILD_ID)` → 模块密钥 → 解密。与第一轮相比，差别仅是"读 .py 正则"变成"从 pyc 取字符串常量"，以及多一次 HKDF 调用。

格式 3 真正的收益：**单份 `pyshield.lic` 泄漏不再等于模块密钥泄漏**。对"拿到完整发布目录"的攻击者没有增益。

## 2. 双记录绑定与时钟

### 2.1 单点删除：已堵住

`_core.py:359-380`（时钟）与 `:462-495`（绑定）在任一记录缺失时从另一份恢复；两份在运行期同时消失会报错（`:370-376`、`:470-475`）。这部分实现正确。

### 2.2 伪造：没有堵住（新发现，文档未列出）

- 时钟状态的 HMAC 密钥是公开可推导的：`pyshield/secure_store.py:108-110`
  ```python
  state_key = sha256(f"pyshield-state-key-v1|{purpose}|{machine}")
  ```
  三个输入都可由本地攻击者获得（`python -m _shield_runtime fingerprint` 就打印机器指纹）。攻击者可以自己构造任意 `last_seen` 的记录；重新封装也不构成障碍——`secure_store.unprotect()` 无条件接受 `PLAIN1\n` 前缀（`secure_store.py:103-105`），连 DPAPI 都不需要。因此"状态被修改会拒绝启动"只对**误损坏**成立，对**有意伪造**不成立。
- 绑定记录同理：`_binding_mac` 的密钥是 `HKDF(build_key, …)`，而 `build_key` 可由 master key 派生。能提取 master key 的攻击者可以伪造任意指纹/任意 `bound_at` 的绑定记录，而不只是"删除两份"。

影响：这两项没有给攻击者带来**超出**"删除两份状态"的新能力（V1 提权后都能做到），但"防篡改"的表述不成立，应改为"防误删/防单点丢失"。

### 2.3 双写部分失败 → 拒绝启动（可用性风险，中）

`_core.py:334-356` 依次写主文件和锚点。单份写入本身是原子的（`secure_store.write_private` 走 `.tmp` + `fsync` + `os.replace`，`secure_store.py:140-152`），但**两份之间没有事务**。若第一份写成功、第二份失败（磁盘满、杀软锁、漫游配置不可写），主文件已是新日期、锚点仍是旧日期。下次启动 `_read_clock` 发现两份都存在但值不同（`:365-368`），直接抛 `LicenseError("records disagree")`，**代码不会自愈**——只有删除其中一份才能恢复。

绑定记录不受同一问题影响：首次绑定若只写成功主记录，下次启动会因锚点缺失走恢复分支（`:486-488`），用主记录的 `bound_at` 重建锚点，值一致，不会进入"disagree"。因此这是时钟状态独有的路径。

这不是绕过，但属于"一次瞬时写失败把安装变成砖"的路径。建议：

1. 两份都先写临时文件，再依次 `os.replace`；或
2. 不一致时取较新日期并重写两份（对齐），而不是硬失败；
3. 至少在错误信息里给出可执行的自救命令。

### 2.4 每用户状态 → 新 OS 用户可重置时钟（低-中）

主状态在 `~/.pyshield`，锚点在 `%LOCALAPPDATA%\PyShield\state`（`_core.py:255-295`），两者都按**用户**隔离。同一台机器上新建一个本地用户运行程序，会得到全新的时钟状态，`_check_clock` 视为首次运行并写入"今天"（可以是回拨后的日期），从而绕过回滚检测。若目标场景包含多用户共用一台机器，锚点应放机器级受 ACL 保护的位置（如 `%PROGRAMDATA%`）或服务端。

`PYSHELD_STATE_DIR` 一旦设置，`_state_anchor_dir()` 返回 `None`，锚点退化为主文件同目录的 `.anchor`（`:266-272`），双记录分离度下降；部署文档应提示这一点。

## 3. 300 秒周期重验

**没有发现新的绕过或竞争。** `_state_for_load()`（`_core.py:650-663`）在锁内二次判断 TTL，`_load_state` 在 RLock 内完成校验并一次性写入 `_state`/`_state_loaded_at`；并发导入只会触发一次重验，失败后所有后续导入一致失败。

**但重验不覆盖完整性。** `_enforce_integrity()`（`:220-234`）用进程级 `_integrity` 缓存，首次扫描后永不重扫。于是：

- 长驻进程启动后，被替换的 stub 若尚未导入（懒加载），下一次导入**不会**重新做 SHA-256 校验；
- 文档写"重新校验许可证、绑定和时钟"是准确的，但读者容易理解为清单也每 300 秒复核；建议明确写成"清单只在进程首次加载时校验一次"。

其余行为符合预期：许可证/绑定/时钟失败会让后续模块导入抛错（fail-closed）；没有受保护模块导入时不触发刷新（文档已注明）。

## 4. 自保护样本

### 4.1 已验证通过

对 zip 解包后的树逐项检查：

```
business modules: 26, stubs: 26, manage stub: True
pyc: 0  secret: 0  runtime_template: False
bundle stub: True
license v3: OPENED format=3 build_key=False  (bind_mode=any, expires=None)
license v1: rejected (CryptoError)
runtime LICENSE_FORMAT: 3 | legacy salt in runtime: False | placeholders left: False
decrypted gui.backend / pyshield.builder 均成功（用样本 master key 派生）
```

- 26 个 `pyshield/` + `gui/` 模块全部以 stub 开头，加上 `manage.py` 共 27 个，与 `.pyshield-build.json` 的 `modules: 27` 一致；
- 无 `.pyc`、无 `*.shield-secret.json`、无 `pyshield/runtime_template/`、无 `.pyx`；
- 许可证为 format 3、不含 `build_key`；v1 盐解封被拒；
- `_runtime_bundle.py` 本身也是 stub（其内容受保护）。

### 4.2 需要修正的表述/风险

1. **最终 exe 未提供。** `SHA256SUMS.txt` 列出了 `PyShieldBuilder-selfbuild.exe` 的哈希，但仓库里没有该文件；我只能确认"打包前的树 + spec"不包含 runtime datas，不能直接证明产物里没有。建议把自保护 exe 一并附上，或提供 CI artifact 的哈希+下载方式。
2. **`_runtime_bundle.py` 只是压缩，不是加密。** `pyshield/_runtime_bundle.py:284-292`：`zlib.decompress(base64.b64decode(BLOB_B64))` → `zipfile` → 直接读 `_core.py`、`_crypto.py`、`_machine.py`、`_secure_store.py`。文档已写"不是机密容器"，但"去掉解包后直接阅读源码的最低成本路径"这个说法偏乐观：成本是**一次解压**。
3. **自保护构建 `integrity="off"`**（`tools/selfbuild.py:141`）。这意味着自保护 exe **没有清单校验**：stub 与 runtime 被替换不会被检测。它只提高了静态阅读成本，这一点应在 `docs/selfbuild-poc.md` 里明确。
4. **onefile + first-run 绑定会失效（条件性）。** PyInstaller onefile 下 `_dist_dir()`/`_runtime_dir()` 指向 `_MEIPASS` 临时目录，绑定主记录和 `.anchor` 都会在进程退出时随临时目录消失，下次启动又是"首次运行"。本样本是 `bind_mode: any`，不受影响；但不要把 first-run 绑定的构建做成 onefile，除非把绑定路径迁出 `_MEIPASS`。
5. 样本树是 `runtime: source`（中间产物，PyInstaller 会编译），这符合说明；但如果有人直接分发这棵树，里面是**明文 runtime + master key**。保留 `selfbuild.py:360` 的警告，并避免把中间树作为发布物。

## 5. 密钥隔离

- 样本 `build_id=33d9f7faed18a913`，许可证内 `build_id` 一致；`bind_mode=any`、`expires=None`，符合"测试专用"；
- `builder.build()` 每次随机生成 `build_id` / `build_key`（已改为派生）/ `master_key` / `signing_key`（`builder.py:647-653`），代码层面没有固定密钥；
- CI（`.github/workflows/selfbuild-poc.yml`）每次从源码全新构建，未发现复用 `ci-develop-latest` 的路径；
- **限制**：正式 exe 只给了哈希，未给文件，无法做样本与正式包的密钥比对。若需要结论级确认，请提供正式 exe 或至少提供其 `_core.pyc` 中 master key 的指纹比对结果。

## 6. 残余风险清单核对

`docs/security-hardening.md` 列出的五项（运行时明文、双删重置、客户端校验、未签名、无 TPM/服务端）都准确。**遗漏/需要补写**：

| 新增项 | 严重度 | 路径/影响 |
|---|---|---|
| 状态 MAC 密钥非秘密 → 时钟/绑定可伪造 | 中（表述级） | `secure_store.py:108-110`；无新能力，但"防篡改"不成立 |
| 时钟双写部分失败 → 永久不一致、拒绝启动 | 中（可用性） | `_core.py:334-356, 365-368` |
| 完整性清单不参与 300 秒重验 | 中-低 | `_core.py:220-234` |
| 每用户状态 → 新 OS 用户重置时钟 | 低-中 | `_core.py:255-295` |
| 自保护 `integrity=off` + bundle 仅压缩 | 低 | `tools/selfbuild.py:141`；`_runtime_bundle.py:284-292` |
| onefile 下 first-run 绑定写入 `_MEIPASS` | 低（条件性） | `_core.py:383-390` |
| DPAPI 失败静默降级 PLAIN | 低 | `secure_store.py:84-91` |
| `read_private` 抛 `SecureStoreError` 而非 `LicenseError` | 信息 | `_core.py:308` vs `:313-317` |
| `_wipe` 只清 `bytearray`，`open_envelope`/`zlib.decompress` 返回的 `bytes` 未清 | 信息 | `_core.py:666-695` |

## 7. 修复优先级建议

1. **时钟双写改成可自愈**（对齐较新值并重写，或两阶段提交），避免瞬时写失败造成永久拒绝启动；
2. **在文档中把"防篡改"改成"防单点丢失"**，并明确状态 MAC 密钥是公开派生值；
3. **明确 300 秒重验不覆盖清单**；如需覆盖，把 `_integrity` 改成带 TTL 的缓存（注意大目录的哈希开销）；
4. 若多用户共用机器在威胁模型内，把时钟锚点迁到机器级 ACL 目录或服务端；
5. 自保护文档补一句 `integrity=off` 与 "bundle 是一次解压" 的事实；
6. 附带自保护 exe 产物或 CI artifact 的可验证引用，闭合"最终产物"这一环。

## 8. 验证命令

```powershell
python tools/embed_runtime.py --check          # runtime bundle is up to date
python -m unittest discover -s tests           # Ran 159 tests, OK (skipped=1)
python review\verify-sample-chain.py           # 格式 3 / 无 build_key / 模块可解密
```

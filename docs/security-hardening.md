# 安全加固说明（2026-11）

本文记录针对 `PyShieldBuilder.exe` 静态逆向分析与离线 PoC 的修复范围、验证方式和仍存在的边界。结论先说清楚：

> **在“客户端离线分发 + 本地执行”这个前提下，不存在让攻击者拿不到明文的方案。**
> 本轮修复的目标是：移除最容易被脚本化利用的密钥链、提高静态/动态提取成本、把状态重置从“删一个文件”提高到“删多个受保护状态”，并把对外措辞改为“提高逆向成本”。

## 1. 本轮修复对照

| 编号 | 原问题 | 本轮处理 | 仍然存在的边界 |
| --- | --- | --- | --- |
| V1 | `master_key` 在发布目录的 `_core.py`，许可证携带 `build_key`，发布目录自包含 | 许可证升级为格式 3，**不再携带 `build_key`**；`build_key` 由 `master_key + build_id` 通过 HKDF 派生；主密钥改为拆分/XOR 掩码的 `_keyring`，不再以单个 Base85 常量出现；Pro/Enterprise 在工具链可用时默认原生 runtime | 主密钥仍在客户端（`_keyring` 模块或原生扩展中）；攻击者可调用 `_keyring.master_key()` 或 dump 内存后自行派生。真正修复需要服务端解锁或硬件密钥 |
| V2 | 运行期 `marshal.loads` + `exec`，内存中必然出现明文 code object | `load_module` 使用 `bytearray` 解包并在 `finally` 中覆写中间缓冲；默认不再发布 runtime 源码；Pro/Enterprise 默认使用原生 runtime | code object 和运行时字符串仍在进程内存中，可被调试器/dump 获取。本轮只是缩短窗口，不是内存加密 |
| V3 | 首次启动绑定只写 `.pyshield-binding.json`，删除即可重置 | 增加 `_shield_runtime/.pyshield-binding.json.anchor`；主记录与锚点都用 `build_key` 派生 MAC 校验，删除单份会从另一份恢复；两份不一致或损坏均拒绝启动；绑定读写使用唯一临时文件、共享冲突重试，并在整个 read-modify-write 外层加跨进程锁；任何非 onedir `_MEIPASS` 都拒绝首次绑定，设置 `PYSHELD_BINDING_FILE` 也不能关闭检查 | 同时删除主记录和锚点仍可重新绑定；能提取 master key 的攻击者可伪造 MAC。彻底修复需要服务端首绑登记或硬件绑定 |
| V4 | 删除 `~/.pyshield/<build_id>.state` 即视为首次运行；只读部署静默跳过写入 | 时钟状态改用 `secure_store`：Windows DPAPI、其它平台 HMAC；MAC 密钥改为由 `build_key` 派生；写入主文件 + 独立锚点；单份写入失败不再砖化，下一次启动取较新日期并对齐两份；删除单份会恢复；写入使用唯一临时文件、共享冲突重试和跨进程锁；旧公开-MAC 状态默认拒绝，仅在显式 `PYSHELD_ALLOW_LEGACY_STATE=1` 时允许迁移一次，成功后写入一次性 marker，之后即使变量仍为 1 也拒绝旧格式；运行中不接受 legacy 重置；未来日期超过 7 天（legacy 状态为今天之后）拒绝 | 同时删除两份状态和迁移 marker 仍可重置；本地时钟始终可被改；MAC 密钥仍可从 master 派生；状态目录按 OS 用户隔离（可用 `PYSHELD_STATE_ANCHOR_DIR` 指向机器级 ACL 目录） |
| V5 | 到期/宽限/绑定全部由客户端本地判断 | 每次加载受保护模块时，若许可证缓存超过 5 分钟会重新校验；长期运行的进程不会无限期沿用启动时的授权状态；所有状态校验集中到 `_load_state` | 客户端仍可修改 runtime 或绕过校验；**5 分钟重验只覆盖许可证/绑定/时钟，完整性清单仍只在进程首次加载时扫描一次** |
| V6 | exe 未签名、明文 runtime 源码、PDB 路径 | 构建器不再把 `runtime_template`、`crypto.py`、`fingerprint.py`、`ed25519.py` 作为 PyInstaller 明文 `datas`；改为 `pyshield/_runtime_bundle.py` 压缩资源；原生 runtime 构建后执行 strip、删除 `.pdb`、清 PE CodeView 目录；PyInstaller 关闭 UPX | Authenticode 需要证书；PyInstaller 内置 CPython 扩展的 PDB 路径仍可能存在；`.pyc`/PYZ 仍可反编译 |
| V7 | 纯 Python ChaCha 对整块数据做大整数 XOR | 改为 64 KiB 分块 XOR，限制临时整数大小；新增跨块边界的参考实现测试 | 纯 Python 仍然慢；需要性能时使用 `cryptography` 或 `--runtime-native` |

## 2. 关键设计变化

### 2.1 许可证格式 3

格式 3 的许可证只包含授权字段：

```json
{
  "format": 3,
  "build_id": "...",
  "cipher": "chacha20poly1305",
  "python": "3.12",
  "not_before": "...",
  "expires": "...",
  "grace_days": 0,
  "bind_mode": "any",
  "fingerprints": [],
  "customer": null,
  "signature": "..."
}
```

- 加密盐：`pyshield-license-v3`
- AAD：`pyshield-license-v3:<build_id>`
- 签名域：`pyshield-license-v3:`
- 不再包含 `build_key`；模块密钥为 `HKDF(master_key, salt="pyshield-build-key-v1", info=build_id)`。
- 构建侧仍能读取格式 2 许可证，用于旧构建续期；新 runtime 只接受格式 3。
- 旧构建若要获得本轮加固，需要重新构建。`issue` 仍可为格式 2 构建密钥签发格式 2 许可证。

### 2.2 runtime 形态与主密钥

- Trial：默认 `--runtime-pyc`，runtime 全部编译为 `UNCHECKED_HASH` 的 `.pyc`，删除 `.py`。
- Pro / Enterprise：没有显式传 `--runtime-*` 且本机具备 Cython、C 编译器、strip/llvm-strip 时，默认 `--runtime-native`；GUI 中同样默认选择原生运行时，工具链缺失时锁定并回退字节码。
- `--runtime-native`：Cython 编译 `_core`、`_crypto`、`_ed25519`、`_chacha_fast`、`_keyring`；构建后使用 `strip --strip-unneeded`、删除 `.pdb/.ilk/.exp/.lib`，并清空 PE CodeView/debug directory。strip 失败时构建失败，不静默发布未清理产物。
- `--runtime-source`：仅用于调试，保留 `.py` 并输出醒目警告。
- 主密钥不再写入 `_core.py` 的 `MASTER_KEY_B85`。构建时生成 `_keyring.py`：把 32 字节主密钥拆成 8 段，与随机掩码 XOR 后以十六进制保存；运行时由 `_keyring.master_key()` 重建。`strings` 不会得到完整 Base85 主密钥，但可以调用函数或从内存获取，因此这仍是成本项，不是机密性边界。
- `builder.assert_no_master_key()` 在发布前扫描 runtime 目录，若仍出现完整 Base85/原始/hex 主密钥则构建失败。

### 2.3 构建器自身不再携带明文 runtime

`PyShieldBuilder.spec` 不再把以下路径放入 `datas`：

- `pyshield/runtime_template/`
- `pyshield/crypto.py`
- `pyshield/fingerprint.py`
- `pyshield/ed25519.py`

运行时模板与上述模块由 `tools/embed_runtime.py` 压缩打包进 `pyshield/_runtime_bundle.py`，作为 PYZ 的一部分编译。它只是 base64+zlib+zip，解包成本约为一次解压，**不是机密容器**；它的作用是避免 runtime 以明文 `datas` 直接出现在安装目录，而不是阻止有能力的分析者阅读。

修改 runtime、`crypto.py`、`fingerprint.py`、`ed25519.py` 或 `secure_store.py` 后必须执行：

```bash
python tools/embed_runtime.py
```

单元测试 `tests/test_security_hardening.py` 会逐字节检查 bundle 是否与源码一致；`python tools/embed_runtime.py --check` 可用于 CI。

### 2.4 状态锚点、自动修复与边界

时钟状态：

- 主文件：`~/.pyshield/<build_id>.state`
- 锚点：平台状态目录（Windows `%LOCALAPPDATA%\PyShield\state`，Linux `$XDG_STATE_HOME/pyshield` 或 `~/.local/state/pyshield`）
- 覆盖：`PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR`
- Windows 用 DPAPI 封装；其它平台用 HMAC；MAC 密钥由 `build_key` 派生，而不是公开的机器指纹。
- 删除一份会从另一份恢复；两份不一致时取较新日期并重写两份，避免“主文件写成功、锚点写失败”导致永久拒绝启动。
- 写入临时文件使用随机唯一文件名，写完 `fsync` 后原子替换；Windows 共享冲突会短时重试。主/锚点的读改写通过同目录 `.lock` 文件做跨进程串行化，避免多 worker 首次启动时互相覆盖或争用固定 `.tmp` 路径。
- 旧版本用公开 MAC 写入的状态默认**拒绝**。显式设置 `PYSHELD_ALLOW_LEGACY_STATE=1` 后只允许迁移一次：迁移成功或已有 keyed 状态时写一次性 marker（主/锚点各一份），之后即使变量仍为 1，旧格式也会被拒绝；进程已经运行后再注入 legacy 状态同样拒绝。迁移完成后应移除该变量。`last_seen` 晚于今天 7 天以上会按损坏状态拒绝；legacy 状态的日期晚于今天也直接拒绝。
- 两份都损坏/无法解密仍会拒绝启动；只读部署默认拒绝启动，`PYSHELD_ALLOW_VOLATILE_STATE=1` 是明确的风险接受开关。
- 主状态与锚点默认按 OS 用户隔离。同一台机器新建 OS 用户会得到全新时钟；多用户场景应把 `PYSHELD_STATE_ANCHOR_DIR` 指向带 ACL 的机器级目录，或改用服务端时间。设置 `PYSHELD_STATE_DIR` 会让锚点退化为同目录 `.anchor`，分离度降低。
- 状态 HMAC 防误损坏和单点修改，不防能提取 `build_key` 的攻击者；它不是“不可伪造”的安全边界。拥有状态目录写权限的人仍可通过删除两份记录重置历史，旧格式 opt-in 迁移只是让这条路径更显式。

首次启动绑定：

- 主记录：`<dist>/.pyshield-binding.json`
- 锚点：`<dist>/_shield_runtime/.pyshield-binding.json.anchor`
- 删除单份会恢复；同时删除两份仍视为首次运行。
- 绑定 MAC 使用 `build_key` 派生，能防止仅修改单份记录；提取 master key 后仍可伪造。
- 绑定写与读复用 `secure_store` 的唯一临时文件、共享冲突重试，并在 primary/anchor 的整个 read-modify-write 外层持有跨进程锁。单份写入失败不会阻塞启动，下一次启动会从存活副本恢复缺失的一份。
- PyInstaller onefile 的 `_MEIPASS` 是临时目录：首次绑定记录会随进程退出消失。runtime 现在把任何非 onedir `_MEIPASS` 都视为临时并拒绝首次绑定；`PYSHELD_BINDING_FILE` 在 onefile 下不能关闭检查，也不能让解压目录持久化。应改用固定指纹或 onedir 构建。非 onefile 的只读部署仍可用 `PYSHELD_BINDING_FILE` 指向包外可写路径，但这会削弱复制防护。

### 2.5 已知残余风险（对外表述必须保留）

| 残余项 | 现状 | 影响 |
| --- | --- | --- |
| 主密钥可提取 | `_keyring.master_key()` 可调用；`.pyc`/`.pyd`/内存都可被分析 | 能看到完整发布目录的攻击者仍可离线解密；keyring 只是去掉直接字符串常量 |
| 状态 MAC 可伪造 | 时钟 MAC 由 `build_key` 派生，绑定 MAC 也是；提取 master 后可伪造 | “防篡改”不成立，正确表述是“防单点丢失/误损坏” |
| 双删重置 | 删除主状态和锚点仍可重置时钟/绑定 | 纯离线方案无法彻底修复；需要服务端或硬件单调计数器 |
| 旧状态迁移开关 | 默认拒绝；`PYSHELD_ALLOW_LEGACY_STATE=1` 只允许迁移一次，成功后写一次性 marker | 同时删除 marker 与两份状态仍可重新打开迁移路径；迁移后应移除该变量 |
| 状态锁不可用时 | 状态目录不可写时锁文件创建失败，退化为无锁读写 | 唯一临时文件与重试仍防止撕裂；真正不可写时按设计 fail-closed |
| 每用户状态 | 默认状态目录按 OS 用户隔离 | 新 OS 用户可获得全新时钟；多用户场景需机器级锚点 |
| TTL 不覆盖清单 | 300 秒重验只覆盖许可证/绑定/时钟 | 进程启动后替换、尚未导入的 stub 不会被重扫 |
| 自保护 `integrity=off` | 自保护 exe 不做清单校验 | stub/runtime 被替换不会检测；只提高静态阅读成本 |
| bundle 仅压缩 | `_runtime_bundle.py` 是 base64+zlib+zip | 解包成本约一次解压，不是机密容器 |
| onefile 首次绑定 | 非 onedir `_MEIPASS` 一律拒绝，`PYSHELD_BINDING_FILE` 不能关闭检查，也不能让解压目录持久化 | 首绑仍可双删重置；需用固定指纹或 onedir |
| 临时目录中的源码部署 | 无 `_MEIPASS` 时不会检测 dist 是否位于 `%TEMP%` | first-run 绑定可能随临时目录清理丢失；生产环境应使用持久路径 |
| DPAPI 失败降级 | Windows DPAPI 失败时写 `PLAIN1` 状态 | 本地状态仍受 HMAC 保护，但可被伪造；没有静默提权 |
| 内存清理有限 | `_wipe` 只清 `bytearray` 中间缓冲 | code object、`open_envelope`/`zlib` 返回的 `bytes` 仍可能驻留 |
| PyInstaller PDB/符号 | 原生 runtime 已清理；PyInstaller 内置二进制未逐一审计 | 仍可能暴露构建路径/符号 |
| 未签名 | 无 Authenticode | SmartScreen 提示，exe 可被替换后二次打包 |

### MCP 构建服务的额外边界

`PyShieldMCP.exe` 是一个本地 stdio 服务，不是远程 API：

- 只接受 stdio，不监听端口；测试会 mock `socket.socket` 断言无网络调用。
- 项目根、输出根、secret/job/audit 根必须显式配置且互不包含；项目树中的 symlink/junction/reparse point 会被拒绝。
- 输出目录必须不存在；MCP 不暴露 `force`，不会删除已有目录。
- 构建密钥写到独立的 secret 根，不在输出目录同级；响应、日志、审计不会返回密钥。
- 默认构建 `no_verify`，不执行目标项目代码；`pyshield_check_output` 需要在启动时显式开启，并在复制出的沙箱中用最小环境和白名单解释器运行。
- MCP 的额度语义是“严格预检通过后扣减；只有 `PROJECT_CODE` 失败退还”；CLI/GUI 当前仍是“启动即扣、失败/取消不退”，Phase 3 再统一。
- 残余风险：agent 若同时拥有 secret 根的文件读取权限，仍可读取构建密钥；应把 `--output-root` / `--secret-dir` 放在 agent 工作区之外。`check_output` 的沙箱是进程级隔离，不是容器/低权限用户隔离。硬链接不是 reparse point，当前扫描不会拒绝；项目来自不可信来源时应先清理或使用隔离副本。

## 3. 如何验证

```bash
# 全量测试（原生 runtime/符号链接等用例按环境跳过）
python -m unittest discover -s tests -v

# 检查 runtime bundle 是否过期
python tools/embed_runtime.py --check

# 发布前扫描：flag、构建密钥、私钥和包内 secret 文件
python tools/check_release.py dist-demo
python tools/check_release.py dist
python tools/check_release.py --temp

# MCP 冒烟（源码或冻结产物）
python tools/mcp_smoke.py --server module
python tools/mcp_smoke.py --server dist/PyShieldMCP.exe

# 在带 Cython + C 编译器 + strip 的环境验证原生 runtime
python shield.py build -p <project> -o dist-native --runtime-native --no-verify --force
python tools/check_native_build.py dist-native

# 构建一个默认产物
python shield.py build -p <django-project> -o dist-test --cipher chacha-pure

# 产物中不应有 runtime 源码或 .pyx
find dist-test/_shield_runtime -maxdepth 1 -type f | sort

# 许可证中不应出现 build_key
python - <<'PY'
import json
from pathlib import Path
from pyshield import crypto, licensing
secret = licensing.read_secret(Path("dist-test.shield-secret.json"))
payload = licensing.open_license(
    crypto.b85decode(secret["master_key"]), secret["build_id"],
    Path("dist-test/pyshield.lic").read_bytes(), licensing.secret_public_key(secret),
)
print(payload.get("format"), "build_key" in payload)
PY

# 验证签名清单与许可证
python -m _shield_runtime verify --full
python -m _shield_runtime status
```

对旧审计 PoC 的预期：旧脚本按 `_core.py` 正则读取 `MASTER_KEY_B85`，现在会直接失败，因为 `_core.py` 只调用 `_keyring.master_key()`，生成文件里没有完整主密钥常量。攻击者仍可读取 `_keyring.py`/`.pyc`/原生扩展中的掩码分段并重建，或调用 `master_key()`；许可证里也不再存在可提取的 `build_key`，必须自行复现 HKDF 派生。这不等于“无法解密”，只是把利用成本从 20 行脚本提高。

## 4. 构建器自保护 PoC

`tools/selfbuild.py` 已实现一个实验性流程：先用 PyShield 保护 PyShieldBuilder 自身的 `pyshield/` 与 `gui/` 源码，再用 PyInstaller 打包。它能让解包后的业务模块变成 stub，提高静态分析成本；但 runtime 主密钥和自用许可证仍在 exe 内，仍然可以离线提取，不是 V1 的结构性修复。自保护构建显式使用 `runtime_source=True` 和 `integrity="off"`：前者用于让 PyInstaller 静态分析，后者是因为 onefile 的 `_MEIPASS` 布局与清单路径不一致；因此自保护 exe **不做清单完整性校验**，stub/runtime 被替换不会被发现。使用方式与边界见 [selfbuild-poc.md](selfbuild-poc.md)。

## 5. 仍建议完成的结构性工作

1. **服务端解锁**：首次启动把许可证 + 机器指纹发给授权服务，服务端返回短期、机器绑定的密钥；本地只缓存 DPAPI 保护的结果，离线宽限期有限。这是离线分发之外真正的信任边界。
2. **服务端首绑登记**：同一许可证只允许 N 次首次绑定，删除本地记录无法绕过。
3. **硬件密钥**：TPM 密封、加密狗或独立原生密钥模块，主密钥不进入发布目录。
4. **Authenticode 签名**：在 CI 中接入代码签名证书；未签名 exe 仍会触发 SmartScreen，并可被替换后二次打包。
5. **发布暴露审计**：原生 runtime 已做 strip/PDB/CodeView 清理，构建时也会拒绝 runtime 目录中出现完整主密钥；仍需对 PyInstaller 内置二进制做 PDB 路径审计，并在 CI 中固定执行 `strings`/符号/密钥暴露检查。Authenticode 仍未接入。
6. **反调试与内存加固**：检测调试器/注入、减少明文驻留时间。此类措施维护成本高，且不能替代服务端或硬件边界。
7. **机器级状态锚点**：当前主状态/锚点默认按用户隔离；多用户共用机器时，应把锚点放到 `%PROGRAMDATA%` 等带 ACL 的机器级目录或服务端，避免新 OS 用户获得全新的时钟状态。
8. **完整性 TTL**：当前 `_integrity` 只在进程首次加载时扫描并缓存；300 秒重验不覆盖清单。若长驻进程需要发现启动后替换的未导入 stub，应把清单缓存改为带 TTL 的实现，并评估大目录哈希开销。

## 6. 对外表述

建议统一使用：

- “提高源码逆向成本，不是攻击者拿不到明文”
- “离线许可证用于授权管理，不是绝对 DRM”
- “需要更强机密性时使用服务端解锁或硬件密钥”

避免继续使用“源码不落地”“绝对不可破解”等无法在离线客户端兑现的表述。

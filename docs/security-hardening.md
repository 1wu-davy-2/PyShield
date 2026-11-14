# 安全加固说明（2026-11）

本文记录针对 `PyShieldBuilder.exe` 静态逆向分析与离线 PoC 的修复范围、验证方式和仍存在的边界。结论先说清楚：

> **在“客户端离线分发 + 本地执行”这个前提下，不存在让攻击者拿不到明文的方案。**
> 本轮修复的目标是：移除最容易被脚本化利用的密钥链、提高静态/动态提取成本、把状态重置从“删一个文件”提高到“删多个受保护状态”，并把对外措辞改为“提高逆向成本”。

## 1. 本轮修复对照

| 编号 | 原问题 | 本轮处理 | 仍然存在的边界 |
| --- | --- | --- | --- |
| V1 | `master_key` 在发布目录的 `_core.py`，许可证携带 `build_key`，发布目录自包含 | 许可证升级为格式 3，**不再携带 `build_key`**；`build_key` 由 `master_key + build_id` 通过 HKDF 派生；默认 runtime 改为 sourceless `.pyc`，构建器不再以明文 `.py` 发布 runtime 模板 | 主密钥仍在客户端（`.pyc` 或原生扩展中）；有能力的攻击者仍可提取并自行派生。真正修复需要服务端解锁或硬件密钥 |
| V2 | 运行期 `marshal.loads` + `exec`，内存中必然出现明文 code object | `load_module` 使用 `bytearray` 解包并在 `finally` 中覆写中间缓冲；默认不再发布 runtime 源码；保留 `--runtime-native` 选项 | code object 和运行时字符串仍在进程内存中，可被调试器/dump 获取。本轮只是缩短窗口，不是内存加密 |
| V3 | 首次启动绑定只写 `.pyshield-binding.json`，删除即可重置 | 增加 `_shield_runtime/.pyshield-binding.json.anchor`；主记录与锚点都做 HMAC 校验，删除单份会从另一份恢复；两份不一致或损坏均拒绝启动 | 同时删除主记录和锚点仍可重新绑定；彻底修复需要服务端首绑登记或硬件绑定 |
| V4 | 删除 `~/.pyshield/<build_id>.state` 即视为首次运行；只读部署静默跳过写入 | 时钟状态改用 `secure_store`：Windows DPAPI、其它平台 HMAC；写入主文件 + 独立锚点；删除单份会恢复；写入失败默认拒绝启动，只读部署需显式 `PYSHELD_ALLOW_VOLATILE_STATE=1` | 同时删除两份状态仍可重置；本地时钟始终可被改。可靠方案是服务端签名时间或硬件单调计数器 |
| V5 | 到期/宽限/绑定全部由客户端本地判断 | 每次加载受保护模块时，若许可证缓存超过 5 分钟会重新校验；长期运行的进程不会无限期沿用启动时的授权状态；所有状态校验集中到 `_load_state` | 客户端仍可修改 runtime 或绕过校验。离线方案无法把授权判断变成可信执行；没有受保护模块加载时不会触发刷新 |
| V6 | exe 未签名、明文 runtime 源码、PDB 路径 | 构建器不再把 `runtime_template`、`crypto.py`、`fingerprint.py`、`ed25519.py` 作为 PyInstaller 明文 `datas`；改为 `pyshield/_runtime_bundle.py` 压缩资源；CI 可加 Authenticode 签名 | Authenticode 需要证书；PyInstaller 内置 CPython 扩展的 PDB 路径仍可能存在；`.pyc`/PYZ 仍可反编译 |
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

### 2.2 默认 runtime 为 `.pyc`

- 默认（不传 runtime 参数）、`--runtime-pyc`：runtime 全部编译为 `UNCHECKED_HASH` 的 `.pyc`，删除 `.py`。
- `--runtime-native`：Cython 编译 `_core`、`_crypto`、`_ed25519`、`_chacha_fast`。
- `--runtime-source`：仅用于调试，保留 `.py` 并输出醒目警告。

### 2.3 构建器自身不再携带明文 runtime

`PyShieldBuilder.spec` 不再把以下路径放入 `datas`：

- `pyshield/runtime_template/`
- `pyshield/crypto.py`
- `pyshield/fingerprint.py`
- `pyshield/ed25519.py`

运行时模板与上述模块由 `tools/embed_runtime.py` 压缩打包进 `pyshield/_runtime_bundle.py`，作为 PYZ 的一部分编译。它不能被当作机密容器，但去掉了“解包 exe 后直接阅读解密 runtime 源码”的最低成本路径。

修改 runtime、`crypto.py`、`fingerprint.py`、`ed25519.py` 或 `secure_store.py` 后必须执行：

```bash
python tools/embed_runtime.py
```

单元测试 `tests/test_security_hardening.py` 会逐字节检查 bundle 是否与源码一致；`python tools/embed_runtime.py --check` 可用于 CI。

### 2.4 状态锚点与失败关闭

时钟状态：

- 主文件：`~/.pyshield/<build_id>.state`
- 锚点：平台状态目录（Windows `%LOCALAPPDATA%\PyShield\state`，Linux `$XDG_STATE_HOME/pyshield` 或 `~/.local/state/pyshield`）
- 覆盖：`PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR`
- Windows 用 DPAPI 封装；其它平台用 HMAC。
- 删除一份会从另一份恢复；两份不一致、损坏或无法解密都会拒绝启动。
- 只读部署默认拒绝启动；`PYSHELD_ALLOW_VOLATILE_STATE=1` 是明确的风险接受开关。

首次启动绑定：

- 主记录：`<dist>/.pyshield-binding.json`
- 锚点：`<dist>/_shield_runtime/.pyshield-binding.json.anchor`
- 删除单份会恢复；同时删除两份仍视为首次运行。

## 3. 如何验证

```bash
# 全量测试（当前 159 个，1 个按环境跳过）
python -m unittest discover -s tests -v

# 检查 runtime bundle 是否过期
python tools/embed_runtime.py --check

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

对旧审计 PoC 的预期：它按 `_core.py` 正则读取 `MASTER_KEY_B85`，在默认 `.pyc` 产物上会直接失败；即使反编译 `.pyc` 取得主密钥，许可证里也不再存在可提取的 `build_key`，必须自行复现 HKDF 派生。这不等于“无法解密”，只是把利用成本从 20 行脚本提高。

## 4. 构建器自保护 PoC

`tools/selfbuild.py` 已实现一个实验性流程：先用 PyShield 保护 PyShieldBuilder 自身的 `pyshield/` 与 `gui/` 源码，再用 PyInstaller 打包。它能让解包后的业务模块变成 stub，提高静态分析成本；但 runtime 主密钥和自用许可证仍在 exe 内，仍然可以离线提取，不是 V1 的结构性修复。使用方式与边界见 [selfbuild-poc.md](selfbuild-poc.md)。

## 5. 仍建议完成的结构性工作

1. **服务端解锁**：首次启动把许可证 + 机器指纹发给授权服务，服务端返回短期、机器绑定的密钥；本地只缓存 DPAPI 保护的结果，离线宽限期有限。这是离线分发之外真正的信任边界。
2. **服务端首绑登记**：同一许可证只允许 N 次首次绑定，删除本地记录无法绕过。
3. **硬件密钥**：TPM 密封、加密狗或独立原生密钥模块，主密钥不进入发布目录。
4. **Authenticode 签名**：在 CI 中接入代码签名证书；未签名 exe 仍会触发 SmartScreen，并可被替换后二次打包。
5. **PDB 路径与发布暴露审计**：对 PyInstaller 内置二进制做 strip/CodeView 清理，并在 CI 中运行 `rea audit-release-exposure` 一类检查。当前本轮未消除这部分。
6. **反调试与内存加固**：检测调试器/注入、减少明文驻留时间。此类措施维护成本高，且不能替代服务端或硬件边界。

## 6. 对外表述

建议统一使用：

- “提高源码逆向成本，不是攻击者拿不到明文”
- “离线许可证用于授权管理，不是绝对 DRM”
- “需要更强机密性时使用服务端解锁或硬件密钥”

避免继续使用“源码不落地”“绝对不可破解”等无法在离线客户端兑现的表述。

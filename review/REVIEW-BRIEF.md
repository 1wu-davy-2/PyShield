# 给审查的说明：PyShieldBuilder 第二轮安全评估

## 一、本轮改动

本轮没有更换“客户端离线分发”的前提，也没有声称已经解决该前提下的根本矛盾。改动集中在减少发布物里的明文、收紧密钥链和状态回滚路径：

1. **许可证格式 3**：`pyshield.lic` 不再携带 `build_key`；模块密钥改为 `HKDF(master_key, salt="pyshield-build-key-v1", info=build_id)` 在运行时本地派生。旧格式 2 仍可读取，用于兼容和旧构建重签。
2. **默认运行时改为 `pyc`**：普通构建不再交付明文 runtime `.py`；`--runtime-source` 仅用于调试。runtime 源码改为嵌入 `_runtime_bundle.py`，PyInstaller spec 不再把 `runtime_template/`、`crypto.py`、`fingerprint.py`、`ed25519.py` 当明文 `datas` 交付。
3. **绑定与时钟状态**：首次运行绑定采用可见记录 + `.anchor` 双记录；时钟状态采用主状态 + 平台锚点，Windows 用 DPAPI、POSIX 用 HMAC，写入失败默认 fail-closed；删除单份记录会从另一份恢复，两份都被删除仍然可以重置（残余风险未变）。
4. **周期性重验**：受保护模块加载时按 300 秒 TTL 刷新许可证、绑定和时钟检查。
5. **缓冲区清理**：模块解密后尽力清理临时缓冲区，降低内存中长时间保留明文的概率；这不等于防止调试器/内存 dump。
6. **ChaCha 纯 Python 实现**：改为 64 KiB 分块 XOR，避免把整个 payload 当成巨大整数处理。
7. **构建器自保护 PoC**：`tools/selfbuild.py` 先用 PyShield 加密 `pyshield/`、`gui/` 的 27 个模块，再交给 PyInstaller；构建完成后删除 `*.shield-secret.json`，并检查所有业务模块都是 stub。

## 二、请重点审查的问题

1. **密钥链与格式降级**：格式 3 的 `open_license` 是否仍存在回退到格式 2 的降级路径？`build_key` 不在许可证里之后，是否还有别的发布物同时包含 master key 和许可证，从而恢复旧攻击链？请按“攻击者拥有完整发布目录”来验证。
2. **绑定/时钟重置**：双记录是否真的覆盖了单点删除；两份都被删除时是否仍然可以重置；只读部署、权限异常、跨平台锚点失败时是否 fail-closed，是否引入拒绝服务或状态竞争。
3. **自保护样本**：`review/PyShieldBuilder-protected-sample.zip` 是 PyInstaller 打包前的受保护树，27 个业务模块都应以 stub 开头，且不应包含 `*.shield-secret.json`、`runtime_template/`、`.pyc`。请检查 `pyshield.lic` 解包后是否为格式 3、是否不含 `build_key`。
4. **自保护 exe 的交付边界**：`_shield_runtime` 在 intermediate tree 中是明文（为了让 PyInstaller 静态分析），最终 onefile exe 中应是 PYZ 字节码而不是 datas 明文；请确认 spec 与构建脚本没有把 runtime 明文或构建 secret 带进产物。
5. **样本密钥隔离**：样本里的 master key 和自用许可证只能解密该样本，不应与 `ci-develop-latest` 的正式 exe 共用；请确认是否存在跨样本/跨发布复用的密钥。
6. **残余风险清单**：请核对 [security-hardening.md](../docs/security-hardening.md) 中的残余风险是否准确、是否还有遗漏的高/中危问题。特别是：运行时内存明文、删除双份状态后的重置、客户端许可证校验、exe 未签名/无 Authenticode、无 TPM/服务端解锁。

## 三、我们明确的结论

- 在“客户端完全离线、exe 必须自带解密能力”的前提下，不存在让攻击者拿不到明文的方案；`--runtime-native` 也只是把成本从写脚本提高到逆向调试。
- 本轮目标是**提高静态逆向成本、减少发布物明文、堵住明显的状态回滚路径**，不是提供机密性保证。
- 对外表述应使用“提高逆向成本”，不使用“源码不落地”或“无法还原源码”。
- 如果需要更强的边界，应转向 Authenticode 签名、`--runtime-native`、服务端解锁或硬件密钥；这些不在本轮 PoC 范围内。

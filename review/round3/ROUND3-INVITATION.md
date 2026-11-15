# PyShield Round 3 安全评估邀请

感谢第二轮复核。我们已按 `review/REVIEW-FINDINGS-round2.md` 修复可修复项，并在第三轮修复中处理挑战包 flag 可达性、时钟并发、旧状态迁移和 onefile 绑定问题；仍存在的边界已写入文档。请进行第三轮独立评估。

## 材料

- 桌面构建器：`PyShieldBuilder.exe`
  - SHA-256：`e3f85e630daa0fecf04d90b54c0ac80d50dc8140c3856ce4d25a732e66c416c1`
- 自保护构建器：`dist-selfbuild/PyShieldBuilder-selfbuild.exe`
  - SHA-256：`4351f7d0a4c9cf9e64d3f662d3f495b3f714101892f4a1c4e70bf5f11a7d8f04`
- 打包前受保护源码树：`review/round3/PyShieldBuilder-protected-sample-round3.zip`
  - SHA-256：`ddb14d1b8403f4e0cb51afe7f5a02efc4ee8ad0a25a619ce67ccf6ad1c364320`
- 受保护 Django 测试工程：`review/round3/round3-django-shielded.zip`
  - SHA-256：`03fd153b94028f6c0180dcb53584866da3a414fbaa3e96ebaadb5ea19117a2fa`
- Challenge flag 的 SHA-256：`a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce`
- 源码：`develop @ 8adeccf`
- CI：native-runtime `43675650384`、build-develop `43675650496`、selfbuild-poc `43675650448`，均通过

## 请重点验证

1. 从 `round3-django-shielded.zip` 中恢复 `demo.hidden_flag.FLAG`。
   - flag 的 SHA-256：`a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce`
   - 允许静态分析、反编译、动态调试、内存 dump、hook、patch 等任意手段。
   - 请给出可复现路径，而不是只给结果。
2. 检查 `_keyring` 是否仍能以低成本恢复主密钥；记录从拿到发布目录到解出业务模块的步骤与耗时。
3. 用 8–16 个并发进程验证时钟双写：主文件/锚点日期不一致时是否取较新值并重写两份；首次运行、跨日首次启动和续期重验时是否还会出现 `.tmp` 冲突或启动失败。
4. 验证旧公开-MAC 状态迁移：默认应拒绝；仅在显式 `PYSHELD_ALLOW_LEGACY_STATE=1` 时允许一次性迁移；未来日期状态应被限制。
5. 验证 onefile 首次启动绑定拒绝逻辑：默认 `_MEI*` 目录拒绝；即使设置 `PYSHELD_BINDING_FILE`，onefile/非标准 `_MEIPASS` 也不得关闭检查；文档中的双删重置仍是已承认边界。
6. 复核 `--runtime-native`：符号剥离、PDB/CodeView 清理、原生 keyring 是否仍留下完整主密钥常量或明显符号。
7. 复核自保护样本：`integrity=off`、`_runtime_bundle.py` 仅压缩、中间树含明文 runtime/密钥。

## 边界

- 前提仍是客户端离线、exe 必须自带解密能力；我们不要求“攻击者拿不到明文”的机密性保证。
- 目标是评估静态/动态提取成本、状态回滚、许可证校验和发布卫生，不是证明不可破解。
- 允许修改本地副本进行 PoC；不要进行拒绝服务、破坏基础设施、批量攻击或影响第三方。
- 如发现远程/供应链问题，请先私下联系我们，不要公开利用。

## 交付

请按 `VULNERABILITY-REPORT-FORMAT.md` 提交：

- 每个漏洞一条记录，包含严重度、受影响哈希、前置条件、复现步骤、最小 PoC、根因、影响、修复建议；
- 给出从发布物到 flag 的实际路径和大致耗时；
- 对已修复项给出“有效/无效/部分有效”的验证结论；
- 对仍无法修复的离线边界明确标注“残余风险”，不要写成产品缺陷或绝对结论。

请在双方约定的评估窗口内提交；报告可以是 Markdown，附 PoC 文件和日志。感谢复核。

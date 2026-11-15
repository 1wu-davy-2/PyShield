# PyShield Round 3 安全评估邀请

感谢第二轮复核。我们已按 `review/REVIEW-FINDINGS-round2.md` 修复可修复项，并把仍存在的边界写进文档。请进行第三轮独立评估。

## 材料

- 桌面构建器：`PyShieldBuilder.exe`
  - SHA-256：`486fd67e4e23efd30c582529437076b6690d38232a4ff24662b96c334644b0f9`
- 自保护构建器：`dist-selfbuild/PyShieldBuilder-selfbuild.exe`
  - SHA-256：`3a3d0ecfacd5fe2c02c0203f60ac6158ebe69ea85fae3a2526a86fd5f9adcd2a`
- 打包前受保护源码树：`review/round3/PyShieldBuilder-protected-sample-round3.zip`
  - SHA-256：`7a87d55eb55443c99019b0678a530833f8a7c5589c8ad40c6a8dcfefc9f00b23`
- 受保护 Django 测试工程：`review/round3/round3-django-shielded.zip`
  - SHA-256：`3ec82a849b58e126b660f4632f45c209a5571a943139753867b036bcb46304a2`
- 源码：`develop @ 08f400a`（含 `2b5b16d` 的原生 runtime 加固）

## 请重点验证

1. 从 `round3-django-shielded.zip` 中恢复 `demo.hidden_flag.FLAG`。
   - flag 的 SHA-256：`46b5119e53dcb631ca73426deb23910af129b589c8a9f618da1663aa9448e3bc`
   - 允许静态分析、反编译、动态调试、内存 dump、hook、patch 等任意手段。
   - 请给出可复现路径，而不是只给结果。
2. 检查 `_keyring` 是否仍能以低成本恢复主密钥；记录从拿到发布目录到解出业务模块的步骤与耗时。
3. 验证时钟双写自愈：主文件/锚点日期不一致时是否取较新值并重写两份；单份写入失败是否还会砖化。
4. 验证状态 MAC 改为 `build_key` 派生后的影响；旧公开-MAC 状态迁移是否形成新的降级路径。
5. 验证 onefile 首次启动绑定拒绝逻辑是否可绕过，以及 `PYSHELD_BINDING_FILE` 迁移路径。
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

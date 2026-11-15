# PyShield Round 3 安全评估邀请（第五轮）

感谢第四轮复核。我们已按 `review/round3/ASSESSMENT-round4.md` 修复 PSH-R5-001（未签名字节码缓存绕过清单）和 PSH-R5-002（发布检查覆盖不足），并用修复后的源码重建了挑战包与静态样本；仍存在的边界已写入文档。请进行第五轮独立复核。

## 材料

- 桌面构建器（本轮本机构建，含字节码修复）：`PyShieldBuilder-r5.exe`
  - SHA-256：`db212b615ef77ec085913701a9bb944aa093c796978a41c68357613737ca9ce6`
  - 大小：49,307,453 B；与第四轮 CI 产物（`PyShieldBuilder.exe`，`a27a1d73…b3f9`）不是同一构建
- 自保护构建器（本轮重建）：`dist-selfbuild/PyShieldBuilder-selfbuild.exe`
  - SHA-256：`684e88c7b38195a2c9ea1b23406892751a1b65eec4a8eb89e174f8d5eb970b9b`
  - 大小：40,711,405 B
- 打包前受保护源码树：`review/round3/PyShieldBuilder-protected-sample-round3.zip`
  - SHA-256：`6f628ef1d3c682671fff4523b1fc0a9075ae436078614b73433a5c7602fba136`
  - 41 个业务模块 stub，build `ef3bb96a41a169b5`，548,749 B
- 受保护 Django 测试工程：`review/round3/round3-django-shielded.zip`
  - SHA-256：`1be02ace3a2f8b8e161f41b8488dc545537bae20930566f880326086fcda9742`
  - build `60650120c0d42ab3`，68,116 B，zip 内 `__pycache__` 条目为 0
- Challenge flag 的 SHA-256：`4d2b771c90f071e8f1602c00d9d5e4d7f998658873a1c353d9a81b2efbe66e37`
  - 旧值 `a036bf16…c4ce` 因明文进入公开仓库历史已作废轮换；flag 明文不进 git
  - 与第四轮相同，flag 常量位置调整后重新构建仍可离线解出
- 源码：`develop @ 130f1d7`（主提交 `9365307`、路径卫生 `d98b48e`、入口包容错 `5ca70cf`、短路径规范化 `130f1d7`；材料/文档提交另计）
- CI：`130f1d7` 三个 run 全部通过——[native-runtime `43733768015`](https://github.com/1wu-davy-2/PyShield/actions/runs/43733768015)、[build-develop `43733768023`](https://github.com/1wu-davy-2/PyShield/actions/runs/43733768023)、[selfbuild-poc `43733768104`](https://github.com/1wu-davy-2/PyShield/actions/runs/43733768104)；此前 `0e18ad5`/`5ca70cf` 的两次失败与修复见 `ASSESSMENT-round5.md` §1.1
- 自查报告：`ASSESSMENT-round5.md` / `assessment-round5.json`（开发方复核，不是独立审计）

## 请重点验证

1. PSH-R5-001 是否真的关闭：
   - 在可写副本中伪造 `demo/__pycache__/__init__.cpython-312.pyc`（头里的 source mtime/size 与 `.py` 一致），运行 `python manage.py check`；
   - 预期：`unsigned bytecode cache` 失败、marker 不执行；同时确认正常运行后发布目录不产生 `__pycache__`；
   - 请重点尝试绕过 `sys.pycache_prefix` 隔离（`PYTHONPYCACHEPREFIX`、预置 prefix 目录、替换 `_shield_runtime`、`integrity=off` 构建）。
2. PSH-R5-002 的检查器语义：
   - `python tools/check_release.py --temp`（无 `--secret`、无 TEMP 内真实 `*.shield-secret.json`）应失败为“无法验证”，而不是返回 0；
   - 用 `--secret <build secret>` 或 sibling secret 时，能否检出裸 master/build key；
   - 带 `__pycache__` 的目录/zip 是否被拒绝；正常挑战包是否通过（不含构建密钥时需 `--skip-key-scan`）。
3. 从 `round3-django-shielded.zip` 中恢复 `demo.hidden_flag.FLAG`，给出可复现路径与耗时；flag 哈希同上。
4. 复核 `_keyring` 的主密钥提取成本；伪造 pyc 之外，是否还有不依赖内存 dump 的离线路径。
5. 用 8–16 个并发进程复核时钟双写与首次运行绑定：主文件/锚点是否自愈，是否出现 `.tmp` 冲突或两份 `bound_at` 不一致（本轮改动未触及这些路径，回归结论应保持不变）。
6. 复核 onefile/`_MEIPASS` 绑定拒绝、legacy 一次性迁移 marker、`--runtime-native` 的符号/CodeView 清理与自保护 exe 的 `integrity=off` 边界。
7. 检查本轮新增的发布卫生断言是否会被正常部署误伤：构建后清理失败、只读发布目录、源码 runtime 模式下的 `_shield_runtime/__pycache__`。

## 边界

- 前提仍是客户端离线、exe 必须自带解密能力；我们不要求“攻击者拿不到明文”的机密性保证。
- 目标是评估静态/动态提取成本、状态回滚、许可证校验和发布卫生，不是证明不可破解。
- 字节码隔离只保证“未签名缓存不会被执行、不会被视为受保护文件”；能替换 runtime 的攻击者不在该承诺内。
- 允许修改本地副本进行 PoC；不要进行拒绝服务、破坏基础设施、批量攻击或影响第三方。
- 如发现远程/供应链问题，请先私下联系我们，不要公开利用。

## 交付

请按 `VULNERABILITY-REPORT-FORMAT.md` 提交：

- 每个漏洞一条记录，包含严重度、受影响哈希、前置条件、复现步骤、最小 PoC、根因、影响、修复建议；
- 给出从发布物到 flag 的实际路径和大致耗时；
- 对 PSH-R5-001/002 给出“已修复验证 / 部分修复 / 仍可利用”的明确结论；
- 对仍无法修复的离线边界明确标注“残余风险”，不要写成产品缺陷或绝对结论。

请在双方约定的评估窗口内提交；报告可以是 Markdown，附 PoC 文件和日志。感谢复核。

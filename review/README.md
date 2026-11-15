# 审查材料

本目录存放交给安全审查的自保护构建样本，不属于正式发布链路。

## Round 3（当前）

第三轮材料在 [`round3/`](round3/README.md)：

- `round3/PyShieldBuilder-protected-sample-round3.zip`：重新生成的受保护源码树（41 个业务模块 stub）；
- `round3/round3-django-shielded.zip`：可直接运行的受保护 Django 示例与 Crack Challenge；
- `round3/ROUND3-INVITATION.md`：发给审查方的话术；
- `round3/VULNERABILITY-REPORT-FORMAT.md`：漏洞报告格式；
- `round3/ROUND3-FIX-RESPONSE.md`：对第二至第五轮问题的逐项修复说明；
- `round3/ASSESSMENT-round5.md`：第五轮修复复核报告（审计范文），上一轮为 `ASSESSMENT-round4.md`；
- `round3/poc-round5/`：字节码缓存与发布检查的回归脚本与日志；
- `round3/SHA256SUMS.txt`：exe、zip 与 flag 哈希。

## Round 2（历史）

| 文件 | 说明 |
|---|---|
| `PyShieldBuilder-protected-sample.zip` | PyInstaller 打包前的受保护源码树（27 个业务模块 stub），zip 内含 `SAMPLE-NOTICE.md` 和 `verify_sample.py` |
| `SHA256SUMS.txt` | 样本 zip、本地正式 exe、本地自保护 exe 的 SHA256 |
| `REVIEW-BRIEF.md` | 可直接发给审查方的本轮改动、关注点和边界说明 |

```powershell
Expand-Archive review\PyShieldBuilder-protected-sample.zip -DestinationPath review -Force
cd review/selfbuild-protected-sample
python verify_sample.py
```

解压目录仅用于本地检查，不纳入版本控制；仓库提交的是 zip 包。注意仓库的 `*.lic` 忽略规则会跳过解压目录里的 `pyshield.lic`，但压缩包内已包含它。

样本包含本次测试构建的 master key 和自用许可证，仅能解密该样本；它不是发布密钥，也不要作为交付物分发。详细说明见 [docs/selfbuild-review-sample.md](../docs/selfbuild-review-sample.md)。

注意：仓库的 `*.lic` 忽略规则会跳过解压目录里的 `pyshield.lic`，但压缩包内已包含它。如果要提交解压目录，需要显式保留该测试许可证。


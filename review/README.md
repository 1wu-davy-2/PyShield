# 审查材料

本目录存放交给安全审查的自保护构建样本，不属于正式发布链路。

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


# Round 3 审查材料

本目录是第二轮复核后的第三轮材料（2026-11-15 修复版），对应源码 `develop @ 8adeccf`（时钟并发、旧状态迁移、onefile 绑定、单实例启动和挑战 flag 可达性）。所有样本只用于授权安全评估，不是正式发布链路。

| 文件 | 说明 |
| --- | --- |
| `PyShieldBuilder.exe`（仓库根目录，未纳入 git） | 重新构建的桌面构建器，21,541,926 B |
| `dist-selfbuild/PyShieldBuilder-selfbuild.exe`（未纳入 git） | 重新构建的自保护构建器，40,562,131 B |
| `PyShieldBuilder-protected-sample-round3.zip` | PyInstaller 打包前的受保护源码树（28 个业务模块 stub），用于静态检查，430,520 B |
| `round3-django-shielded.zip` | 可直接运行的受保护 Django 示例工程，用于 Crack Challenge，70,742 B |
| `ROUND3-CHALLENGE.md` | Crack Challenge 目标、flag 哈希与规则 |
| `ROUND3-INVITATION.md` | 可直接发给审查方的邀请话术 |
| `VULNERABILITY-REPORT-FORMAT.md` | 要求审查方使用的漏洞报告格式 |
| `ROUND3-FIX-RESPONSE.md` | 对第二轮与第三轮问题的逐项修复说明（自评，不是独立审计） |
| `ASSESSMENT-round3.md` / `assessment-round3.json` | 审查方针对上一版材料的原始报告；其中的哈希对应旧产物，本轮修复后的哈希以 `SHA256SUMS.txt` 为准 |
| `SHA256SUMS.txt` | 本轮 exe、zip 与 flag 哈希 |

## 运行受保护示例

```powershell
Expand-Archive review\round3\round3-django-shielded.zip -DestinationPath review\round3\extracted -Force
cd review\round3\extracted\round3-django-shielded
python manage.py check
python manage.py reveal
```

运行需要 CPython 3.12.x 和 Django 5.x。`python manage.py check` 与 `reveal` 只验证受保护工程可运行，不会打印 flag；flag 在 `demo.hidden_flag` 中，需要分析加密模块才能恢复。修复版已把 `FLAG` 赋值移到无条件 `raise` 之前，并验证解密后的 `co_consts` 确实包含该值；flag 哈希见 `flag-sha256.txt`。

## 原生 runtime CI

`.github/workflows/native-runtime.yml` 会在 `develop` push 时于 Windows 和 Ubuntu 上执行 `--runtime-native` 构建，验证原生扩展、无明文 runtime、无 PDB 侧车、无完整主密钥，并运行受保护应用。

本轮通过：`8adeccf`，[native-runtime run 43675650384](https://github.com/1wu-davy-2/PyShield/actions/runs/43675650384)、[build-develop run 43675650496](https://github.com/1wu-davy-2/PyShield/actions/runs/43675650496)、[selfbuild-poc run 43675650448](https://github.com/1wu-davy-2/PyShield/actions/runs/43675650448)。产物名：`native-runtime-windows-latest`、`native-runtime-ubuntu-24.04`、`PyShieldBuilder-windows-x64`、`PyShieldBuilder-selfbuild-windows-x64`。桌面 exe 已用 CI 快照 `ci-develop-latest` 替换本地文件。

## 校验

```powershell
Get-FileHash review\round3\round3-django-shielded.zip -Algorithm SHA256
Get-FileHash review\round3\PyShieldBuilder-protected-sample-round3.zip -Algorithm SHA256
Get-FileHash PyShieldBuilder.exe -Algorithm SHA256
Get-FileHash dist-selfbuild\PyShieldBuilder-selfbuild.exe -Algorithm SHA256
```

解压目录不纳入版本控制；仓库只提交 zip 和报告。受保护示例包中的 `pyshield.lic` 仅用于该测试构建，不是发布授权。

# Round 3 审查材料

本目录是第二轮复核后的第三轮材料（2026-11-15 第五轮修复版），对应源码 `develop @ d98b48e`（PSH-R5 主提交 `9365307`：未签名字节码缓存与发布检查覆盖；`d98b48e`：runtime traceback 路径卫生）。所有样本只用于授权安全评估，不是正式发布链路。

| 文件 | 说明 |
| --- | --- |
| `PyShieldBuilder-r5.exe`（仓库根目录，未纳入 git） | 第五轮本机构建的桌面构建器，49,305,274 B，含本轮 runtime 修复；仓库根的 `PyShieldBuilder.exe` 是第四轮 CI 旧产物，不含修复，不要混发 |
| `dist-selfbuild/PyShieldBuilder-selfbuild.exe`（未纳入 git） | 本轮重建的自保护构建器，40,711,334 B；`integrity=off` 为设计边界 |
| `PyShieldBuilder-protected-sample-round3.zip` | PyInstaller 打包前的受保护源码树（41 个业务模块 stub），用于静态检查；build `692a5f580935dfaf`，547,238 B |
| `round3-django-shielded.zip` | 可直接运行的受保护 Django 示例工程，用于 Crack Challenge；build `0d13b86aeefd81f8`，已移除 `__pycache__` |
| `ROUND3-CHALLENGE.md` | Crack Challenge 目标、flag 哈希与规则 |
| `ROUND3-INVITATION.md` | 可直接发给审查方的邀请话术 |
| `VULNERABILITY-REPORT-FORMAT.md` | 要求审查方使用的漏洞报告格式 |
| `ROUND3-FIX-RESPONSE.md` | 对第二至第五轮问题的逐项修复说明（自评，不是独立审计） |
| `ASSESSMENT-round5.md` / `assessment-round5.json` | 本轮修复后的复核报告（审计范文），格式对齐 `VULNERABILITY-REPORT-FORMAT.md` |
| `ASSESSMENT-round4.md` / `assessment-round4.json` | 上一轮复核报告，提出 PSH-R5-001/002；哈希对应旧产物 |
| `SHA256SUMS.txt` | 本轮 exe、zip 与 flag 哈希 |

## 运行受保护示例

```powershell
Expand-Archive review\round3\round3-django-shielded.zip -DestinationPath review\round3\extracted -Force
cd review\round3\extracted\round3-django-shielded
python manage.py check
python manage.py reveal
```

运行需要 CPython 3.12.x 和 Django 5.x。`python manage.py check` 与 `reveal` 只验证受保护工程可运行，不会打印 flag；flag 在 `demo.hidden_flag` 中，需要分析加密模块才能恢复。flag 哈希见 `flag-sha256.txt`。

正常运行后发布目录不会出现 `__pycache__`：runtime 在导入时安装字节码隔离（`sys.pycache_prefix` 指向不可预测路径并关闭写入）。如果手工放入未签名的 `demo/__pycache__/*.pyc`，启动会以 `unsigned bytecode cache` 失败，而不是执行该缓存。

## 发布卫生

```powershell
python tools\check_release.py --skip-key-scan review\round3\round3-django-shielded.zip
python tools\check_release.py --skip-key-scan review\round3\PyShieldBuilder-protected-sample-round3.zip
```

两个包都不含构建密钥，因此需要 `--skip-key-scan` 显式接受密钥扫描缺口；不加该参数时检查器会报“无法验证”而不是给出 OK。带 sibling `*.shield-secret.json` 或 `--secret` 时会对 master/build key 做真实比对。

## 本轮回归脚本

`poc-round5/` 是修复后的验证脚本与日志：

- `pycache_tamper_r5.py`：在可写副本里伪造 `demo/__pycache__/__init__.cpython-312.pyc`，预期被 `unsigned bytecode cache` 拒绝且 marker 不执行；
- `check_release_gap_r5.py`：验证 `--temp`/zip 在无密钥时 fail-closed、`--secret` 能检出裸密钥、带 `__pycache__` 的 zip 被拒绝；
- `rebuild_demo.py`：重建挑战包所用的 Django 示例源码（flag 与旧包一致），便于复核构建一致性。

## 原生 runtime CI

`.github/workflows/native-runtime.yml` 会在 `develop` push 时于 Windows 和 Ubuntu 上执行 `--runtime-native` 构建，验证原生扩展、无明文 runtime、无 PDB 侧车、无完整主密钥，并运行受保护应用。

第四轮（`ae718c4`）已通过：[native-runtime run 43697777107](https://github.com/1wu-davy-2/PyShield/actions/runs/43697777107)、[build-develop run 43697777144](https://github.com/1wu-davy-2/PyShield/actions/runs/43697777144)、[selfbuild-poc run 43697777044](https://github.com/1wu-davy-2/PyShield/actions/runs/43697777044)。

第五轮修复提交 `9365307` 目前只有本地测试结果（245 tests OK / skipped=5），尚未触发 CI；推送后请以新的 run 为准，`PyShieldBuilder.exe` 也已按本地构建重新生成，哈希与 CI 快照可能不同。

## 校验

```powershell
Get-FileHash review\round3\round3-django-shielded.zip -Algorithm SHA256
Get-FileHash review\round3\PyShieldBuilder-protected-sample-round3.zip -Algorithm SHA256
Get-FileHash PyShieldBuilder.exe -Algorithm SHA256
```

解压目录不纳入版本控制；仓库只提交 zip 和报告。受保护示例包中的 `pyshield.lic` 仅用于该测试构建，不是发布授权。

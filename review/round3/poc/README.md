# Round 3 PoC 与日志

本目录是第三轮独立评估的最小 PoC 和原始日志。所有样本仅用于本次授权评估。

## 环境

- Windows 10 Pro 19045 x64
- CPython 3.12.0
- Django 5.2.8（仅用于 `manage.py check/reveal`）
- PyInstaller 6.22.3（仅用于离线解析 onefile 归档）
- 依赖：`pyinstaller`、`cryptography`（PyShield 构建器环境已有）

## 路径假设

脚本中的绝对路径来自本次评估机：

- `E:\tmp\workbuild\code\分析\round3\dst\round3-django-shielded`：最终 `round3-django-shielded.zip` 解压目录
- `E:\tmp\workbuild\code\分析\round3\sample\selfbuild-protected-sample`：自保护样本解压目录
- `C:\Users\ww\AppData\Local\Temp\pyshield-zip-recheck-ddnrabzy\round3-django-shielded`：构建机上的早期挑战构建（含 flag）
- `C:\Users\ww\AppData\Local\Temp\pyshield-round3-project-ybevj8q8\review-demo\demo\hidden_flag.py`：构建机上的挑战源码（含 flag）

在其他机器复现时，请只改脚本顶部的路径。

## 脚本

| 文件 | 用途 |
| --- | --- |
| `poc_keyring_decrypt.py` | 从任意 pyc runtime 发布树恢复 master key、派生 build_key、解密全部 stub；不执行目标程序 |
| `poc_exe_extract.py` | 用 PyInstaller `CArchiveReader` 离线读取 onefile exe 的 PYZ，执行 `_shield_runtime._keyring` 代码对象并解密业务模块 |
| `sample_keyring.py` | 验证自保护样本：许可证 format 3、无 build_key、v1 拒绝、解密一个业务模块 |
| `state_tests.py` | 时钟双写自愈、单份写失败、旧公开-MAC 迁移、onefile 绑定、`PYSHELD_BINDING_FILE` 行为测试 |
| `race_driver.py` / `state_race_worker.py` | 8 个并发进程启动，验证时钟状态竞态 |
| `scan_flag.py` | 全文件扫描最终 zip 中的 flag/hash，并校验构建机源码中的 flag 哈希 |
| `flag_rootcause.py` | 对比最终代码对象与构建机源码的编译结果，证明 CPython 删除了 `raise` 之后的 `FLAG` 赋值 |
| `dis_hidden.py` | 解密并反汇编所有受保护模块，确认无 flag 常量 |

## 日志

`logs/` 中是本次运行的原始输出：

- `poc-final.log`：最终 zip（build `c6f18270508c66ce`）全部 13 个模块解密；`demo.hidden_flag` 只有 `co_names` 中的 `FLAG`，无 flag 常量
- `poc-early.log`：早期构建（build `1d39fa1523ef46b0`）解密，`FLAG_CONST=['PSH-ROUND3-...']`
- `exe-poc.log`：自保护 exe（build `26a4b0cc151aa56b`）离线取 master、解密 `pyshield.builder` / `gui.backend`
- `exe-main.log`：主构建器 exe 无 keyring（不是自保护产物）
- `state-tests.log`：26/27 通过；失败项为并发状态写入竞态
- `race.log`：8 进程 × 5 轮，2/40 启动失败
- `smoke.log`：最终样本 `verify` 21 个文件通过，`manage.py check/reveal` 通过
- `sample-verify.log`：自保护样本 28 个 stub、48 个文件、无 secret/pyc/runtime_template
- `sample-keyring.log`：样本许可证 format 3、无 build_key、v1 拒绝、可解密业务模块
- `flag-rootcause.log`：源码编译结果与最终代码对象逐字节对比
- `flag-scan.log`：最终 zip 中 flag/hash 命中为 0
- `tests.log`：项目单元测试 172 通过、1 跳过；runtime bundle 检查通过

## 重要说明

最终交付的 `round3-django-shielded.zip` **不含可恢复的 flag**。`PSH-ROUND3-c32ae3df08a0237b95d472b8413c54f8` 是从构建机临时目录的源码和早期构建恢复的，不是从最终 zip 恢复的。详见评估报告 PSH-R3-001。

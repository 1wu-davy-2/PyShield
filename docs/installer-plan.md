# PyShieldBuilder 安装包规划

**状态：仅规划，尚未实现。** 当前发布物仍是免安装单文件 exe。本文用于后续排期，不改变现有构建流程。

## 1. 目标

提供一个 `PyShieldBuilder-Setup-<版本>-x64.exe` 安装包，用户可：

- 选择安装目录；
- 安装完成后自动生成桌面快捷方式；
- 在安装目录看到卸载程序；
- 升级时保留本机配置、授权与构建记录；
- 卸载时默认只删程序，不动用户配置。

## 2. 关键约束

- **程序装在哪由用户决定，数据始终留在用户目录**：
  - 程序：默认 `%LOCALAPPDATA%\Programs\PyShieldBuilder`，可改；
  - 设置与构建记录：`%APPDATA%\PyShieldBuilder`；
  - 工具授权、下载的 Python 运行时：`%APPDATA%\PyShield`；
  - 以上路径由 `gui/core.py`、`tool_license`、`python_runtime` 决定，安装器不得改写。
- 默认按**当前用户**安装，不需要管理员权限、不触发 UAC；如后续需要装到 `Program Files`，再提供管理员模式。
- 安装包和 exe 目前都未签名，SmartScreen 会提示；代码签名证书到位后再补签。

## 3. 技术选型

推荐 **Inno Setup 6**：

- 免费、单文件安装器、成熟稳定；
- 自带目录选择页、桌面/开始菜单快捷方式、卸载程序（安装目录下的 `unins000.exe`）；
- 支持 per-user 安装（`PrivilegesRequired=lowest`），也可切换为 all-users；
- 可在 CI 中通过 Chocolatey 安装后无界面编译。

备选：NSIS（体积更小，但脚本维护成本高）、MSIX（受签名/证书限制，暂不适合当前发布方式）、WiX（依赖 .NET 工具链，过重）。

## 4. 安装器行为

1. 欢迎页 → 许可协议（`LICENSE` / `CLA.md` 摘要）→ 选择安装目录；
2. 默认勾选「创建桌面快捷方式」，可选「创建开始菜单快捷方式」；
3. 写入 `PyShieldBuilder.exe`、`LICENSE`、`README.md` 与卸载程序；
4. 可选「安装完成后启动 PyShield」；
5. 升级使用固定 `AppId`，覆盖安装程序目录，不触碰 `%APPDATA%`；
6. 卸载默认删除安装目录与快捷方式，**保留** `%APPDATA%\PyShieldBuilder`、`%APPDATA%\PyShield`；提供可选勾选「同时删除本机配置、授权和下载的 Python 运行时」（默认不勾）。

快捷方式名称：「PyShield 加密构建器」；卸载入口同时出现在开始菜单与应用设置中。

## 5. 构建与发布

建议新增以下文件与流水线：

- `installer/PyShieldBuilder.iss`：Inno Setup 脚本；
- `tools/build_installer.ps1`：读取 `pyshield/__init__.py` 版本号，调用 ISCC 编译；
- `.github/workflows/build-develop.yml` 增加 Windows 安装包 job（或并入现有 Windows builder）：
  1. 先产出 `dist/PyShieldBuilder.exe`；
  2. `choco install innosetup`；
  3. 编译安装包，命名 `PyShieldBuilder-Setup-0.6.0-x64.exe`；
  4. 与便携版一起上传到 `ci-develop-latest` release；
  5. 将安装包 SHA256 写入 `SHA256SUMS.txt`。

正式发版时，安装包与便携版并列发布，README/下载页同时给出「安装版」和「免安装版」两个链接。

## 6. 后续阶段

| 阶段 | 内容 | 前置条件 |
| --- | --- | --- |
| M1 | 编写 `.iss`，本机手工编译并验证安装/卸载/升级 | 无 |
| M2 | 接入 GitHub Actions，自动上传 release asset | M1 |
| M3 | 代码签名（OV/EV 证书），消除 SmartScreen 提示 | 证书/主体资质 |
| M4 | 可选：winget 清单、自动更新检查 | M3 |

## 7. 待确认

- 是否需要 **所有用户** 安装模式（需要管理员权限，默认目录改为 `Program Files`）；
- 卸载时是否默认保留用户配置（建议保留）；
- 安装包是否需要内置 `README`/`LICENSE` 的完整文本；
- 是否把官网下载页的默认入口从便携版改为安装版。

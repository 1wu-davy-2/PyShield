# PyShieldBuilder 安装包规划

**状态：安装包仍为规划，尚未实现；本机状态存储（SQLite 双副本）已实现。** 当前发布物仍是免安装单文件 exe。本文用于后续排期，不改变现有构建流程。

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
  - 设置与构建记录：`%APPDATA%\PyShieldBuilder`（SQLite 主副本，另在程序目录保留一份校验副本）；
  - 工具授权、下载的 Python 运行时：`%APPDATA%\PyShield`；
  - 以上路径由 `gui/core.py`、`tool_license`、`python_runtime` 决定，安装器不得改写。
- 默认按**当前用户**安装，不需要管理员权限、不触发 UAC；如后续需要装到 `Program Files`，再提供管理员模式。
- 安装包和 exe 目前都未签名，SmartScreen 会提示；代码签名证书到位后再补签。

## 3. 本机状态存储（已实现）

设置与最近 100 次构建记录不再只放在可手改的 JSON 里，而是以 SQLite 为准，并在程序目录保留第二份副本，用于发现单侧修改和误损坏。实现见 `gui/state.py`，由 `gui/core.py`、`gui/backend.py` 调用。

### 3.1 文件位置

| 用途 | 路径 |
| --- | --- |
| 用户副本（主） | `%APPDATA%\PyShieldBuilder\state.sqlite3` |
| 安装目录副本 | `<安装目录>\state.sqlite3` |
| 写入中标记 | 两个目录下各自的 `state.pending.json` |
| 冲突隔离证据 | `%APPDATA%\PyShieldBuilder\state-incidents\<UTC时间>-<随机串>\` |

- 免安装版：第二副本放在 exe 所在目录；该目录不可写时退化为**单副本模式**，只写用户目录，不误报篡改。
- 源码/开发模式：默认只写用户目录。设置 `PYSHELD_GUI_INSTALL_DIR` 可指定第二副本目录，便于测试双副本流程。
- 旧的 `settings.json` / `history.json` 仍会在首次迁移时导入，并在之后作为导出文件尽力更新；它们不再作为读取来源。无法写导出文件不影响保存。

### 3.2 表结构与一致性判据

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE history (position INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
```

- `meta` 保存 `schema_version`、`install_id`、`created_at`、`generation`、`updated_at`、`digest`、`app_version`。
- 规范化摘要覆盖 `schema_version`、`install_id`、`created_at`、`settings`、`history`；不覆盖 `generation`、`updated_at`、`digest`、`app_version`，保证只改时间戳不会改变内容摘要。
- 两副本一致的条件：内容摘要相同、`install_id` 相同、`generation` 相同。
- 每次写入先在两目录落 `state.pending.json`，再依次写安装目录副本和用户副本；两份都成功后才删除标记。若中途失败，下次启动根据标记和完整副本合并恢复，避免把中断写入误判为篡改。

### 3.3 检测与修复流程

| 情况 | 行为 |
| --- | --- |
| 两份都缺失 | 初始化新库；有旧 JSON 时先导入 |
| 只有一份缺失 | 从另一份复制恢复，模式 `recovered`，不视为篡改 |
| 有写入中标记且存在完整副本 | 采用较新的完整副本并对齐两份，模式 `recovered` |
| 两份都存在但不一致，或任一副本损坏/校验失败 | 判定 `tampered`：隔离两份副本及 sidecar 到 `state-incidents`，写入 `report.json`，禁止保存设置、清空历史和开始构建 |
| 用户选择「使用用户目录副本修复」或「使用安装目录副本修复」 | 校验所选副本 → 取两边较新的 `generation` 加一 → 重写两份 → 清除标记和篡改状态 → 记 `audit` |

- 界面顶部出现红色校验横幅，两个修复按钮对应上述两个来源；修复完成会重新加载设置与历史。
- 后端 API：`state_integrity()` 返回当前状态，`state_repair(source)` 执行修复；`bootstrap()` 会在返回数据中带上 `integrity`。
- 修复不会删除 `state-incidents` 中的证据，便于事后排查。
- 如果两份内容都已损坏且无法选择，删除 `state.sqlite3` 会回到初始化流程，但会丢失设置与构建历史，需要用户明确确认后再操作。

### 3.4 安全边界（不要夸大）

双副本用于发现**单侧修改、同步失败和误损坏**，不是防止本机攻击者的安全边界：能同时改写两个副本、重新计算摘要并更新 `generation` 的攻击者仍可让两份保持一致；摘要算法是公开的，没有密钥或 HMAC。状态库中的设置和构建记录仍是明文，不存放密钥或凭据。真正的授权校验仍由 `pyshield.lic` 与运行时绑定逻辑负责。

### 3.5 与安装器的衔接

- 安装器只写程序文件，**不要预置或覆盖** `state.sqlite3`，也不需要注册表或服务；首次启动由程序自动创建。
- 升级覆盖程序目录时保留已有 `state.sqlite3`；若安装器清空了程序目录，程序会从用户副本自动重建第二副本。
- 卸载默认删除安装目录（含第二副本与卸载程序），保留 `%APPDATA%\PyShieldBuilder` 和 `%APPDATA%\PyShield`；只有用户勾选「同时删除本机配置」时才清理用户副本和隔离证据。
- 若将来改为装到 `Program Files` 等普通用户不可写的目录，第二副本会自然退化为单副本模式；如仍需要双副本，应把镜像目录改为用户可写的安装数据目录，而不是让程序申请提权。

## 4. 技术选型

推荐 **Inno Setup 6**：

- 免费、单文件安装器、成熟稳定；
- 自带目录选择页、桌面/开始菜单快捷方式、卸载程序（安装目录下的 `unins000.exe`）；
- 支持 per-user 安装（`PrivilegesRequired=lowest`），也可切换为 all-users；
- 可在 CI 中通过 Chocolatey 安装后无界面编译。

备选：NSIS（体积更小，但脚本维护成本高）、MSIX（受签名/证书限制，暂不适合当前发布方式）、WiX（依赖 .NET 工具链，过重）。

## 5. 安装器行为

1. 欢迎页 → 许可协议（`LICENSE` / `CLA.md` 摘要）→ 选择安装目录；
2. 默认勾选「创建桌面快捷方式」，可选「创建开始菜单快捷方式」；
3. 写入 `PyShieldBuilder.exe`、`LICENSE`、`README.md` 与卸载程序；
4. 可选「安装完成后启动 PyShield」；
5. 升级使用固定 `AppId`，覆盖安装程序目录，不触碰 `%APPDATA%`；
6. 卸载默认删除安装目录与快捷方式，**保留** `%APPDATA%\PyShieldBuilder`、`%APPDATA%\PyShield`；提供可选勾选「同时删除本机配置、授权和下载的 Python 运行时」（默认不勾）。

快捷方式名称：「PyShield 加密构建器」；卸载入口同时出现在开始菜单与应用设置中。

## 6. 构建与发布

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

## 7. 后续阶段

| 阶段 | 内容 | 前置条件 |
| --- | --- | --- |
| M1 | 编写 `.iss`，本机手工编译并验证安装/卸载/升级 | 无 |
| M2 | 接入 GitHub Actions，自动上传 release asset | M1 |
| M3 | 代码签名（OV/EV 证书），消除 SmartScreen 提示 | 证书/主体资质 |
| M4 | 可选：winget 清单、自动更新检查 | M3 |

## 8. 待确认

- 是否需要 **所有用户** 安装模式（需要管理员权限，默认目录改为 `Program Files`）；
- 卸载时是否默认保留用户配置（建议保留）；
- 安装包是否需要内置 `README`/`LICENSE` 的完整文本；
- 是否把官网下载页的默认入口从便携版改为安装版；
- 安装版是否需要保持程序目录可写以启用双副本校验（当前 per-user 默认目录可写；若改为 `Program Files`，第二副本会退化为单副本模式，是否接受）。

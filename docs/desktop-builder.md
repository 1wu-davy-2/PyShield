# PyShield 桌面构建器

`PyShieldBuilder` 是 PyShield 的桌面构建器：选择 Django 项目目录、确认参数、点击一次即可生成受保护产物。界面使用 pywebview 渲染：Windows 走 WebView2，Linux 走 GTK 3 + WebKit2GTK。构建引擎与全部资源都提前打进单文件程序，运行时不需要安装 Python、PyShield 源码或前端文件。

## 1. 直接使用

Windows 双击 `dist/PyShieldBuilder.exe`，Linux 执行 `dist/PyShieldBuilder`。程序为单文件、免安装，首次启动会解压到系统临时目录，因此比后续启动略慢。Linux 需要系统已安装 WebKit2GTK 4.1 运行库（Ubuntu 24.04：`gir1.2-webkit2-4.1`）。

使用流程：

1. 在“项目与输出”中选择包含 `manage.py` 的 Django 项目根目录；
2. 确认输出目录。默认与项目同级，命名为 `<项目名>-shielded`；
3. 选择加密算法、运行时形态、完整性清单，按需设置许可证与设备绑定；
4. 点击“开始加密”。右侧实时显示阶段、进度和日志；
5. 完成后可直接“打开输出目录”或“查看构建密钥”。

默认值面向离线部署：

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| 加密算法 | ChaCha20 · 纯 Python | 部署机零额外依赖 |
| 运行时形态 | 字节码运行时 | runtime 以 sourceless `.pyc` 发布 |
| 完整性清单 | 保护核心 | 校验业务代码与 runtime |
| 许可证 | 永久 | 可在表单中改为天数或日期 |
| 设备绑定 | 不绑定 | 可改为首次启动绑定或指定指纹 |
| 校验解释器 | 自动检测 | 仅用于 `manage.py check`，可跳过 |

设备绑定：

- **不绑定**：任意兼容机器运行；
- **首次启动绑定**：交付时拿不到客户机指纹时使用。程序第一次启动会在输出目录写入 `.pyshield-binding.json` 并锁定该机器，此后整包复制到其他机器也无法启动；构建时的 `manage.py check` 使用临时绑定文件，不会占用客户机的首次绑定；
- **指定指纹**：已知客户机指纹时使用，可填写多个；客户可运行 `python -m _shield_runtime fingerprint` 获取。

构建密钥写入 `<输出目录>.shield-secret.json`，位置与输出目录同级。它包含 `master_key`、`build_key`、`signing_key`，只应保存在构建方；发布时只交付输出目录本身。

用户设置与构建记录保存在 `%APPDATA%\PyShieldBuilder`（Linux 为 `~/.pyshieldbuilder`）：

- `settings.json`：默认算法、运行时、完整性、输出后缀、解释器检测策略；
- `history.json`：最近 100 次构建记录，只保留本机路径与结果摘要。

可通过环境变量覆盖配置目录：

```powershell
$env:PYSHELD_GUI_CONFIG_DIR = "D:\pyshield-config"
.\PyShieldBuilder.exe
```

## 2. 内置引擎与部署约束

桌面版把 PyShield 构建引擎运行在 exe 进程内，不调用外部 `shield.py`。所有构建资源均已内嵌：

- `gui/web/`：HTML / CSS / JavaScript 界面；
- `pyshield/runtime_template/`：随包 runtime 模板；
- `pyshield/crypto.py`、`fingerprint.py`、`ed25519.py`：构建时复制进产物的 runtime 源文件；
- `cryptography`：启用 AES-256-GCM / ChaCha20-Poly1305 构建；
- pywebview、pythonnet、clr-loader：桌面窗口与 WebView2 宿主。

因此 exe 是自包含的，但产物仍受 Python 版本约束：

- exe 内置 CPython 3.12，受保护模块使用 3.12 的 marshal 格式；
- 产物必须部署在 **CPython 3.12.x** 上，跨小版本会拒绝加载；
- 目标项目自身的 Django 与第三方依赖由部署环境提供，不打包进 exe；
- `manage.py check` 需要一个安装了 Django 的外部解释器。桌面版会自动探测项目 `.venv`、`venv`、`env` 和 PATH，只使用与内置版本相同小版本且安装了 Django 的解释器；找不到时自动跳过校验并写入日志；
- AES-GCM / ChaCha20-Poly1305 产物要求部署机安装 `cryptography`；纯 Python ChaCha20 产物无此要求。

界面中的“本机指纹”用于设备绑定，计算和签名都在本机完成；程序不发起网络请求。

## 3. 从源码打包执行文件（Windows / Linux）

Windows 打包环境建议使用独立的 Python 3.12 虚拟环境：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pyinstaller==6.22.3 pywebview==6.2.1 pythonnet==3.2.1 cryptography
```

确认 WebView2 Runtime 已安装（Windows 10/11 通常随 Edge 提供；缺失时从 Microsoft 官网安装 Evergreen Runtime）。

Linux（Ubuntu 24.04）打包需要 GTK 3 与 WebKit2GTK 的 Python 绑定：

```bash
sudo apt-get install -y python3-gi python3-venv \
  gir1.2-gtk-3.0 gir1.2-webkit2-4.1 gir1.2-soup-3.0
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install pyinstaller==6.22.3 pywebview==6.2.1
```

在 PyShield 项目根目录执行：

```bash
# Windows
.venv/Scripts/python -m PyInstaller --noconfirm --clean PyShieldBuilder.spec

# Linux
.venv/bin/python -m PyInstaller --noconfirm --clean PyShieldBuilder.spec
```

产物为 `dist/PyShieldBuilder.exe`（Windows）或 `dist/PyShieldBuilder`（Linux），均为单文件、无控制台。规格文件 `PyShieldBuilder.spec` 根据平台切换配置：

- 数据文件：`gui/web`、`gui/assets/icon.png`、`pyshield/runtime_template`、`pyshield/crypto.py`、`pyshield/fingerprint.py`、`pyshield/ed25519.py`；
- 隐藏导入：Windows 为 `webview.platforms.edgechromium`、`webview.platforms.winforms`，Linux 为 `webview.platforms.gtk`；
- 排除项：tkinter、Qt、Cython、Django 等非必需组件；Windows 额外排除 `gi`；
- 图标与版本资源：仅 Windows 使用 `gui/assets/icon.ico`、`gui/assets/version_info.txt`；
- `hooks/` 下的自定义 PyInstaller 钩子补齐 WebKit2、JavaScriptCore 与 libsoup 4.0/2.4 的 typelib 收集。

GitHub Actions 工作流 `.github/workflows/desktop-build.yml` 会先运行单元测试，再分别在 `windows-latest` 与 `ubuntu-24.04` 上构建并上传 `PyShieldBuilder-windows-x64`、`PyShieldBuilder-linux-x64` 两个 artifact。

开发时可直接运行源码（需要 pywebview）：

```bash
python -m gui
```

浏览器视觉预览（不启动 WebView）：在 `gui/web` 目录启动任意静态文件服务器，访问
`http://127.0.0.1:<端口>/index.html?preview=1`。`?preview=1` 使用内置模拟数据，不会执行真实构建。设置 `PYSHELD_GUI_DEBUG=1` 可打开 pywebview 调试模式。

## 4. 后端结构

| 文件 | 职责 |
| --- | --- |
| `gui/main.py` | 创建无边框 pywebview 窗口，定位内嵌资源，注册 `pyshield://` 深链 |
| `gui/instance.py` | 单实例锁与深链转发（第二个进程把参数转给已运行实例后退出） |
| `gui/backend.py` | JS 桥接 API、构建线程、进度/日志快照、历史记录、试用状态与 Python 运行时下载任务 |
| `gui/core.py` | 表单校验、默认输出、解释器探测、设置与历史持久化 |
| `gui/web/` | 界面与前端逻辑（`index.html` / `app.js` / `styles.css`） |
| `pyshield/python_runtime.py` | 校验解释器一键下载（镜像选择、版本探测、嵌入包解压、pip/Django 引导） |
| `pyshield/devsite.py` | 无公钥构建自动在 127.0.0.1:8090 拉起本地模拟授权站 |

构建在后台线程执行，前端轮询快照；“取消”通过 `threading.Event` 协作式停止，已复制的临时目录由构建器清理。同一时间只允许一个构建任务。

**页面结构**：工作区（表单 + 摘要 + 开始加密）与构建日志页（进度 + 日志 + 结果卡）是两个独立视图；点击「开始加密」校验通过后整页跳转，完成后展示产物目录并可返回继续构建。交互细节见 `docs/ui-flow.md`。

**解释器下载**：客户机没有 Python 时，在「校验解释器」下点「从镜像下载一个」，从清华/华为镜像下载官方嵌入版 CPython 到 `%APPDATA%\PyShield\runtimes\python-<版本>`（`PYSHELD_RUNTIME_DIR` 可重定向），可选自动安装 pip 与 Django（版本取项目 requirements）。与 exe 内置 CPython 同一小版本的会标「推荐」；其它版本只能单独使用，构建时跳过 `manage.py check`。

## 5. 测试与验收

```bash
python -m unittest tests.test_gui -v
python -m unittest discover -s tests -v
```

`tests/test_gui.py` 覆盖默认输出命名、表单校验、设置与历史持久化、后端 `inspect`，以及一次真实的进程内构建（生成产物、许可证、密钥与历史记录）。

exe 冒烟测试建议：

1. 启动 `dist/PyShieldBuilder.exe`，确认界面、图标、版本号正常；
2. 选择一个测试 Django 项目并执行构建；
3. 确认输出目录含 `pyshield.lic`、`.pyshield-manifest.json`、`_shield_runtime/`；
4. 在输出目录执行 `python manage.py check`；
5. 确认构建记录、设置页与“关于”页可正常打开。

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| 启动后白屏 | Windows 安装 WebView2 Evergreen Runtime；Linux 安装 `gir1.2-webkit2-4.1`；确认杀毒软件未拦截解压 |
| Linux 提示缺少 WebKit2GTK | 安装 `gir1.2-webkit2-4.1` 与 `libwebkit2gtk-4.1-0`；官方产物在 Ubuntu 24.04 构建，需要 glibc 2.39 及以上 |
| 提示找不到 `manage.py` | 选择 Django 项目根目录，而不是内层应用目录 |
| 输出目录无效 | 输出目录不能位于项目内部，也不能是项目目录的父目录；非空目录必须是已有 PyShield 构建或勾选允许覆盖 |
| 校验解释器不可用 | 选择项目虚拟环境中的解释器（Windows `Scripts\python.exe`，Linux `bin/python`）；没有 Python 时点「从镜像下载一个」自动装好；或勾选“跳过 manage.py check” |
| 首次运行被 SmartScreen 拦截 | exe 未签名所致：更多信息 → 仍要运行；或右键属性勾选「解除锁定」 |
| 下载的 Python 装在哪 | `%APPDATA%\PyShield\runtimes\python-<版本>`，删除目录即卸载，不写注册表与 PATH |
| 部署时报 Python 版本错误 | 部署机必须与内置 CPython 3.12 同一小版本 |
| 部署时报缺少 cryptography | 改用纯 Python ChaCha20，或在部署机安装 `cryptography` |
| 构建完成但无日志 | 检查右侧“构建日志”面板；构建结果会写入“构建记录” |
| 客户机提示绑定到其他机器 | 部署包已被复制。恢复到首次启动的机器；确需换机时删除 `.pyshield-binding.json` 重新绑定，或改用指定指纹模式 |
| 首次启动提示无法写入绑定 | 输出目录对运行用户只读。赋予写权限，或用 `PYSHELD_BINDING_FILE` 指向可写路径 |
| 许可证到期后怎么办 | 无需重新构建整包：用构建密钥执行 `python shield.py issue ...` 重新签发，只替换 `pyshield.lic` |

## 7. 安全边界

桌面构建器不会改变 PyShield 的既有安全模型：

- 构建密钥只在构建机生成，不上传、不写入产物目录；
- 发布时只交付输出目录，绝不能附带 `*.shield-secret.json`；
- 许可证由 Ed25519 签名，runtime 只内置公钥；提取对称主密钥不能伪造许可证；
- 源码保护提高逆向成本，不构成绝对 DRM；具备逆向能力的攻击者仍可能提取内存中的代码或主密钥；
- 完整性清单用于发现现场误改或局部篡改，不能阻止整体替换 runtime。

# PyShield

PyShield 是一套自研的 Python / Django 源码保护工具。构建期把业务模块编译、压缩、加密并替换为 stub，运行期校验许可证后在内存中解密执行；模板、静态资源和迁移文件保持可用。

核心实现只依赖 Python 标准库，不依赖 Pyarmor。

- 执行手册：[docs/runbook.md](docs/runbook.md)
- 实现思路：[docs/implementation.md](docs/implementation.md)
- 许可证：[PyShield 商业软件许可协议](LICENSE)

## 特性

- **提高源码逆向成本**：模块编译为 code object 后经 zlib 压缩，再用每个模块独立的 AEAD 密钥加密；发布目录只保留 Base85 stub，runtime 默认发布 sourceless `.pyc`。这是提高攻击成本，不是“攻击者拿不到明文”的承诺。
- **桌面构建器**：`PyShieldBuilder.exe` 是免安装的 Windows 单文件程序，选择 Django 目录、配置参数、点击一次即可完成加密；Web UI、runtime 模板与构建引擎全部内嵌，不依赖外部 Python 或 PyShield 源码。runtime 模板以压缩资源嵌入，不再作为明文 `.py` 随 exe 发布。
- **MCP 构建服务**：`PyShieldMCP.exe` 提供本地 stdio MCP，让 Claude Code、Cursor、CI agent 直接调用构建能力。默认只读、不执行目标项目代码；严格预检后才扣额度，只有目标项目代码错误才退款；构建密钥与日志不通过 MCP 返回。
- **单实例启动**：同一用户会话只允许一个构建器窗口；重复启动会转发深链并唤起已有窗口，不再出现两个实例。主 exe 与自保护 exe 共用同一把全局锁，切换 `PYSHELD_GUI_CONFIG_DIR` 也不能绕过；仅自动化并行 UI 测试可设置 `PYSHELD_ALLOW_MULTI_INSTANCE=1`。
- **Django 友好**：保留正常导入、反射、模板、ORM、URL 路由与管理命令；迁移、测试和静态资源默认不加密。
- **签名完整性清单**：构建时生成 Ed25519 签名的 `.pyshield-manifest.json`，首次加载先校验受保护代码与 runtime 的 SHA-256；许可证、配置、数据库、日志、媒体与 staticfiles 等现场可变文件不受影响。
- **离线许可证**：支持到期日、宽限期、机器绑定和客户标识，可离线重新签发，无需重新构建。许可证格式 v3 只承载授权信息，不再携带模块解密密钥。
- **首次启动绑定**：交付时无法获取客户机指纹时，可选择“首次启动绑定”；程序第一次运行自动锁定该机器，并在发布目录内写入主记录与隐藏锚点。删除单个记录不会重置绑定，之后整目录复制到其他机器也会失败。
- **校验解释器一键下载**：客户机没有 Python 时，可在构建器内从清华/华为镜像下载官方嵌入版 CPython（仅 3.x 维护版本）并自动安装 pip 与 Django，落在用户目录，不改系统 PATH。
- **自助试用**：分发出去的 exe 首次启动即生成本机序列号，点击版本卡片跳转官网申请，服务端签发 14 天 / 3 次构建的试用授权，经 `pyshield://` 深链或轮询回传后自动生效。
- **许可证防伪**：许可证格式 v3 使用 Ed25519 签名，runtime 只内置公钥；即使攻击者提取对称主密钥，也无法伪造或篡改许可证。
- **续期不重打包**：到期只影响 `pyshield.lic`；用构建密钥重新签发并替换该文件即可，业务包、绑定记录和客户标识默认原样保留。
- **续期与时钟保护**：许可证带签发日期，续期后可在进程内热重载；runtime 用 Windows DPAPI（其它平台用 HMAC）封装最后运行日期，MAC 密钥由 `build_key` 派生，并写入主记录与独立锚点。删除单个状态文件不会重置回拨检测；两份日期不一致时取较新值并重写两份，避免一次写入失败把安装变成砖。模块加载时定期重新校验授权，长时间运行的进程不会跨过到期日仍在解密；该 5 分钟重验不覆盖完整性清单。
- **构建绑定**：模块 AAD 同时绑定 `build_id` 与模块 id，跨构建移植密文会解密失败。
- **多种密码套件**：AES-256-GCM、ChaCha20-Poly1305，以及零依赖的纯 Python ChaCha20 + HMAC-SHA256。纯 Python ChaCha 按 64 KiB 分块异或，避免大模块产生超大整数。
- **字节码 / 原生 runtime**：Trail 默认 `--runtime-pyc`；Pro / Enterprise 在工具链可用时默认 `--runtime-native`，用 Cython 把 runtime 编译为 `.pyd` / `.so`，并执行符号剥离、PDB/CodeView 清理。主密钥不再作为单个 Base85 常量写入 runtime，而是拆分、XOR 掩码后由 `_keyring` 在运行时重建。`--runtime-source` 仅供调试，会把解密逻辑以明文发布。
- **离线边界**：客户端必须在本地完成解密，因此主密钥最终会进入客户环境；在完全不联网、不依赖硬件密钥的前提下，不存在“攻击者拿不到明文”的方案。PyShield 的目标是把成本从脚本提取提升到逆向与动态调试级别；真正的机密性边界需要服务端解锁或 TPM/加密狗。

## 环境要求

构建机：

- CPython，推荐 3.12（在 3.12.0 上验证）
- 可选 `cryptography`：启用 AES-256-GCM / ChaCha20-Poly1305
- 可选 `Cython`、`setuptools`、`strip`/`llvm-strip` 和 C 编译器：启用 `--runtime-native`；打包带原生能力的桌面构建器时也需要 `Cython>=3`
- 目标项目根目录必须存在 `manage.py`

部署机：

- 与构建机**相同小版本**的 CPython
- 目标项目自身的运行依赖（Django 等）
- 使用 AES-GCM / ChaCha20-Poly1305 时需要 `cryptography`
- 使用原生 runtime 时，需要与构建机相同的操作系统、CPU 架构和 Python ABI

桌面构建器打包（仅开发构建单文件程序时需要）：

- Windows：Windows 10/11、WebView2 Runtime，`pywebview`、`pythonnet`、`PyInstaller`
- Linux：GTK 3 与 WebKit2GTK（Ubuntu 24.04 的 `python3-gi`、`gir1.2-gtk-3.0`、`gir1.2-webkit2-4.1`），`pywebview`、`PyInstaller`
- GitHub Actions（`.github/workflows/desktop-build.yml`）会在 Windows 与 Ubuntu 24.04 上运行测试并分别产出 `PyShieldBuilder.exe` 和 `PyShieldBuilder` 单文件程序。

## 快速开始

```bash
git clone https://github.com/1wu-davy-2/PyShield.git
cd PyShield
python shield.py --help
python shield.py selftest
```

保护一个 Django 项目：

```bash
python shield.py build -p ../demo -o ../dist-pyshield
```

构建产物：

- `<output>/`：可部署目录，包含 stub、`_shield_runtime/`、`pyshield.lic`、`.pyshield-manifest.json` 和数据文件；
- `<output>.shield-secret.json`：构建密钥，只保存在构建方，**绝不能随包发布**。

部署：

```bash
cd <output>
python -m _shield_runtime verify
python -m _shield_runtime status
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

生产环境请使用正式的 WSGI / ASGI 服务器，并按项目常规方式配置静态文件与 `ALLOWED_HOSTS`。现场配置建议放在 `local_settings.py`、`.env` 或环境变量中，这些文件默认保持明文且不参与完整性校验。

### 桌面构建器

Windows 用户可以直接运行 `PyShieldBuilder.exe`：选择 Django 项目目录，确认输出位置和参数，点击“开始加密”。默认输出到与项目同级的 `<项目名>-shielded`，构建密钥写到 `<输出目录>.shield-secret.json`。

从源码打包 exe：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pyinstaller==6.22.3 pywebview==6.2.1 pythonnet==3.2.1 cryptography
.venv/Scripts/python -m PyInstaller --noconfirm --clean PyShieldBuilder.spec
```

详见[桌面构建器文档](docs/desktop-builder.md)。桌面版内置 CPython 3.12 与全部界面资源，产物仍需部署在 CPython 3.12.x 上。

### MCP 构建服务

```powershell
PyShieldMCP.exe `
  --project-root E:\work `
  --output-root E:\pyshield-output `
  --state-dir E:\pyshield-state `
  --allow-build
```

源码方式：

```powershell
python -m pyshield.mcp_server --project-root E:\work --output-root E:\pyshield-output --allow-build
```

服务只使用 stdio，默认只暴露只读工具；`--allow-build` 开启构建，`--allow-project-execution` 才允许沙箱执行目标项目代码。构建建议先用 `pyshield_validate_build` 拿到 `plan_hash`，再调用 `pyshield_start_build`。配置、安全边界、额度与退款规则见[MCP 构建服务](docs/mcp-server.md)。

## 工具授权（商业许可）

PyShield 有两层完全不同的许可证，不要混用：

| 层级 | 作用 | 文件 |
| --- | --- | --- |
| 工具授权 | 决定谁能使用 PyShield 构建、能用哪些功能、每月构建多少次 | Windows `%APPDATA%\PyShield\tool-license.json`，其他平台 `~/.pyshield/tool-license.json` |
| 产物授权 | 决定受保护程序能在哪台机器、运行到什么时候 | 输出目录下的 `pyshield.lic` |

工具授权由授权方用离线根私钥签发，构建器只内置根公钥。未安装授权且已配置公钥时进入试用模式（默认 14 天、10 次构建，禁用原生 runtime 与产物签发）。

```bash
python shield.py license status          # 查看授权、功能、配额与到期状态
python shield.py license install --license customer-tool-license.json
python shield.py license activate --license customer-tool-license.json --server https://license.example.com
python shield.py license request --license customer-tool-license.json --out activation-request.json
python shield.py license apply --response activation-response.json
python shield.py license heartbeat --server https://license.example.com
python shield.py license revocations --server https://license.example.com
python shield.py license deactivate
```

- 桌面构建器在“设置”页提供同样的授权导入、在线/离线激活、心跳与撤销更新；
- CI 节点的构建应显式加 `--ci`（要求授权包含 `ci` 功能），不要复用开发者的交互式授权；
- 没有公钥也没有本地授权时显示「未激活」并要求先申请试用；开发调试需要不限制构建时显式设置 `PYSHELD_DEV=1`；
- 商业发布包用 `python tools/build_commercial.py build --public-key root.pub --backend nuitka` 构建并注入公钥；根私钥必须离线保存，绝不能进入安装包或版本控制。

授权模型、版本划分、激活流程、密钥管理与发布检查清单见[商业授权与交付](docs/commercial.md)。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python shield.py build -p <project> -o <output>` | 构建受保护的发布目录 |
| `python shield.py issue --secret <secret> -e 30` | 从构建密钥重新签发或续期许可证 |
| `python shield.py verify -d <output> --secret <secret>` | 校验清单签名、受保护文件与许可证 |
| `python shield.py inspect --secret <secret>` | 查看构建与许可证信息 |
| `python shield.py fingerprint` | 打印当前机器指纹 |
| `python shield.py selftest` | 运行密码算法自检 |

构建的常用选项：

| 选项 | 说明 |
| --- | --- |
| `-e, --expire` | 到期时间：天数或 `YYYY-MM-DD` |
| `--grace-days` | 到期后的宽限天数，默认 0 |
| `-b, --bind-device` | 绑定指定机器指纹，可重复 |
| `--bind-first-run` | 首次启动时自动绑定到客户机（与 `-b` 互斥） |
| `--integrity` | `protected`（默认，代码 + runtime）、`full`（含数据文件）、`off` |
| `--mutable` | 允许现场修改、不进入清单的额外文件模式（fnmatch），可重复 |
| `--cipher` | `auto`（默认）、`chacha`、`chacha-pure`、`aesgcm` |
| `--runtime-pyc` | runtime 只发布 sourceless `.pyc`（试用版/缺工具链时的默认；可显式指定） |
| `--runtime-native` | 用 Cython 编译 runtime，并 strip/清 PDB/CodeView（Pro/Enterprise 在工具链可用时的默认；与 `--runtime-pyc` / `--runtime-source` 互斥） |
| `--runtime-source` | runtime 以 `.py` 发布，仅用于调试；会暴露解密逻辑 |
| `--encrypt-migrations` | 迁移文件也加密 |
| `--encrypt-tests` | 测试文件也加密 |
| `--no-verify` | 跳过产物上的 `manage.py check` |

完整选项见 [执行手册](docs/runbook.md)。

## 密码套件

| `--cipher` | 实际算法 | 部署依赖 |
| --- | --- | --- |
| `auto` | 有 `cryptography` 时 AES-256-GCM，否则 ChaCha20 + HMAC-SHA256 | 视构建机而定 |
| `aesgcm` | AES-256-GCM | `cryptography` |
| `chacha` | 有 `cryptography` 时 ChaCha20-Poly1305，否则纯 ChaCha20 + HMAC-SHA256 | 视构建机而定 |
| `chacha-pure` | 强制纯 Python ChaCha20 + HMAC-SHA256 | 无 |

## 运行时模式

| 模式 | 说明 |
| --- | --- |
| `--runtime-pyc` | runtime 编译为 `UNCHECKED_HASH` 的 sourceless `.pyc`，不发布 runtime 源码 |
| `--runtime-native` | 用 Cython 编译 `_core`、`_crypto`、`_ed25519`、`_chacha_fast`、`_keyring` 为 `.pyd` / `.so`；构建后执行符号剥离、删除 `.pdb` 并清 PE CodeView 目录 |
| `--runtime-source` | runtime 以 `.py` 发布，只用于排查问题；会暴露解密逻辑 |

主密钥由构建时生成的 `_keyring` 模块保存：拆成 8 段并与随机掩码 XOR，`strings` 不会得到完整 Base85 主密钥；运行时再重建。它仍然可以被有能力的攻击者通过调用模块函数或内存 dump 获取，不是机密性边界。

原生 runtime 与操作系统、CPU 架构和 Python ABI 绑定，必须在与部署机一致的环境中构建。构建器自身也不再以明文 `datas` 携带 runtime 模板，而是使用编译进 PYZ 的压缩资源。桌面构建器打包了 Cython/setuptools，可通过隐藏的 `--pyshield-setup` 子进程完成原生编译；C 编译器和 strip 工具仍需本机安装。

## 完整性清单

构建默认生成并签名 `.pyshield-manifest.json`，runtime 在首次加载业务模块前校验：

| `--integrity` | 校验范围 | 适用场景 |
| --- | --- | --- |
| `protected`（默认） | stub（`code`）与 `_shield_runtime/`（`runtime`） | 允许现场修改模板、配置、数据 |
| `full` | 再校验模板、静态数据等 `data` 文件 | 交付内容不可变的场景 |
| `off` | 不生成清单，也不校验 | 需要最大现场灵活性 |

- 许可证、`local_settings.py`、`settings_local.py`、`.env*`、`*.sqlite3`、日志、`media/`、`staticfiles/`、`__pycache__` 默认视为可变文件，不进入清单；
- 新增文件不会破坏启动（清单只校验已登记文件）；删除或修改受保护文件会被拒绝；
- 其他需要现场修改的文件用 `--mutable "path/pattern"` 声明；
- 校验失败会拒绝加载，错误信息列出前几个不一致的文件。

## 许可证签发与设备绑定

设备绑定有三种模式：

| 模式 | 适用场景 | 行为 |
| --- | --- | --- |
| 不绑定 | 通用交付 | 任意兼容机器都能运行 |
| 首次启动绑定 `--bind-first-run` | 构建时拿不到客户机指纹 | 第一次启动自动锁定该机器 |
| 指定指纹 `-b <指纹>` | 已知道客户机指纹 | 许可证内写入白名单，可填多台 |

首次启动绑定：

```bash
python shield.py build -p <project> -o <output> --bind-first-run -e 2027-12-31
```

- 构建和 `manage.py check` 不消费客户机的首次绑定，校验时使用临时绑定文件；
- 部署后第一次启动会在输出目录写入 `.pyshield-binding.json`（`build_id`、机器指纹、绑定时间、HMAC），并在 `_shield_runtime/` 写入独立锚点；之后每次启动都要求同一台机器；
- 删除主记录不会重置绑定：runtime 会从锚点恢复。修改任一记录、整包复制到其他机器，或记录被替换成另一台机器，都会拒绝加载；
- 只读部署可用 `PYSHELD_BINDING_FILE` 指向可写路径；该环境变量是调试/特殊部署入口，放到包外会削弱“复制整包”防护；
- 更换硬件需要重新绑定：由构建方删除旧绑定记录，或改用 `-b` 签发固定指纹许可证。离线方案无法阻止有权同时删除主记录与锚点的人重新绑定。

在目标机器取指纹（客户也可自行运行，不需要许可证）：

```bash
python shield.py fingerprint
cd <output> && python -m _shield_runtime fingerprint
```

续期只替换许可证文件：

```bash
python shield.py issue --secret <output>.shield-secret.json -e 2027-12-31
```

- **到期不会废弃业务包**。受保护代码、清单与构建密钥都仍然有效，只有 `pyshield.lic` 过期；重新签发后替换现场的同名文件即可，无需重新构建或重新上传整包；
- `issue` 默认继承现有许可证的到期日、绑定模式、指纹和客户标识，只覆盖显式传入的参数；首次启动绑定的记录不随续期改变，客户机无需重新绑定；
- 宽限期在到期后继续允许运行，默认 0 天；不传 `--grace-days` 时沿用原许可证的值；
- 许可证带 `not_before` 签发日期；现场时钟早于签发日期超过 1 天会拒绝加载；
- runtime 把 `last_seen` 用 DPAPI（Windows）或 HMAC（其它平台）封装后写入 `~/.pyshield/<build_id>.state`，并写入一个独立锚点（可用 `PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR` 指定）；MAC 密钥由 `build_key` 派生；写入使用唯一临时文件、共享冲突重试和跨进程锁；删除单个文件会从锚点恢复，两份日期不一致时取较新值对齐，发现时钟明显回拨时拒绝加载；旧公开-MAC 状态默认拒绝，只有一次性升级时才临时设置 `PYSHELD_ALLOW_LEGACY_STATE=1`；
- 只读部署默认会因无法持久化时钟状态而拒绝启动；如明确接受回拨检测失效，可设置 `PYSHELD_ALLOW_VOLATILE_STATE=1`；
- runtime 在模块加载时每 5 分钟重新校验一次许可证与日期，长时间运行的进程不会跨过到期日继续解密；
- 热重载：`from _shield_runtime import license_status; license_status(refresh=True)`，新导入的模块立即使用新许可证，已导入模块不受影响；
- 时钟状态与首次绑定都是尽力而为的离线措施；同时删除主记录与锚点可重置历史，真正的锚点必须放在服务端或硬件中，请同时启用 NTP 与授权流程。主状态/锚点默认按 OS 用户隔离，多用户共用机器时可用 `PYSHELD_STATE_ANCHOR_DIR` 指向机器级 ACL 目录。

`issue` 需要构建密钥中的 `signing_key`，因此 `*.shield-secret.json` 必须妥善保管。

## 现场部署

- **配置外置**：把环境相关配置放在 `local_settings.py`（由 `settings.py` 末尾导入）、`.env` 或环境变量中；这些文件默认明文，不参与清单校验。
- **部署校验**：先运行 `python -m _shield_runtime verify`（`--full` 同时校验数据文件），再用 `python -m _shield_runtime status` 查看许可证、到期日、宽限期和最后运行日期。
- **可写目录**：运行用户需要能写许可证所在目录（续期替换）；首次启动绑定还需要在输出目录写 `.pyshield-binding.json` 与 `_shield_runtime/.pyshield-binding.json.anchor`。时钟状态写入用户目录；只读部署可用 `PYSHELD_BINDING_FILE`、`PYSHELD_STATE_DIR` 与 `PYSHELD_STATE_ANCHOR_DIR` 指向可写位置，或设置 `PYSHELD_ALLOW_VOLATILE_STATE=1` 明确接受回拨检测失效。
- **时间同步**：部署机应启用 NTP/时间同步；容器重建或快照恢复后如出现时钟回拨告警，先校正系统时间。
- **允许的现场变更**：仅修改清单之外的可变文件；stub、`_shield_runtime/`、`.pyshield-manifest.json` 不可手工编辑，许可证通过 `issue --out` 替换。构建方可随时运行 `python shield.py verify -d <output> --secret <secret>` 复核交付包。

## 安全边界

- 服务端代码最终要在内存中解密，runtime 中的 `master_key` 也可能被提取；本工具提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM。
- **离线客户端不存在绝对机密性**：许可证不再携带 `build_key`，runtime 也不再发布 `.py` 源码；主密钥被拆分/掩码存放，不再以单个 Base85 常量出现，但拿到发布目录且有逆向能力的攻击者仍可通过调用 `_keyring` 或内存 dump 提取主密钥并自行还原模块。需要真正机密性时，应引入服务端解锁、TPM/加密狗或额外的原生密钥模块。
- Ed25519 签名可以阻止伪造许可证，但无法阻止攻击者绕过校验逻辑或 dump 已解密的代码。
- 完整性清单由 runtime 自身执行校验，且**只在进程首次加载时扫描一次并缓存**；5 分钟重验只覆盖许可证/绑定/时钟。如果攻击者可以整体替换 `_shield_runtime/`，清单也会一起失效。它用于发现现场误改或局部篡改，不是二进制级防篡改。
- 时钟回拨检测依赖本地状态与 DPAPI/HMAC；MAC 密钥由 `build_key` 派生，提取 master 后仍可伪造。删除主记录与锚点后仍可重置历史，服务端时间或硬件单调计数器才是可靠锚点。宽限期和到期日仍应通过合同与授权流程约束。
- 首次启动绑定把 HMAC 记录写在部署目录，并增加独立锚点；能阻止直接复制整包到其他机器，也能阻止只删除主记录的重置；但拥有写权限且同时删除两份记录的人仍可重新绑定，这是纯离线方案的边界。PyInstaller onefile 的 `_MEIPASS` 会随进程消失；runtime 把任何非 onedir `_MEIPASS` 视为临时目录并拒绝首次绑定，`PYSHELD_BINDING_FILE` 不能关闭该检查，也不能让解压目录持久化；请改用固定指纹或 onedir。
- 状态 HMAC 和 DPAPI 用于防误损坏、单点修改和抬高伪造成本，不等于不可伪造；DPAPI 失败时会退化为 `PLAIN1` 状态（仍带 HMAC），需要在部署文档中说明。
- `--integrity full` 会把模板与静态数据纳入校验，现场修改这些文件会阻止启动；需要现场可改的内容应使用 `--mutable` 声明。
- 构建与部署必须使用相同的 Python 小版本；原生 runtime 还要求相同的操作系统和 CPU 架构。
- 迁移与测试默认保持明文；模板与静态资源不加密，前端代码对用户可见。
- 泄漏 `*.shield-secret.json` 等同于泄漏构建方最高权限密钥，应立即重新构建并作废旧包。
- 发布卫生：原生 runtime 已做符号剥离和 PDB/CodeView 清理，UPX 已关闭；Authenticode 签名与构建器内置二进制的 PDB 路径审计仍需发布流程处理，见 [docs/security-hardening.md](docs/security-hardening.md)。
- 构建器自身的实验性自保护构建见 [docs/selfbuild-poc.md](docs/selfbuild-poc.md)；它只提高静态逆向成本，不改变上述离线边界。

## 文档

- [执行手册](docs/runbook.md)：构建、部署、许可证、更新与故障排查
- [桌面构建器](docs/desktop-builder.md)：exe 使用、打包、内置资源与部署约束
- [MCP 构建服务](docs/mcp-server.md)：stdio 配置、工具、安全边界、额度与退款
- [商业授权与交付](docs/commercial.md)：工具授权、版本与功能、激活、配额与水印、商业构建
- [试用登记与授权回传](docs/trial.md)：序列号、官网申请、深链回传与试用限制
- [页面交互流程](docs/ui-flow.md)：全部界面、状态与文案，可喂给原型工具
- [开发者交接说明](docs/handover.md)：模块地图、本地开发、测试与发布流程、待办
- [Phase 3 路线图](docs/phase3-roadmap.md)：HSM/KMS 签发、托管构建、OEM 版税、客户门户与审计
- [实现思路](docs/implementation.md)：密钥体系、信封格式、运行时加载与设计取舍
- [自保护构建 PoC](docs/selfbuild-poc.md)：先加密 PyShieldBuilder 自身源码再打 exe 的实验流程

## 许可证

PyShield 是商业专有软件，以 **PyShield Proprietary Software License Agreement** 授权，完整条款见 [LICENSE](LICENSE)。

软件按授权文件（许可证密钥或签名授权书）授予有限、非独占、不可转让的使用权；授权范围以版本、功能、席位、构建配额和期限为准。未经书面授权，不得反向工程、修改、再分发、出租、转售或将本软件作为服务提供给第三方。

受保护产物中被许可方自己的代码归被许可方所有；产物内嵌的 PyShield 运行时允许随该产物一并分发，但不得单独提取或用于构建竞争产品。

历史上以 AGPL-3.0 发布的版本，其已经取得的权利不受本次授权变更影响；新版本以本仓库 [LICENSE](LICENSE) 为准。商业授权、OEM 合作、离线激活与企业版采购，请通过官方仓库或授权渠道联系。

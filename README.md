# PyShield

PyShield 是一套自研的 Python / Django 源码保护工具。构建期把业务模块编译、压缩、加密并替换为 stub，运行期校验许可证后在内存中解密执行；模板、静态资源和迁移文件保持可用。

核心实现只依赖 Python 标准库，不依赖 Pyarmor。

- 执行手册：[docs/runbook.md](docs/runbook.md)
- 实现思路：[docs/implementation.md](docs/implementation.md)
- 许可证：[PyShield 商业软件许可协议](LICENSE)

## 特性

- **源码不落地**：模块编译为 code object 后经 zlib 压缩，再用每个模块独立的 AEAD 密钥加密；发布目录只保留 Base85 stub。
- **桌面构建器**：`PyShieldBuilder.exe` 是免安装的 Windows 单文件程序，选择 Django 目录、配置参数、点击一次即可完成加密；Web UI、runtime 模板与构建引擎全部内嵌，不依赖外部 Python 或 PyShield 源码。
- **Django 友好**：保留正常导入、反射、模板、ORM、URL 路由与管理命令；迁移、测试和静态资源默认不加密。
- **签名完整性清单**：构建时生成 Ed25519 签名的 `.pyshield-manifest.json`，首次加载先校验受保护代码与 runtime 的 SHA-256；许可证、配置、数据库、日志、媒体与 staticfiles 等现场可变文件不受影响。
- **离线许可证**：支持到期日、宽限期、机器绑定和客户标识，可离线重新签发，无需重新构建。
- **首次启动绑定**：交付时无法获取客户机指纹时，可选择“首次启动绑定”；程序第一次运行自动锁定该机器并写入带 HMAC 的绑定记录，之后整目录复制到其他机器也无法启动。
- **自助试用**：分发出去的 exe 首次启动即生成本机序列号，点击版本卡片跳转官网申请，服务端签发 14 天 / 3 次构建的试用授权，经 `pyshield://` 深链或轮询回传后自动生效。
- **许可证防伪**：许可证格式 v2 使用 Ed25519 签名，runtime 只内置公钥；即使攻击者提取对称主密钥，也无法伪造或篡改许可证。
- **续期不重打包**：到期只影响 `pyshield.lic`；用构建密钥重新签发并替换该文件即可，业务包、绑定记录和客户标识默认原样保留。
- **续期与时钟保护**：许可证带签发日期，续期后可在进程内热重载；runtime 记录带 HMAC 的最后运行日期，检测明显的系统时钟回拨。
- **构建绑定**：模块 AAD 同时绑定 `build_id` 与模块 id，跨构建移植密文会解密失败。
- **多种密码套件**：AES-256-GCM、ChaCha20-Poly1305，以及零依赖的纯 Python ChaCha20 + HMAC-SHA256。
- **可选原生 runtime**：`--runtime-native` 用 Cython 把 runtime 编译为 `.pyd` / `.so`，提高静态提取门槛，并为纯 ChaCha 回退提供原生加速。

## 环境要求

构建机：

- CPython，推荐 3.12（在 3.12.0 上验证）
- 可选 `cryptography`：启用 AES-256-GCM / ChaCha20-Poly1305
- 可选 `Cython`、`setuptools` 和 C 编译器：启用 `--runtime-native`
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
- 开发仓库默认进入开发模式（不限制构建）；一旦注入了公钥，本地调试需设置 `PYSHELD_DEV=1`；
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
| `--runtime-pyc` | runtime 只发布 sourceless `.pyc` |
| `--runtime-native` | 用 Cython 编译 runtime（与 `--runtime-pyc` 互斥） |
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
| 默认 | runtime 以 `.py` 发布，便于排查问题 |
| `--runtime-pyc` | runtime 编译为 `UNCHECKED_HASH` 的 sourceless `.pyc` |
| `--runtime-native` | 用 Cython 编译 `_core`、`_crypto`、`_ed25519`、`_chacha_fast` 为 `.pyd` / `.so` |

原生 runtime 与操作系统、CPU 架构和 Python ABI 绑定，必须在与部署机一致的环境中构建。

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
- 部署后第一次启动会在输出目录写入 `.pyshield-binding.json`（`build_id`、机器指纹、绑定时间、HMAC），之后每次启动都要求同一台机器；
- 绑定记录被修改、整包复制到其他机器，或记录被替换成另一台机器，都会拒绝加载；
- 只读部署可用 `PYSHELD_BINDING_FILE` 指向可写路径；绑定记录放到包外会削弱“复制整包”防护；
- 更换硬件需要重新绑定：由构建方删除旧绑定文件，或改用 `-b` 签发固定指纹许可证。离线方案无法阻止有权删除本地文件的人重新绑定。

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
- runtime 将带 HMAC 的 `last_seen` 写入 `~/.pyshield/<build_id>.state`（可用 `PYSHELD_STATE_DIR` 指定目录）；发现时钟明显回拨时拒绝加载；
- 热重载：`from _shield_runtime import license_status; license_status(refresh=True)`，新导入的模块立即使用新许可证，已导入模块不受影响；
- 时钟状态与首次绑定都是尽力而为的离线措施；删除本地状态可重置历史，请同时启用 NTP 与授权流程。

`issue` 需要构建密钥中的 `signing_key`，因此 `*.shield-secret.json` 必须妥善保管。

## 现场部署

- **配置外置**：把环境相关配置放在 `local_settings.py`（由 `settings.py` 末尾导入）、`.env` 或环境变量中；这些文件默认明文，不参与清单校验。
- **部署校验**：先运行 `python -m _shield_runtime verify`（`--full` 同时校验数据文件），再用 `python -m _shield_runtime status` 查看许可证、到期日、宽限期和最后运行日期。
- **可写目录**：运行用户需要能写许可证所在目录（续期替换）；首次启动绑定还需要在输出目录写 `.pyshield-binding.json`。只读部署可用 `PYSHELD_BINDING_FILE` 与 `PYSHELD_STATE_DIR` 指向可写位置。
- **时间同步**：部署机应启用 NTP/时间同步；容器重建或快照恢复后如出现时钟回拨告警，先校正系统时间。
- **允许的现场变更**：仅修改清单之外的可变文件；stub、`_shield_runtime/`、`.pyshield-manifest.json` 不可手工编辑，许可证通过 `issue --out` 替换。构建方可随时运行 `python shield.py verify -d <output> --secret <secret>` 复核交付包。

## 安全边界

- 服务端代码最终要在内存中解密，runtime 中的 `master_key` 也可能被提取；本工具提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM。
- Ed25519 签名可以阻止伪造许可证，但无法阻止攻击者绕过校验逻辑或 dump 已解密的代码。
- 完整性清单由 runtime 自身执行校验；如果攻击者可以整体替换 `_shield_runtime/`，清单也会一起失效。它用于发现现场误改或局部篡改，不是二进制级防篡改。
- 时钟回拨检测依赖本地状态文件，删除状态可重置历史；宽限期和到期日仍应通过合同与授权流程约束。
- 首次启动绑定把 HMAC 记录写在部署目录，能阻止直接复制整包到其他机器；但拥有写权限的人删除该文件后可以重新绑定，这是纯离线方案的边界。
- `--integrity full` 会把模板与静态数据纳入校验，现场修改这些文件会阻止启动；需要现场可改的内容应使用 `--mutable` 声明。
- 构建与部署必须使用相同的 Python 小版本；原生 runtime 还要求相同的操作系统和 CPU 架构。
- 迁移与测试默认保持明文；模板与静态资源不加密，前端代码对用户可见。
- 泄漏 `*.shield-secret.json` 等同于泄漏构建方最高权限密钥，应立即重新构建并作废旧包。

## 文档

- [执行手册](docs/runbook.md)：构建、部署、许可证、更新与故障排查
- [桌面构建器](docs/desktop-builder.md)：exe 使用、打包、内置资源与部署约束
- [商业授权与交付](docs/commercial.md)：工具授权、版本与功能、激活、配额与水印、商业构建
- [试用登记与授权回传](docs/trial.md)：序列号、官网申请、深链回传与试用限制
- [Phase 3 路线图](docs/phase3-roadmap.md)：HSM/KMS 签发、托管构建、OEM 版税、客户门户与审计
- [实现思路](docs/implementation.md)：密钥体系、信封格式、运行时加载与设计取舍

## 许可证

PyShield 是商业专有软件，以 **PyShield Proprietary Software License Agreement** 授权，完整条款见 [LICENSE](LICENSE)。

软件按授权文件（许可证密钥或签名授权书）授予有限、非独占、不可转让的使用权；授权范围以版本、功能、席位、构建配额和期限为准。未经书面授权，不得反向工程、修改、再分发、出租、转售或将本软件作为服务提供给第三方。

受保护产物中被许可方自己的代码归被许可方所有；产物内嵌的 PyShield 运行时允许随该产物一并分发，但不得单独提取或用于构建竞争产品。

历史上以 AGPL-3.0 发布的版本，其已经取得的权利不受本次授权变更影响；新版本以本仓库 [LICENSE](LICENSE) 为准。商业授权、OEM 合作、离线激活与企业版采购，请通过官方仓库或授权渠道联系。

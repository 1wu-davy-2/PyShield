# PyShield 执行手册

PyShield 是一套自研的 Python / Django 源码保护工具：构建期把 Python 模块编译、压缩、加密并替换为 stub，运行期在内存中解密执行；模板、静态资源、迁移文件保持可用。

本手册面向 PyShield 的构建与部署人员，覆盖构建、部署、许可证、更新和故障排查。

## 1. 项目组成

| 路径 | 说明 |
| --- | --- |
| `shield.py` | 命令行入口 |
| `pyshield/builder.py` | 模块发现、加密、stub、runtime、许可证、清单与产物校验 |
| `pyshield/crypto.py` | HKDF、AEAD 信封、AES-256-GCM、ChaCha20-Poly1305、ChaCha20 + HMAC-SHA256 |
| `pyshield/ed25519.py` | 许可证与清单签名、验签（RFC 8032，纯 Python，优先用 cryptography） |
| `pyshield/licensing.py` | 许可证与构建密钥读写、签发日期、宽限期与校验 |
| `pyshield/manifest.py` | 签名完整性清单：文件发现、分类、SHA-256、签名与校验 |
| `pyshield/fingerprint.py` | 跨平台机器指纹 |
| `pyshield/runtime_template/` | 运行库模板源码（完整性校验、时钟状态、诊断入口与 Cython 原生 ChaCha）；构建器通过 `pyshield/_runtime_bundle.py` 的压缩资源使用，exe 中不再以明文 `.py` 发布 |
| `gui/` | 桌面构建器：pywebview 窗口、JS 桥接、表单校验与内嵌 Web UI |
| `PyShieldBuilder.spec` | PyInstaller 单文件打包配置 |
| `tests/` | 加密、签名、许可证、构建流程与桌面构建器后端测试 |
| `docs/` | 本手册与《实现思路》 |

核心实现只依赖 Python 标准库；Ed25519 有纯 Python 实现；AES-256-GCM 和 ChaCha20-Poly1305 需要可选的 `cryptography`；原生 runtime 需要可选的 Cython、setuptools 和 C 编译器。

## 2. 环境要求

构建机：

- CPython，推荐 3.12（在 3.12.0 上验证）
- 可选：`cryptography`（启用 AES-256-GCM / ChaCha20-Poly1305）
- 可选：`Cython`、`setuptools` 和 C 编译器（启用 `--runtime-native`）
- 目标项目：Python / Django 项目根目录存在 `manage.py`

部署机：

- 与构建机**相同小版本**的 CPython（例如双方都是 3.12）
- 目标项目自身的运行依赖（Django 等）
- 若产物使用 AES-GCM 或 ChaCha20-Poly1305，需要安装 `cryptography`；`--cipher chacha-pure` 无额外依赖
- 若使用原生 runtime，部署机必须与构建机**同操作系统、同 CPU 架构、同 Python 小版本**（`.pyd`/`.so` 与 ABI 绑定）

PyShield 自身不需要 Django，也不依赖 Pyarmor。

## 3. 构建

在 PyShield 项目根目录执行：

```bash
python shield.py build -p <project> -o <output>
```

示例：

```bash
python shield.py build -p ../demo -o ../dist-pyshield
```

输出目录必须位于目标项目目录之外，并且只能是空目录、已有的 PyShield 构建目录，或显式传入 `--force`。

常用选项：

| 选项 | 说明 |
| --- | --- |
| `-p, --project` | 目标项目根目录，必须包含 `manage.py` |
| `-o, --output` | 输出目录 |
| `-e, --expire` | 到期时间：天数或 `YYYY-MM-DD` |
| `--grace-days` | 到期后的宽限天数，默认 0，最大 3650 |
| `-b, --bind-device` | 绑定指定机器指纹，可重复 |
| `--bind-first-run` | 首次启动时自动绑定到客户机，与 `-b` 互斥 |
| `--integrity` | `protected`（默认，代码 + runtime）、`full`（含 data 文件）、`off` |
| `--mutable` | 额外允许现场变更的 fnmatch 模式，不进入清单，可重复 |
| `--cipher` | `auto`（默认）、`chacha`、`chacha-pure`、`aesgcm`，见下表 |
| `--exclude` | 额外排除模式（fnmatch），可重复 |
| `--encrypt-migrations` | 迁移文件也加密（默认保留明文） |
| `--encrypt-tests` | 测试文件也加密（默认保留明文） |
| `--runtime-pyc` | runtime 只发布 sourceless `.pyc`（默认；可显式指定） |
| `--runtime-native` | 用 Cython 把 runtime 编译为原生扩展（与 `--runtime-pyc` / `--runtime-source` 互斥） |
| `--runtime-source` | runtime 保留 `.py` 源码，仅用于调试；会暴露解密逻辑 |
| `--no-verify` | 跳过产物上的 `manage.py check` |
| `--force` | 覆盖不是 PyShield 构建的输出目录 |
| `--dry-run` | 只打印计划，不写文件 |

密码套件：

| 取值 | 实际算法 | 部署依赖 |
| --- | --- | --- |
| `auto` | 有 `cryptography` 时 AES-256-GCM，否则 ChaCha20 + HMAC-SHA256 | 视构建机而定 |
| `aesgcm` | AES-256-GCM | `cryptography` |
| `chacha` | 有 `cryptography` 时 ChaCha20-Poly1305，否则纯 Python ChaCha20 + HMAC-SHA256 | 视构建机而定 |
| `chacha-pure` | 强制纯 Python ChaCha20 + HMAC-SHA256 | 无 |

构建过程：

1. 复制目标项目的数据文件，忽略 `.git`、虚拟环境、`__pycache__`、数据库、日志等；
2. 发现 `manage.py` 与带 `__init__.py` 的包，默认排除 `migrations` 和 `tests`；
3. 逐模块 `compile → marshal → zlib → AEAD → Base85`，写入 stub；
4. 从内嵌压缩资源解出 runtime，写入构建 ID、主密钥、Ed25519 公钥、算法、完整性模式与 Python 小版本；默认编译为 `.pyc`；
5. 生成并签名 `pyshield.lic`（含签发日期、到期日、宽限期与绑定模式）；
6. 生成并签名 `.pyshield-manifest.json`（完整性模式非 `off` 时）；
7. 在产物上执行 `manage.py check`；
8. 写出构建密钥，并清理 `__pycache__`。

产物：

- `<output>/`：可部署目录，包含 stub、`_shield_runtime/`、`pyshield.lic` 和所有数据文件；
- `<output>.shield-secret.json`：构建密钥（格式 3，含 `master_key`、派生出的 `build_key`、`signing_key`），只保存在构建方。

查看构建信息：

```bash
python shield.py inspect --secret <output>.shield-secret.json
python shield.py fingerprint
python shield.py fingerprint --json
python shield.py selftest
```

### 桌面构建器（Windows）

`PyShieldBuilder.exe` 把构建引擎、Web UI、runtime 模板和可选 `cryptography` 全部打进单文件，双击即可使用：

1. 选择包含 `manage.py` 的 Django 项目根目录；
2. 默认输出到同级 `<项目名>-shielded`，构建密钥写到 `<输出目录>.shield-secret.json`；
3. 选择算法、运行时、完整性、许可证与设备绑定；
4. 点击“开始加密”，在确认弹窗中核对输出与许可证；确认后消耗 1 次构建额度并进入构建日志页，查看阶段、进度与日志。

桌面版内置 CPython 3.12，产物必须部署在 3.12.x；`manage.py check` 使用项目自己的 Python，仅要求同小版本且安装了 Django，找不到时自动跳过。设置与最近 100 次构建记录保存在 `%APPDATA%\PyShieldBuilder\state.sqlite3`（`PYSHELD_GUI_CONFIG_DIR` 可覆盖），程序目录另存一份校验副本；两份不一致时程序会阻止保存与构建，并在界面顶部要求选择副本修复，证据保留在 `state-incidents/`。该机制只检测单侧修改/误损坏，不是防本机攻击者的安全边界。

打包与完整说明见[桌面构建器文档](desktop-builder.md)。

## 4. 部署与运行

将整个输出目录发布到目标机器，保持以下文件完整：

- `_shield_runtime/`
- `pyshield.lic`
- `.pyshield-manifest.json`（`--integrity off` 时不生成）
- 所有 stub 与数据文件

使用首次启动绑定时，部署目录会在第一次运行后新增 `.pyshield-binding.json`；它由 runtime 生成，不需要随包构建。

部署后先做只读检查，再启动服务：

```bash
cd <output>
python -m _shield_runtime verify            # 校验清单签名与 code/runtime 哈希
python -m _shield_runtime verify --full     # 额外校验模板与数据文件
python -m _shield_runtime status            # 许可证、到期日、宽限期、最后运行日期
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

`status` 与 `verify` 都支持 `--json`，便于部署脚本判断结果；失败时退出码为 1。

启动方式与普通 Django 项目一致：

```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

生产环境请使用正式的 WSGI/ASGI 服务器，并按目标项目的常规方式配置静态文件与 `ALLOWED_HOSTS`。

### 4.1 运行期检查顺序

首次加载任一受保护模块时，runtime 依次检查：

1. CPython 与 Python 小版本；
2. 包清单存在、格式、`build_id`、算法、完整性模式与 Ed25519 签名；
3. 清单中 `code` 与 `runtime` 文件的 SHA-256；`full` 模式还包括 `data` 文件；
4. 许可证路径、AEAD 解封、许可证签名与 `build_id`；
5. 绑定模式：首次启动绑定读取/写入 `.pyshield-binding.json`，固定指纹模式比对许可证白名单；
6. 时钟状态（HMAC 校验、回拨检测）；
7. 签发日期 `not_before`、到期日与宽限期。

任何一步失败都会抛出 `LicenseError` / `ImportError`，模块不会以明文落地。校验结果在进程内缓存；`license_status(refresh=True)` 或 `reload_license()` 可重新读取许可证。

### 4.2 配置外置

建议把现场差异放在以下明文中，它们默认不加密，也不进入完整性清单：

- `local_settings.py` / `settings_local.py`（在 `settings.py` 末尾 `from .local_settings import *`）；
- `.env`、`.env.*`（由 `django-environ`、`python-dotenv` 等读取）；
- 进程环境变量（数据库地址、`ALLOWED_HOSTS`、密钥等）；
- 数据库 `*.sqlite3`、日志、`media/`、`staticfiles/`。

需要额外允许现场修改的文件，用 `--mutable "path/pattern"` 声明；需要额外排除加密的模块，用 `--exclude`。

### 4.3 可写目录与时间同步

- 续期时通常直接把新 `pyshield.lic` 写到原位置，运行用户需要该目录的写权限；也可用 `PYSHELD_LICENSE` 指向外部可写路径。
- 首次启动绑定会在输出目录写入 `.pyshield-binding.json`，并在 `_shield_runtime/` 写入独立锚点，因此该目录必须可写；只读部署用 `PYSHELD_BINDING_FILE` 指向可写位置（绑定记录放在包外会削弱复制防护）。写入失败时启动会被拒绝，避免绑定被静默跳过；删除主记录会从锚点恢复。PyInstaller onefile 或任何非 onedir `_MEIPASS` 都会被视为临时目录并拒绝首次绑定；设置 `PYSHELD_BINDING_FILE` 也不能关闭该检查，请改用固定指纹、onedir 或真正的持久路径。
- 时钟状态写入 `~/.pyshield/<build_id>.state` 和独立锚点（默认在平台用户状态目录）；可用 `PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR` 指定。Windows 使用 DPAPI，其它平台使用 HMAC；MAC 密钥由 `build_key` 派生。写入使用唯一临时文件、共享冲突重试和跨进程锁；删除单份会从另一份恢复，两份日期不一致时取较新值并重写两份。旧公开-MAC 状态默认拒绝，只有一次性升级时才临时设置 `PYSHELD_ALLOW_LEGACY_STATE=1`。只读部署默认会因无法持久化而拒绝启动；确认接受回拨检测失效时设置 `PYSHELD_ALLOW_VOLATILE_STATE=1`。默认状态按 OS 用户隔离，多用户共用机器时应把 `PYSHELD_STATE_ANCHOR_DIR` 指向机器级 ACL 目录。
- 部署机应启用 NTP/时间同步。容器重建、快照恢复或虚拟机迁移后如果时钟明显回拨，runtime 会拒绝启动，先校正时间再启动。
- 许可证带签发日期，现场时钟早于签发日期超过 1 天同样会拒绝。

### 4.4 现场允许的变更

可以修改：许可证（通过 `issue --out` 生成的新文件）、`local_settings.py`、`.env`、数据库、日志、`media/`、`staticfiles/`，以及构建时用 `--mutable` 声明的文件。

不要手工修改：stub、`_shield_runtime/`、`.pyshield-manifest.json`。`--integrity full` 下模板与静态数据也被校验，修改前应先重新构建或用 `--mutable` 声明。

原生 runtime 的 `.pyd`/`.so` 与操作系统、CPU 架构和 Python ABI 绑定，必须在与部署机一致的环境中构建。

## 5. 许可证操作

### 5.1 设备绑定模式

| 模式 | 构建参数 | 说明 |
| --- | --- | --- |
| 不绑定 | 默认 | 任意兼容机器运行 |
| 首次启动绑定 | `--bind-first-run` | 第一次启动时自动锁定客户机 |
| 指定指纹 | `-b <指纹>`（可重复） | 构建时写入白名单 |

构建时拿不到客户机指纹，用首次启动绑定：

```bash
python shield.py build -p <project> -o <output> --bind-first-run -e 2027-12-31
```

- 首次启动写入 `<output>/.pyshield-binding.json` 与 `_shield_runtime/.pyshield-binding.json.anchor`，包含 `build_id`、机器指纹、绑定时间和 `build_key` 派生的 HMAC；后续启动要求同一台机器；
- 记录被修改、整包复制到另一台机器、或替换成其他机器的记录都会拒绝加载；
- 首次启动绑定在第一次启动应用、或运行 `python -m _shield_runtime status` 时生效；不要在非目标机器上执行 `status`；
- 构建期的 `manage.py check` 使用临时绑定文件，不会占用客户机的首次绑定；
- 只读部署用 `PYSHELD_BINDING_FILE` 指向可写文件；该变量是调试/特殊部署入口，绑定放在包外会削弱复制防护；
- 硬件更换需删除旧绑定记录后重新绑定，或改用 `-b` 固定指纹许可证。离线方案无法阻止有本地写权限的人重新绑定。

取指纹：

```bash
python shield.py fingerprint
cd <output> && python -m _shield_runtime fingerprint
```

### 5.2 重新签发与续期

重新签发许可证（不需要重新构建）：

```bash
python shield.py issue --secret <output>.shield-secret.json -e 30
python shield.py issue --secret <output>.shield-secret.json -e 2027-12-31
python shield.py issue --secret <output>.shield-secret.json -e 2027-12-31 --grace-days 14
python shield.py issue --secret <output>.shield-secret.json -b <机器指纹>
python shield.py issue --secret <output>.shield-secret.json --bind-first-run
python shield.py issue --secret <output>.shield-secret.json --customer "客户名称"
python shield.py issue --secret <output>.shield-secret.json --out /secure/pyshield.lic -e 30
```

说明：

- **到期只影响 `pyshield.lic`**：业务包、清单、构建密钥都仍然有效，重新签发并替换该文件即可，不需要重新构建或重新上传整包；
- `issue` 默认继承现有许可证的到期日、绑定模式、指纹、宽限期和客户标识，只覆盖显式传入的 `-e` / `-b` / `--bind-first-run` / `--grace-days` / `--customer`；
- 首次启动绑定记录使用不随续期改变的 `build_key` 签名，续期后客户机无需重新绑定；
- `-e` 接受“从今天起的天数”或 `YYYY-MM-DD`，到期日当天仍然有效；不传 `-e` 且原许可证已存在时保持原到期日；
- 许可证包含 `not_before`（本次签发日期）；现场时钟早于它超过 1 天会拒绝加载；
- 默认写回构建时记录的许可证路径，可用 `--out` 指定其他位置；替换现场许可证后无需重启进程即可通过 `reload_license()` 生效；
- `--no-restrictions` 明确签发无限制许可证（无到期、不绑定设备）；
- 许可证格式为 v3，载荷只包含授权字段，不含模块解密密钥；载荷由构建时生成的 Ed25519 私钥签名，runtime 只内置公钥，即使攻击者提取对称主密钥也无法伪造许可证。

runtime 按以下顺序查找许可证：

1. 环境变量 `PYSHELD_LICENSE` 指向的文件；
2. `manage.py` 同目录的 `pyshield.lic`；
3. `_shield_runtime/` 包内的 `pyshield.lic`。

### 5.3 时钟回拨与热重载

- runtime 在许可证校验通过后，把 `last_seen`（日期）和 `build_id` 用 DPAPI（Windows）或 HMAC（其它平台）封装，写入 `~/.pyshield/<build_id>.state` 及其独立锚点，可用 `PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR` 覆盖；MAC 密钥由 `build_key` 派生；
- 如果今天比 `last_seen` 早超过 1 天，判定为时钟回拨并拒绝加载；状态被修改、损坏或两份记录无法解密会拒绝；两份日期不一致时取较新值并重写两份，不再因一次部分写入失败而永久拒绝；删除单份会从另一份恢复；
- 删除状态文件会重置历史，这是离线方案的固有边界；请同时使用 NTP 和授权流程；
- 热重载示例：

```python
from _shield_runtime import license_status, reload_license

license_status(refresh=True)   # 重新读取许可证并返回状态
reload_license()               # 等价入口，返回同样的状态字典
```

已导入的模块继续使用旧进程内的 code object；刷新后新导入的模块会按新许可证的到期日、宽限期与指纹状态放行。格式 3 许可证不含 `build_key`，模块密钥由构建 ID 与主密钥派生，续期不会改变它；跨构建替换许可证仍需重新构建。

### 5.4 构建方复核交付包

```bash
python shield.py verify -d <output> --secret <output>.shield-secret.json
python shield.py verify -d <output> --secret <output>.shield-secret.json --full
python shield.py verify -d <output> --public-key <Base85 公钥> --skip-license
```

`verify` 会检查清单签名、`build_id`、受保护文件哈希与许可证；`--full` 同时校验数据文件，`--skip-license` 只做完整性检查。成功退出码为 0，失败为 1 并列出问题。

`issue` 需要构建密钥中的 `signing_key`，因此必须使用当前版本生成的 `*.shield-secret.json`；旧版格式的 secret 会提示重新构建。

如果许可证放在外部路径：

```bash
PYSHELD_LICENSE=/secure/pyshield.lic python manage.py check
```

## 6. 更新与发布

1. 修改目标项目源码；
2. 在目标项目中执行 `manage.py check`；
3. 停止正在运行的服务（Windows 下避免文件占用）；
4. 重新执行构建命令；
5. 在产物上验证 `manage.py check` 与关键页面；
6. 发布整个输出目录；
7. 构建密钥继续留在构建方，不随包发布。

不要直接修改产物中的 stub、runtime、`.pyshield-manifest.json` 或许可证；这些文件会在下次构建时被覆盖，手工改动还会导致完整性校验或验签失败。现场配置与数据请放在可变文件中。

> v0.3.0 起模块 AAD 绑定 `build_id`，并新增完整性清单、签发日期与宽限期。要获得这些新特性，v0.2 及更早版本构建的产物需要用 v0.3 重新构建；v0.2 生成的格式 2 许可证仍可被新 runtime 读取（缺少的新字段按默认值处理），`issue` 会用当前版本重写许可证。
>
> v0.5.0 起支持首次启动绑定与续期继承。旧构建的许可证没有 `bind_mode` 字段，runtime 按“有指纹即固定绑定、否则不绑定”兼容处理；`issue` 也会按同样规则继承。

## 7. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ImportError: PyShield: cannot decrypt protected module ...` | 密文被改动、损坏，或来自其他构建（`build_id` 不匹配） | 重新构建或恢复完整加密包 |
| `package integrity check failed` | 受保护文件或 runtime 被修改/删除，或清单被替换 | 从原始交付包恢复文件；必要时重新构建 |
| `package manifest ... is missing` | 未部署 `.pyshield-manifest.json` | 部署完整目录，或构建时使用 `--integrity off` |
| 许可证签名无效 | 许可证被修改，或公钥/私钥不匹配 | 使用原构建 secret 重新执行 `issue`；secret 丢失则重新构建 |
| 许可证过期 | `expires` 已过且宽限期结束 | 使用 `issue` 续期，只替换 `pyshield.lic` 后重启或热重载，无需重装整包 |
| `this deployment is bound to machine ...` | 首次启动绑定已锁定另一台机器，通常是整包被复制 | 恢复到原机器；确需换机时由构建方删除绑定记录或改用 `-b` 重新签发 |
| `binding file ... was modified` | `.pyshield-binding.json` 被编辑、损坏或替换 | 从备份恢复；确认换机后再删除并重新绑定 |
| `cannot record the first-run binding` | 输出目录只读，首次启动无法写入绑定记录 | 赋予目录写权限，或把 `PYSHELD_BINDING_FILE` 指向可写路径 |
| `cannot record clock state` | 时钟状态目录只读，且未显式允许易失状态 | 赋予用户状态目录写权限，或确认接受回拨检测失效后设置 `PYSHELD_ALLOW_VOLATILE_STATE=1` |
| `license is not valid before ...` | 现场时钟早于签发日期超过 1 天 | 校正系统时间或重新签发 |
| `system clock rollback` | 时钟早于上次运行日期超过 1 天 | 校正 NTP/系统时间；不要删除状态文件来绕过 |
| 时钟状态文件损坏 | 状态文件被修改、DPAPI 不匹配或写入中断 | 恢复主记录与锚点，或校正时间；确认 `PYSHELD_STATE_DIR` / `PYSHELD_STATE_ANCHOR_DIR` 可写。两份日期不一致会自动取较新值，不需要手动删除 |
| 原生运行时不可用 | 缺少 Cython、C 编译器或 strip/llvm-strip | 安装工具链，或切换为字节码运行时；桌面 GUI 会锁定该选项并显示原因 |
| 指纹不匹配 | 许可证绑定的是其他机器 | 在目标机器取指纹后用 `issue -b` 重新签发 |
| 找不到许可证 | 许可证路径不正确或未随包发布 | 放入输出根目录，或设置 `PYSHELD_LICENSE` |
| `ModuleNotFoundError: cryptography` | 使用 AES-GCM / ChaCha20-Poly1305 但部署机未安装依赖 | 安装 `cryptography`，或改用 `--cipher chacha-pure` 重新构建 |
| Python 版本错误或加载异常 | 构建与部署的 Python 小版本不一致 | 使用相同的小版本 |
| `--runtime-native requires Cython and setuptools` | 构建机缺少原生编译依赖 | `pip install cython setuptools`，并安装 C 编译器 |
| 原生 runtime 编译失败 | 未找到 C 编译器或编译环境不完整 | Windows 可安装 MinGW-w64 或 MSVC Build Tools；确认 `gcc` 在 PATH 或位于 `C:\mingw64\bin` |
| 原生 runtime 导入失败 | `.pyd`/`.so` 与部署机平台或 Python ABI 不匹配 | 在目标操作系统、CPU 架构和 Python 小版本上重新构建 |
| 构建拒绝覆盖目录 | 输出目录不是 PyShield 构建 | 确认目录归属后再使用 `--force` |
| 静态资源 404 | 生产模式未配置静态文件服务 | 执行 `collectstatic` 并配置 Web 服务器 |

## 8. 测试

```bash
python -m unittest discover -s tests -v
```

核心测试覆盖：

- ChaCha20 RFC 8439 向量、信封封解、AAD/密文防篡改、构建 ID 绑定；
- Ed25519 RFC 8032 向量、pure/native 交叉验证、伪造许可证拒绝；
- 模块密钥隔离、Base85 往返、清单签名与文件校验；
- 许可证签发、过期、宽限期、签发日期、设备绑定、签名校验、热重载；
- 构建器生成结构、runtime 安装、stub 可执行、输出目录保护；
- 完整性模式：`protected` 允许数据修改、`full` 检测数据修改、`off` 不生成清单、`--mutable` 排除；
- 时钟回拨检测、`verify` 命令、runtime `status` / `verify` 入口；
- 桌面构建器：默认输出、表单校验、设置/历史持久化、后端探针与进程内构建；
- 原生 runtime 构建与 `--cipher chacha-pure` 快速解密（需要 Cython 与 C 编译器，否则跳过）。

## 9. 安全事项

- `*.shield-secret.json` 同时包含 `master_key`、`build_key` 和 `signing_key`，是构建方最高权限密钥，**绝不能随包发布**；泄露后应重新构建并作废旧包。
- 许可证格式 v3 使用 Ed25519 签名，修改到期日、机器指纹等字段会导致验签失败；格式 3 不含 `build_key`；旧格式 2 可由构建侧读取用于续期，但新 runtime 只接受格式 3。
- 完整性清单由 runtime 自身校验，且只在进程首次加载时扫描一次；5 分钟重验只覆盖许可证/绑定/时钟。如果攻击者能整体替换 `_shield_runtime/`，清单也会失效。它用于发现现场误改、局部篡改和跨构建移植，不是二进制级防篡改。
- 时钟状态与回拨检测是尽力而为：MAC 密钥由 `build_key` 派生，提取 master 后仍可伪造；同时删除主记录与锚点仍会重置历史；默认状态按 OS 用户隔离。请结合 NTP、机器级锚点、服务端时间、合同和授权流程。
- 原生 runtime 把主密钥、验签逻辑和快速 ChaCha 编译进 `.pyd`/`.so`，显著提高静态提取门槛，但无法阻止有能力的攻击者在运行时内存中提取。
- 发布卫生（Authenticode、PDB 路径、发布暴露审计）见 [security-hardening.md](security-hardening.md)。
- 迁移与测试默认保持明文；如包含敏感逻辑，使用 `--encrypt-migrations` / `--encrypt-tests`。
- 加密提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM；服务端代码最终会在内存中还原，请配合合同、授权、SaaS 化等手段。

# PyShield 执行手册

PyShield 是一套自研的 Python / Django 源码保护工具：构建期把 Python 模块编译、压缩、加密并替换为 stub，运行期在内存中解密执行；模板、静态资源、迁移文件保持可用。

本手册面向 PyShield 的构建与部署人员，覆盖构建、部署、许可证、更新和故障排查。

## 1. 项目组成

| 路径 | 说明 |
| --- | --- |
| `shield.py` | 命令行入口 |
| `pyshield/builder.py` | 模块发现、加密、stub、runtime、许可证与产物校验 |
| `pyshield/crypto.py` | HKDF、AEAD 信封、AES-256-GCM、ChaCha20-Poly1305、ChaCha20 + HMAC-SHA256 |
| `pyshield/ed25519.py` | 许可证签名与验签（RFC 8032，纯 Python，优先用 cryptography） |
| `pyshield/licensing.py` | 许可证与构建密钥读写 |
| `pyshield/fingerprint.py` | 跨平台机器指纹 |
| `pyshield/runtime_template/` | 随包运行库模板，含 Cython 原生 ChaCha 源文件 |
| `tests/` | 加密、签名、许可证与构建流程测试 |
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
| `-b, --bind-device` | 绑定机器指纹，可重复 |
| `--cipher` | `auto`（默认）、`chacha`、`chacha-pure`、`aesgcm`，见下表 |
| `--exclude` | 额外排除模式（fnmatch），可重复 |
| `--encrypt-migrations` | 迁移文件也加密（默认保留明文） |
| `--encrypt-tests` | 测试文件也加密（默认保留明文） |
| `--runtime-pyc` | runtime 只发布 sourceless `.pyc` |
| `--runtime-native` | 用 Cython 把 runtime 编译为原生扩展（与 `--runtime-pyc` 互斥） |
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
4. 安装 runtime，写入构建 ID、主密钥、Ed25519 公钥、算法与 Python 小版本；
5. 生成并签名 `pyshield.lic`；
6. 在产物上执行 `manage.py check`；
7. 写出构建密钥，并清理 `__pycache__`。

产物：

- `<output>/`：可部署目录，包含 stub、`_shield_runtime/`、`pyshield.lic` 和所有数据文件；
- `<output>.shield-secret.json`：构建密钥（含 `master_key`、`build_key`、`signing_key`），只保存在构建方。

查看构建信息：

```bash
python shield.py inspect --secret <output>.shield-secret.json
python shield.py fingerprint
python shield.py fingerprint --json
python shield.py selftest
```

## 4. 部署与运行

将整个输出目录发布到目标机器，保持以下文件完整：

- `_shield_runtime/`
- `pyshield.lic`
- 所有 stub 与数据文件

启动方式与普通 Django 项目一致：

```bash
cd <output>
python manage.py check
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

生产环境请使用正式的 WSGI/ASGI 服务器，并按目标项目的常规方式配置静态文件与 `ALLOWED_HOSTS`。

运行时按以下顺序查找许可证：

1. 环境变量 `PYSHELD_LICENSE` 指向的文件；
2. `manage.py` 同目录的 `pyshield.lic`；
3. `_shield_runtime/` 包内的 `pyshield.lic`。

许可证校验在首次加载模块时进行并缓存，校验内容包括许可证格式、Ed25519 签名、Python 小版本、构建 ID、到期日和机器指纹。许可证被修改（例如去掉到期日）会因签名不匹配而拒绝加载。

原生 runtime 的 `.pyd`/`.so` 与操作系统、CPU 架构和 Python ABI 绑定，必须在与部署机一致的环境中构建。

## 5. 许可证操作

取目标机器指纹：

```bash
python shield.py fingerprint
```

重新签发许可证（不需要重新构建）：

```bash
python shield.py issue --secret <output>.shield-secret.json -e 30
python shield.py issue --secret <output>.shield-secret.json -e 2027-12-31
python shield.py issue --secret <output>.shield-secret.json -b <机器指纹>
python shield.py issue --secret <output>.shield-secret.json --customer "客户名称"
```

说明：

- 许可证格式为 v2，载荷由构建时生成的 Ed25519 私钥签名；runtime 只内置公钥，因此即使攻击者提取了对称主密钥，也无法伪造或修改许可证；
- `-e` 接受“从今天起的天数”或 `YYYY-MM-DD`，到期日当天仍然有效；
- `-b` 可重复，绑定多台机器；
- **每次签发都会按本次参数重建许可证并重新签名**：不传 `-e` 表示永不过期，不传 `-b` 表示不限机器，不传 `--customer` 会清空客户标识。续期时若需保留绑定，必须同时传入 `-b`；
- 默认写回构建时记录的许可证路径，可用 `--out` 指定其他位置；
- `--no-restrictions` 明确签发无限制许可证。

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

不要直接修改产物中的 stub、runtime 或许可证；这些文件会在下次构建时被覆盖，手工改动还会导致认证失败。

## 7. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ImportError: PyShield: cannot decrypt protected module ...` | 密文被改动、损坏或与许可证不匹配 | 重新构建或恢复完整加密包 |
| 许可证签名无效 | 许可证被修改，或公钥/私钥不匹配 | 使用原构建 secret 重新执行 `issue`；secret 丢失则重新构建 |
| 许可证过期 | `expires` 已过 | 使用 `issue` 续期并重启 |
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

- ChaCha20 RFC 8439 向量、信封封解、AAD/密文防篡改；
- Ed25519 RFC 8032 向量、pure/native 交叉验证、伪造许可证拒绝；
- 模块密钥隔离、Base85 往返；
- 许可证签发、过期、设备绑定、签名校验；
- 构建器生成结构、runtime 安装、stub 可执行、输出目录保护；
- 原生 runtime 构建与 `--cipher chacha-pure` 快速解密（需要 Cython 与 C 编译器，否则跳过）。

## 9. 安全事项

- `*.shield-secret.json` 同时包含 `master_key`、`build_key` 和 `signing_key`，是构建方最高权限密钥，**绝不能随包发布**；泄露后应重新构建并作废旧包。
- 许可证格式 v2 使用 Ed25519 签名，修改到期日、机器指纹等字段会导致验签失败；旧格式许可证不被新 runtime 接受。
- 原生 runtime 把主密钥、验签逻辑和快速 ChaCha 编译进 `.pyd`/`.so`，显著提高静态提取门槛，但无法阻止有能力的攻击者在运行时内存中提取。
- 迁移与测试默认保持明文；如包含敏感逻辑，使用 `--encrypt-migrations` / `--encrypt-tests`。
- 加密提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM；服务端代码最终会在内存中还原，请配合合同、授权、SaaS 化等手段。

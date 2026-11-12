# PyShield 执行手册

PyShield 是一套自研的 Python / Django 源码保护工具：构建期把 Python 模块编译、压缩、加密并替换为 stub，运行期在内存中解密执行；模板、静态资源、迁移文件保持可用。

本手册面向 PyShield 的构建与部署人员，覆盖构建、部署、许可证、更新和故障排查。

## 1. 项目组成

| 路径 | 说明 |
| --- | --- |
| `shield.py` | 命令行入口 |
| `pyshield/builder.py` | 模块发现、加密、stub、runtime、许可证与产物校验 |
| `pyshield/crypto.py` | HKDF、AEAD 信封、AES-256-GCM、ChaCha20 + HMAC-SHA256 |
| `pyshield/licensing.py` | 许可证与构建密钥读写 |
| `pyshield/fingerprint.py` | 跨平台机器指纹 |
| `pyshield/runtime_template/` | 随包运行库模板 |
| `tests/` | 加密、许可证与构建流程测试 |
| `docs/` | 本手册与《实现思路》 |

核心实现只依赖 Python 标准库；AES-256-GCM 需要可选的 `cryptography`。

## 2. 环境要求

构建机：

- CPython，推荐 3.12（在 3.12.0 上验证）
- 可选：`cryptography`（启用 AES-256-GCM）
- 目标项目：Python / Django 项目根目录存在 `manage.py`

部署机：

- 与构建机**相同小版本**的 CPython（例如双方都是 3.12）
- 目标项目自身的运行依赖（Django 等）
- 若产物使用 AES-GCM，需要安装 `cryptography`；ChaCha20 套件无额外依赖

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
| `--cipher` | `auto`（默认）、`chacha`、`aesgcm` |
| `--exclude` | 额外排除模式（fnmatch），可重复 |
| `--encrypt-migrations` | 迁移文件也加密（默认保留明文） |
| `--encrypt-tests` | 测试文件也加密（默认保留明文） |
| `--runtime-pyc` | runtime 只发布 sourceless `.pyc` |
| `--no-verify` | 跳过产物上的 `manage.py check` |
| `--force` | 覆盖不是 PyShield 构建的输出目录 |
| `--dry-run` | 只打印计划，不写文件 |

构建过程：

1. 复制目标项目的数据文件，忽略 `.git`、虚拟环境、`__pycache__`、数据库、日志等；
2. 发现 `manage.py` 与带 `__init__.py` 的包，默认排除 `migrations` 和 `tests`；
3. 逐模块 `compile → marshal → zlib → AEAD → Base85`，写入 stub；
4. 安装 runtime，写入构建 ID、主密钥、算法与 Python 小版本；
5. 生成 `pyshield.lic`；
6. 在产物上执行 `manage.py check`；
7. 写出构建密钥，并清理 `__pycache__`。

产物：

- `<output>/`：可部署目录，包含 stub、`_shield_runtime/`、`pyshield.lic` 和所有数据文件；
- `<output>.shield-secret.json`：构建密钥（含 `master_key` 与 `build_key`），只保存在构建方。

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

许可证校验在首次加载模块时进行并缓存，校验内容包括 Python 小版本、构建 ID、到期日和机器指纹。

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

- `-e` 接受“从今天起的天数”或 `YYYY-MM-DD`，到期日当天仍然有效；
- `-b` 可重复，绑定多台机器；
- **每次签发都会按本次参数重建许可证**：不传 `-e` 表示永不过期，不传 `-b` 表示不限机器，不传 `--customer` 会清空客户标识。续期时若需保留绑定，必须同时传入 `-b`；
- 默认写回构建时记录的许可证路径，可用 `--out` 指定其他位置；
- `--no-restrictions` 明确签发无限制许可证。

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
| 许可证过期 | `expires` 已过 | 使用 `issue` 续期并重启 |
| 指纹不匹配 | 许可证绑定的是其他机器 | 在目标机器取指纹后用 `issue -b` 重新签发 |
| 找不到许可证 | 许可证路径不正确或未随包发布 | 放入输出根目录，或设置 `PYSHELD_LICENSE` |
| `ModuleNotFoundError: cryptography` | 使用 AES-GCM 但部署机未安装依赖 | 安装 `cryptography`，或改用 `--cipher chacha` 重新构建 |
| Python 版本错误或加载异常 | 构建与部署的 Python 小版本不一致 | 使用相同的小版本 |
| 构建拒绝覆盖目录 | 输出目录不是 PyShield 构建 | 确认目录归属后再使用 `--force` |
| 静态资源 404 | 生产模式未配置静态文件服务 | 执行 `collectstatic` 并配置 Web 服务器 |

## 8. 测试

```bash
python -m unittest discover -s tests -v
```

核心测试覆盖：

- ChaCha20 RFC 8439 向量、信封封解、AAD/密文防篡改；
- 模块密钥隔离、Base85 往返；
- 许可证签发、过期、设备绑定、构建密钥校验；
- 构建器生成结构、runtime 安装、stub 可执行、输出目录保护。

## 9. 安全事项

- `*.shield-secret.json` 同时包含 `master_key` 与 `build_key`，等同于构建方主密钥，泄露后应重新构建并作废旧包。
- 许可证与 runtime 必须随包发布，缺少任一文件都无法加载模块。
- 迁移与测试默认保持明文；如包含敏感逻辑，使用 `--encrypt-migrations` / `--encrypt-tests`。
- 加密提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM；服务端代码最终会在内存中还原，请配合合同、授权、SaaS 化等手段。

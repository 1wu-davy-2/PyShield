# PyShield

PyShield 是一套自研的 Python / Django 源码保护工具。构建期把业务模块编译、压缩、加密并替换为 stub，运行期校验许可证后在内存中解密执行；模板、静态资源和迁移文件保持可用。

核心实现只依赖 Python 标准库，不依赖 Pyarmor。

- 执行手册：[docs/runbook.md](docs/runbook.md)
- 实现思路：[docs/implementation.md](docs/implementation.md)
- 许可证：[AGPL-3.0](LICENSE)

## 特性

- **源码不落地**：模块编译为 code object 后经 zlib 压缩，再用每个模块独立的 AEAD 密钥加密；发布目录只保留 Base85 stub。
- **Django 友好**：保留正常导入、反射、模板、ORM、URL 路由与管理命令；迁移、测试和静态资源默认不加密。
- **离线许可证**：支持到期日、机器绑定和客户标识，可离线重新签发，无需重新构建。
- **许可证防伪**：许可证格式 v2 使用 Ed25519 签名，runtime 只内置公钥；即使攻击者提取对称主密钥，也无法伪造或篡改许可证。
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

- `<output>/`：可部署目录，包含 stub、`_shield_runtime/`、`pyshield.lic` 和数据文件；
- `<output>.shield-secret.json`：构建密钥，只保存在构建方，**绝不能随包发布**。

部署：

```bash
cd <output>
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

生产环境请使用正式的 WSGI / ASGI 服务器，并按项目常规方式配置静态文件与 `ALLOWED_HOSTS`。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `python shield.py build -p <project> -o <output>` | 构建受保护的发布目录 |
| `python shield.py issue --secret <secret> -e 30` | 从构建密钥重新签发许可证 |
| `python shield.py inspect --secret <secret>` | 查看构建与许可证信息 |
| `python shield.py fingerprint` | 打印当前机器指纹 |
| `python shield.py selftest` | 运行密码算法自检 |

构建的常用选项：

| 选项 | 说明 |
| --- | --- |
| `-e, --expire` | 到期时间：天数或 `YYYY-MM-DD` |
| `-b, --bind-device` | 绑定机器指纹，可重复 |
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

## 许可证签发

在目标机器取指纹：

```bash
python shield.py fingerprint
```

重新签发许可证（不需要重新构建）：

```bash
python shield.py issue --secret <output>.shield-secret.json -e 2027-12-31 -b <机器指纹>
```

每次签发都会按本次参数重建并重新签名许可证；不传 `-b` 表示不限机器。`issue` 需要构建密钥中的 `signing_key`，因此 `*.shield-secret.json` 必须妥善保管。

## 安全边界

- 服务端代码最终要在内存中解密，runtime 中的 `master_key` 也可能被提取；本工具提高的是逆向与源码泄露门槛，不是绝对不可破解的 DRM。
- Ed25519 签名可以阻止伪造许可证，但无法阻止攻击者绕过校验逻辑或 dump 已解密的代码。
- 构建与部署必须使用相同的 Python 小版本；原生 runtime 还要求相同的操作系统和 CPU 架构。
- 迁移与测试默认保持明文；模板与静态资源不加密，前端代码对用户可见。
- 泄漏 `*.shield-secret.json` 等同于泄漏构建方最高权限密钥，应立即重新构建并作废旧包。

## 文档

- [执行手册](docs/runbook.md)：构建、部署、许可证、更新与故障排查
- [实现思路](docs/implementation.md)：密钥体系、信封格式、运行时加载与设计取舍

## 许可证

PyShield 以 **GNU Affero General Public License v3.0** 发布，完整条款见 [LICENSE](LICENSE)。

AGPL 包含“网络 Copyleft”条款：修改后的程序如果通过网络向用户提供服务，必须向这些用户提供对应的完整源代码。若你的使用场景无法满足 AGPL（例如闭源 SaaS 或商业分发），请先评估授权方案。

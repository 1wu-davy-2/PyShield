# PyShield 授权门户（license-portal）

`license-portal` 是独立部署的 PyShield 授权签发、在线验签与运营管理服务：后端是纯标准库 + SQLite 的 JSON API，前端是 React 单页应用（`web/`），包含公共试用/购买页面和需要登录的管理端。服务端持有 Ed25519 签发私钥，签发好的授权文档由客户端用内置公钥离线验签；服务端与桌面构建器解耦，可以单独部署、升级和备份。

## 1. 功能概览

- **试用申请**：桌面端打开 `/activate?serial=...&machine=...` 时自动携带序列号和机器码；用户填写邮箱、手机号、姓名/称呼（表单字段 `name`，即页面上的用户名）、个人或公司、公司名称，并勾选同意条款。
- **邮箱验证码**：6 位数字验证码，10 分钟内有效，单个申请最多尝试 5 次；验证通过后签发试用授权。未收到时可重发，重发后旧码立即失效，同一申请每小时最多重发 5 次。
- **品牌化邮件**：验证码与系统通知使用与门户一致的深色页头、石灰绿强调和验证码卡片 HTML 模板，同时提供纯文本正文；发件人显示名、Reply-To、站点名称、支持邮箱与客服微信均可配置。
- **Cloudflare Turnstile**：配置 Site Key 和 Secret 后自动启用；开发环境可留空关闭。
- **试用码与深链**：签发成功后页面显示 `PST-XXXX-XXXX-XXXX` 试用码，并提供 `pyshield://activate?serial=...&code=...` 深链；未安装协议处理程序时也能复制试用码。
- **激活码**：管理端可批量生成专业版/企业版激活码（`PSP-...` / `PSE-...`），支持查看、复制、吊销和批次备注。
- **手动微信收款**：用户通过页面或微信联系运营人员，人工确认转账后由管理员生成激活码并发送；订单页用于记录 `pending / paid / fulfilled / cancelled`。
- **在线激活与验签**：桌面端提交激活码到 `/api/license/activate`，服务端返回已签名授权文档；客户端只用内置公钥验签，写入本地授权。
- **续费、升级、吊销**：管理端修改授权后 `revision` 加一；桌面端刷新状态时通过 `/api/license/status` 领取新文档。吊销后在线刷新或同步吊销列表即拒绝。
- **管理端**：概览、授权、激活码、客户（列表 + 详情）、订单、审计日志（支持 CSV 导出）、签发密钥公钥/指纹。
- **React 单页应用**：`license-portal/web` 使用 Vite + React + TypeScript + Ant Design；后端只提供 JSON，页面路由由前端负责，`npm run build` 产物由 Nginx 直接托管。
- **单机轻量部署**：仅依赖 Python 标准库和仓库内的 `pyshield` 共享模块，SQLite WAL 单文件存储，适合早期客户量。

> 服务端签发的文档与 `pyshield/tool_license.py` 使用的授权格式一致；客户端不会、也不能持有签发私钥。

## 2. 目录结构

```text
license-portal/
├── README.md                       # 本文件
├── config.example.json             # 生产配置样例（JSON，不能写注释）
├── license_portal/
│   ├── __init__.py                 # 把仓库根加入 sys.path
│   ├── __main__.py                 # CLI：init / run / admin / signing / selftest
│   ├── config.py                   # 默认值、JSON、PYSHELD_PORTAL_* 环境变量加载
│   ├── db.py                       # SQLite 连接、WAL、建表
│   ├── service.py                  # 试用、激活、续费、升级、吊销、审计等业务逻辑
│   ├── server.py                   # 标准库 HTTP 服务、路由、Cookie/CSRF/来源校验
│   ├── signing.py                  # Ed25519 签发密钥的生成、加密存储、轮换
│   ├── security.py                 # scrypt、Cookie 签名、敏感字段加解密
│   ├── validation.py               # 表单/API 参数校验与规范化
│   ├── mailer.py                   # SMTP 邮件；开发环境 ConsoleMailer
│   ├── captcha.py                  # Cloudflare Turnstile
│   └── api.py                      # 浏览器 JSON API（/api/public/*、/api/admin/*）
├── web/                            # React 控制台（Vite + TS + Ant Design）
│   ├── src/api/                    # fetch 封装、类型、TanStack Query hooks
│   ├── src/components/             # 布局、状态徽标、Turnstile、通用 UI
│   ├── src/pages/public/           # 首页、试用、验证、成功、升级、兑换
│   ├── src/pages/admin/            # 登录、概览、授权、激活码、客户、订单、审计、签发
│   ├── src/styles/app.css          # 设计 token 与页面样式
│   └── dist/                       # 构建产物（Nginx 托管的静态文件）
├── deploy/
│   ├── systemd/pyshield-license-portal.service
│   ├── nginx/pyshield-license-portal.conf
│   └── backup.md                   # 备份、恢复与演练手册
└── tests/                          # unittest 用例（含 API 与 SPA 托管）
```

部署时还需要仓库根下的共享包：

```text
pyshield/                           # crypto、ed25519、tool_license、trial_license 等
tools/build_commercial.py           # 把授权公钥和试用站点注入桌面构建器
```

`license_portal.__init__` 会把仓库根加入 `sys.path`，但生产环境仍建议显式设置 `PYTHONPATH`，避免工作目录变化导致 `import pyshield` 失败。

## 3. 快速开始（开发）

### 3.1 前置条件

- **Python 3.11+**：后端没有任何第三方依赖，只需要标准库和仓库里的 `pyshield` 包。
- **Node.js 18+**：仅在构建前端时需要；服务器上可以只放 `web/dist`，不装 Node。
- 保证仓库根（包含 `pyshield/` 的目录）和 `license-portal/` 都在模块搜索路径中。

在仓库根执行（PowerShell）：

```powershell
$env:PYTHONPATH = "$PWD\license-portal;$PWD"
python -m license_portal --version
```

Linux/macOS：

```bash
export PYTHONPATH="$PWD/license-portal:$PWD"
python -m license_portal --version
```

### 3.2 初始化

复制样例并按开发环境调整；也可以直接让默认值生效：

```powershell
Copy-Item license-portal/config.example.json license-portal/config.json
```

开发用最小配置示例（只写需要覆盖的字段）：

```json
{
  "data_dir": "license-data",
  "host": "127.0.0.1",
  "port": 8091,
  "site_url": "http://127.0.0.1:8091",
  "email_verification": true,
  "secure_cookies": false,
  "trust_proxy": false,
  "admin_allow_ips": []
}
```

初始化会创建数据目录、`portal.secret`、`signing.key`（口令加密）、`signing.pass`（0600）和 SQLite 数据库：

```bash
python -m license_portal init --config license-portal/config.json
```

命令会打印数据目录、数据库路径、签发密钥 ID、Base85 公钥和指纹。**公钥可以公开，私钥与口令不能进入客户端或版本库。**

### 3.3 创建管理员

管理员密码至少 10 个字符。密码可以通过 `PYSHELD_PORTAL_ADMIN_PASSWORD` 传入，避免留在 shell 历史里：

```powershell
$env:PYSHELD_PORTAL_ADMIN_PASSWORD = "请替换为至少10位的强密码"
python -m license_portal admin create --config license-portal/config.json --username admin
```

```bash
export PYSHELD_PORTAL_ADMIN_PASSWORD='请替换为至少10位的强密码'
python -m license_portal admin create --config license-portal/config.json --username admin
```

`init --username admin` 也可以一步完成初始化和管理员创建；`admin reset-password --username admin` 可重置密码，并会使该管理员的现有会话全部失效。

### 3.4 构建前端并启动开发服务

后端只提供 JSON，页面在 `web/` 里。首次运行先构建一次：

```bash
cd license-portal/web
npm install
npm run build          # 生成 web/dist，后端会自动托管
```

日常改前端时用 Vite 热更新（`/api` 会代理到 8091）：

```bash
cd license-portal/web && npm run dev   # http://127.0.0.1:5173
```

只调试后端时不需要 Node，启动服务和以前一样：

```bash
python -m license_portal run --config license-portal/config.json --dev
```

如果没有 `web/dist`，根路径会返回 503 并提示先执行 `npm run build`；`/api/*` 仍然可用。

`--dev` 的行为：

- 邮件使用 `ConsoleMailer`，6 位验证码直接打印到标准错误，不发送真实邮件。
- 强制关闭 `Secure` Cookie，允许 `http://127.0.0.1` 本地调试。
- 日志级别为 DEBUG。
- Turnstile 在 Site Key / Secret 均为空时关闭；需要联调人机校验时填入测试密钥。
- 如果希望跳过邮箱验证码，把配置中的 `email_verification` 设为 `false`；此时提交后立即签发。

打开试用页（序列号须符合 `PS-XXXX-XXXX-XXXX`，机器码为 32 位十六进制）：

```text
http://127.0.0.1:8091/activate?serial=PS-0000-0000-0000&machine=0123456789abcdef0123456789abcdef
```

管理端：

```text
http://127.0.0.1:8091/admin/login
```

### 3.5 自检

```bash
python -m license_portal selftest
python -m unittest discover -s license-portal/tests -v      # 后端 + API + SPA 托管
cd license-portal/web && npm run typecheck                  # 前端类型检查
```

## 4. 生产部署

### 4.1 目录、用户与权限

建议使用专用系统用户，代码、数据和配置分开：

| 路径 | 用途 | 建议属主/权限 |
| --- | --- | --- |
| `/opt/pyshield-portal` | 代码、虚拟环境、部署样例 | `root:root`，目录 0755，文件 0644 |
| `/var/lib/pyshield-portal` | `portal.sqlite3`、`portal.secret`、`signing.key`、`signing.pass` | `pyshield:pyshield`，目录 0700，文件 0600 |
| `/etc/pyshield-portal` | `config.json`、`portal.env` | `root:root` 或 `root:pyshield`；`portal.env` 必须 0600 |

创建用户和目录：

```bash
sudo useradd --system --home /opt/pyshield-portal --shell /usr/sbin/nologin pyshield
sudo install -d -o root -g root -m 0755 /opt/pyshield-portal
sudo install -d -o pyshield -g pyshield -m 0700 /var/lib/pyshield-portal
sudo install -d -o root -g pyshield -m 0750 /etc/pyshield-portal
```

不要把数据目录放进 Web 根目录、代码目录或 Nginx 可读目录。

### 4.2 安装代码与虚拟环境

把 `license-portal/` 和仓库根的 `pyshield/` 部署到 `/opt/pyshield-portal`，保留相对位置：

```bash
sudo rsync -a --delete --exclude '__pycache__' --exclude 'config.json' \
  ./license-portal/ /opt/pyshield-portal/license-portal/
sudo rsync -a --delete --exclude '__pycache__' \
  ./pyshield/ /opt/pyshield-portal/pyshield/
sudo python3.11 -m venv /opt/pyshield-portal/.venv
sudo chown -R root:root /opt/pyshield-portal
```

服务运行时只需要读权限。`ProtectSystem=strict` 会让 `/opt` 只读；设置 `PYTHONDONTWRITEBYTECODE=1` 可以避免 Python 尝试写 `__pycache__`。

前端有两种上法，任选一种：

```bash
# A. 在服务器上构建（需要 Node 18+，构建完可以卸载）
cd /opt/pyshield-portal/license-portal/web
npm ci && npm run build          # 产物：web/dist

# B. 在 CI / 本机构建后只上传产物
rsync -a --delete license-portal/web/dist/ \
  /opt/pyshield-portal/license-portal/web/dist/
```

Nginx 的 `root` 指向 `web/dist`；如果让 Python 进程自己托管静态文件，把 `PYSHELD_PORTAL_WEB_DIR` 指向同一个目录即可。

### 4.3 配置

JSON 配置使用 **snake_case** 键名；环境变量使用 `PYSHELD_PORTAL_` + 大写键名。加载优先级从低到高：

```text
内置默认值 < JSON 配置文件 < PYSHELD_PORTAL_* 环境变量 < 命令行覆盖（--host / --port / --dev）
```

空的环境变量不会覆盖配置（`_env` 返回空字符串时跳过），要显式置空请写在 JSON 里。`config.example.json` 是合法 JSON，JSON 不支持注释；字段说明见下表。

把样例复制到 `/etc` 并收紧权限：

```bash
sudo install -o root -g pyshield -m 0640 \
  /opt/pyshield-portal/license-portal/config.example.json \
  /etc/pyshield-portal/config.json
sudo install -o root -g root -m 0600 /dev/null /etc/pyshield-portal/portal.env
sudo editor /etc/pyshield-portal/config.json
sudo editor /etc/pyshield-portal/portal.env
```

`config.example.json` 中的 `data_dir` 是 `/var/lib/pyshield-portal`，`site_url`、域名、微信、邮箱、SMTP 和 `admin_allow_ips` 都是占位值，必须替换。**不要把真实密码写进仓库里的样例文件。**

#### 配置项完整说明

| 环境变量 | JSON 键 | 默认值 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| `PYSHELD_PORTAL_DATA_DIR` | `data_dir` | `license-data`（相对工作目录） | 否 | 数据目录，存放数据库、`portal.secret`、`signing.key`、`signing.pass`。生产建议 `/var/lib/pyshield-portal`。 |
| `PYSHELD_PORTAL_HOST` | `host` | `127.0.0.1` | 否 | 监听地址。生产必须保持回环地址，由 Nginx 反代。 |
| `PYSHELD_PORTAL_PORT` | `port` | `8091` | 否 | 监听端口，范围 0-65535。 |
| `PYSHELD_PORTAL_SITE_URL` | `site_url` | 空 | 否 | 对外站点完整源，例如 `https://license.example.com`。用于 Origin/Referer 校验，必须与浏览器实际访问的协议和主机一致，不要带路径或末尾斜杠。 |
| `PYSHELD_PORTAL_SITE_NAME` | `site_name` | `PyShield 授权中心` | 否 | 页面标题和页脚显示的名称。 |
| `PYSHELD_PORTAL_CONTACT_WECHAT` | `contact_wechat` | 空 | 否 | 页面展示的客服微信。 |
| `PYSHELD_PORTAL_SUPPORT_EMAIL` | `support_email` | 空 | 否 | 支持邮箱，展示给用户。 |
| `PYSHELD_PORTAL_TRIAL_DAYS` | `trial_days` | `14` | 否 | 试用有效期天数，1-365。 |
| `PYSHELD_PORTAL_TRIAL_BUILDS` | `trial_builds` | `3` | 否 | 试用期内每月构建次数，1-10000。 |
| `PYSHELD_PORTAL_TRIAL_GRACE_DAYS` | `trial_grace_days` | `0` | 否 | 试用到期后的宽限天数，0-3650。 |
| `PYSHELD_PORTAL_EMAIL_VERIFICATION` | `email_verification` | `true` | 否 | 是否要求邮箱验证码。开启时生产环境必须配置 SMTP，否则试用申请会失败。 |
| `PYSHELD_PORTAL_RATE_LIMIT_PER_HOUR` | `rate_limit_per_hour` | `12` | 否 | 单 IP 每小时试用申请/激活码尝试上限。邮箱、手机号另有每小时 3 次，验证码校验每 IP 每小时 30 次的固定限制。 |
| `PYSHELD_PORTAL_SMTP_HOST` | `smtp_host` | 空 | 否 | SMTP 服务器。为空且非开发模式时邮件不可用。 |
| `PYSHELD_PORTAL_SMTP_PORT` | `smtp_port` | `587` | 否 | SMTP 端口，0-65535。465 通常配合 `ssl`，587 通常配合 `starttls`。 |
| `PYSHELD_PORTAL_SMTP_USER` | `smtp_user` | 空 | 否 | SMTP 登录用户。 |
| `PYSHELD_PORTAL_SMTP_PASSWORD` | `smtp_password` | 空 | **是** | SMTP 密码或授权码，只放在 `portal.env`（或通过 wrapper 从 systemd credential 导出到环境变量）中。 |
| `PYSHELD_PORTAL_SMTP_FROM` | `smtp_from` | 空（回退到 `smtp_user`） | 否 | 发件人地址，必须是 SMTP 服务允许的 From。 |
| `PYSHELD_PORTAL_SMTP_FROM_NAME` | `smtp_from_name` | 空（回退到 `site_name`） | 否 | 发件人显示名，支持中文；实际 `From` 为 `显示名 <smtp_from>`。 |
| `PYSHELD_PORTAL_SMTP_REPLY_TO` | `smtp_reply_to` | 空（回退到 `support_email`，再回退到 `smtp_from`） | 否 | 用户直接回复时使用的地址。 |
| `PYSHELD_PORTAL_SMTP_TLS` | `smtp_tls` | `starttls` | 否 | `starttls`、`ssl` 或 `none`。生产不要用 `none`。 |
| `PYSHELD_PORTAL_TURNSTILE_SITE_KEY` | `turnstile_site_key` | 空 | 否 | Cloudflare Turnstile 公开 Site Key；与 Secret 同时非空才启用。 |
| `PYSHELD_PORTAL_TURNSTILE_SECRET` | `turnstile_secret` | 空 | **是** | Turnstile Secret，只放在 `portal.env` 或受保护配置中。 |
| `PYSHELD_PORTAL_SECURE_COOKIES` | `secure_cookies` | `true` | 否 | 管理员/CSRF Cookie 是否带 `Secure`。生产必须为 true；`--dev` 会强制关闭。 |
| `PYSHELD_PORTAL_TRUST_PROXY` | `trust_proxy` | `false` | 否 | 是否信任 `X-Forwarded-For` 的第一个地址。只在 Nginx 覆盖该头且后端仅监听 127.0.0.1 时开启。 |
| `PYSHELD_PORTAL_SESSION_HOURS` | `session_hours` | `12` | 否 | 管理员会话有效小时数，1-720。 |
| `PYSHELD_PORTAL_ADMIN_ALLOW_IPS` | `admin_allow_ips` | 空 | 否 | 管理端 IP 白名单，逗号分隔的 IP 或 CIDR，例如 `203.0.113.10,10.8.0.0/24`。为空表示不限制来源（仍需登录）。 |
| `PYSHELD_PORTAL_SIGNING_PASSPHRASE` | `signing_passphrase` | 空 | **是** | 签发密钥口令。为空时读取 `signing.pass`；再不存在则随机生成。生产应通过 `portal.env` 或 systemd credential 提供，并删除 `signing.pass`。 |
| `PYSHELD_PORTAL_KEY_REDEMPTION_DAYS` | `key_redemption_days` | `30` | 否 | 激活码默认兑换期限（从生成日算起），1-3650；管理端生成时可单独覆盖。与激活后的授权天数 `days` 是两回事。 |
| 无（仅 JSON） | `max_body` | `65536` | 否 | 单个请求体上限（字节），1 KiB-16 MiB。当前代码没有为它映射环境变量，请写在 JSON 中；Nginx 侧另有 1 MiB 上限。 |
| `PYSHELD_PORTAL_DEV` | `dev` | `false` | 否 | 开发模式。生产不要开启；建议用 `--dev` 临时启动而不是写进配置。 |
| `PYSHELD_PORTAL_ADMIN_PASSWORD` | 无（仅 CLI） | 无 | **是** | `init`、`admin create`、`admin reset-password` 读取的管理员密码，至少 10 个字符。不要写进 `portal.env` 长期保存。 |

补充：

- `max_body` 目前只支持 JSON 配置，未映射 `PYSHELD_PORTAL_MAX_BODY` 环境变量。
- `admin_allow_ips` 在 JSON 中可以是数组，也可以是逗号分隔字符串；环境变量按逗号拆分。
- `trust_proxy=true` 时服务取 `X-Forwarded-For` 的第一个值。Nginx 样例使用 `$remote_addr` 覆盖该头，避免客户端伪造；不要改成 `$proxy_add_x_forwarded_for`。
- `SITE_URL` 不一致会导致带 Origin/Referer 的表单 POST 被拒绝（`origin_rejected`）。

生产 `portal.env` 示例（权限 0600，systemd 以 root 读取后再降权）：

```bash
PYSHELD_PORTAL_DATA_DIR=/var/lib/pyshield-portal
PYSHELD_PORTAL_SITE_URL=https://license.example.com
PYSHELD_PORTAL_TRUST_PROXY=1
PYSHELD_PORTAL_SECURE_COOKIES=1
PYSHELD_PORTAL_ADMIN_ALLOW_IPS=203.0.113.10,10.8.0.0/24

PYSHELD_PORTAL_SMTP_HOST=smtp.example.com
PYSHELD_PORTAL_SMTP_PORT=587
PYSHELD_PORTAL_SMTP_USER=portal@example.com
PYSHELD_PORTAL_SMTP_PASSWORD=替换为SMTP授权码
PYSHELD_PORTAL_SMTP_FROM=portal@example.com
PYSHELD_PORTAL_SMTP_FROM_NAME="PyShield 授权中心"
PYSHELD_PORTAL_SMTP_REPLY_TO=support@example.com
PYSHELD_PORTAL_SMTP_TLS=starttls

PYSHELD_PORTAL_TURNSTILE_SITE_KEY=替换为SiteKey
PYSHELD_PORTAL_TURNSTILE_SECRET=替换为Secret

PYSHELD_PORTAL_SIGNING_PASSPHRASE=替换为签发密钥口令
```

`EnvironmentFile` 使用 `KEY=value` 语法，不要在 `=` 两边加空格；包含空格或特殊字符时用双引号包起来。

#### 邮件发件与模板

验证码邮件和系统通知都使用内置的品牌 HTML 模板：深色页头、站点名称、石灰绿强调色、验证码卡片和页脚客服信息，并自动附带纯文本正文。模板不引用外部字体或图片，样式全部内联，Outlook/QQ 邮箱/移动端均可正常显示。

模板使用的站点字段：

| 字段 | 用途 |
| --- | --- |
| `site_name` | 页头品牌、邮件标题前缀、页脚版权。 |
| `site_url` | 页脚“访问授权中心”链接、正文 CTA 按钮和验证码复制页；只接受 `http`/`https`。 |
| `support_email` | 页脚支持邮箱，同时是 `Reply-To` 的首选回退。 |
| `contact_wechat` | 页脚客服微信（可选）。 |
| `smtp_from_name` | 发件人显示名，默认与 `site_name` 一致。 |

验证码旁边有“复制”按钮。邮件客户端会剥离脚本，不能在邮件内直接操作剪贴板，所以按钮打开站内复制页（形如 `/copy-code.html?minutes=10&site=…&support=…&wechat=…#验证码`）：验证码只放在 URL fragment 中，不发送到服务器，页面读取后立即从地址栏清除；浏览器允许时自动复制，否则显示“复制验证码”按钮。查询参数携带站点名、支持邮箱和客服微信作为回退值，复制页同时读取 `/api/public/config` 获取权威配置，展示当前站点名称、联系方式、授权中心地址、验证码有效期和安全提示；配置接口不可用时仍可复制，只是隐藏未提供的联系方式。直接选中验证码也可以复制。

常见服务商参数：

| 服务商 | 主机 | 端口 | TLS | 备注 |
| --- | --- | --- | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465 | `ssl` | 密码填“授权码”，不是 QQ 密码。 |
| 163 邮箱 | `smtp.163.com` | 465 | `ssl` | 密码填客户端授权码。 |
| 腾讯企业邮 | `smtp.exmail.qq.com` | 465 | `ssl` | `smtp_from` 必须是企业邮箱地址。 |
| 阿里云邮件推送 | `smtpdm.aliyun.com` | 465 | `ssl` | 发件地址需先在控制台验证。 |
| 自建 Postfix | 你的域名 | 587 | `starttls` | 确认证书有效且允许该发件人。 |

配置完成后先发一封测试邮件（会使用同一套品牌模板）：

```bash
sudo bash -c 'set -a; . /etc/pyshield-portal/portal.env; set +a; \
  PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
  /opt/pyshield-portal/.venv/bin/python -m license_portal mail test \
  --config /etc/pyshield-portal/config.json --to you@example.com'
```

成功时命令打印收件人和 SMTP 主机；SMTP 未配置、认证失败或收件人被拒都会以非零状态退出。管理端“概览 → 邮件服务”也会显示 SMTP 是否已配置、发件人与 Reply-To，但不会显示密码。

### 4.4 初始化与管理员（生产）

以 `pyshield` 用户初始化，数据目录必须可写；签发口令通过环境变量传入：

```bash
sudo -u pyshield env \
  PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
  PYSHELD_PORTAL_SIGNING_PASSPHRASE='与 portal.env 中一致的签发口令' \
  /opt/pyshield-portal/.venv/bin/python -m license_portal init \
  --config /etc/pyshield-portal/config.json
```

创建管理员：

```bash
sudo -u pyshield env \
  PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
  PYSHELD_PORTAL_SIGNING_PASSPHRASE='与 portal.env 中一致的签发口令' \
  PYSHELD_PORTAL_ADMIN_PASSWORD='至少10位的强密码' \
  /opt/pyshield-portal/.venv/bin/python -m license_portal admin create \
  --config /etc/pyshield-portal/config.json --username admin
```

如果初始化时没有传口令，会生成 `signing.pass`。生产切换方式见 4.9。

### 4.5 systemd

```bash
sudo install -o root -g root -m 0644 \
  /opt/pyshield-portal/license-portal/deploy/systemd/pyshield-license-portal.service \
  /etc/systemd/system/pyshield-license-portal.service
sudo systemctl daemon-reload
sudo systemctl enable --now pyshield-license-portal
sudo systemctl status pyshield-license-portal
sudo journalctl -u pyshield-license-portal -f
```

unit 已包含：

- `User=pyshield`、`WorkingDirectory=/opt/pyshield-portal`。
- `EnvironmentFile=-/etc/pyshield-portal/portal.env`（文件不存在也不阻止启动）。
- `ExecStart=/opt/pyshield-portal/.venv/bin/python -m license_portal run --config /etc/pyshield-portal/config.json`。
- `Restart=always`。
- `NoNewPrivileges=true`、`ProtectSystem=strict`、`ReadWritePaths=/var/lib/pyshield-portal` 等加固项。
- 可选 `LoadCredential` 传签发口令的注释示例。应用读取的是环境变量，因此 credential 方式需要按注释中的 `sh -c` 包装把 credential 文件内容导出为 `PYSHELD_PORTAL_SIGNING_PASSPHRASE`。

> `ProtectSystem=strict` 使 `/opt`、`/etc` 只读。若把 `data_dir` 改到别处，必须同步修改 `ReadWritePaths`。unit 中的 `Environment=PYTHONPATH=...` 保证 `-m license_portal` 和 `import pyshield` 都能找到包。

### 4.6 Nginx TLS 反向代理

服务只监听 `127.0.0.1:8091`，Nginx 终止 TLS：

```bash
sudo install -o root -g root -m 0644 \
  /opt/pyshield-portal/license-portal/deploy/nginx/pyshield-license-portal.conf \
  /etc/nginx/conf.d/pyshield-license-portal.conf
# Debian/Ubuntu 也可以放到 /etc/nginx/sites-available/ 并软链到 sites-enabled/
sudo editor /etc/nginx/conf.d/pyshield-license-portal.conf
sudo nginx -t
sudo systemctl reload nginx
```

样例按「静态文件 + API 反代」拆分：

- 80 端口 301 跳转到 443。
- 443 TLS、HSTS、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、CSP。
- `root /opt/pyshield-portal/license-portal/web/dist;`，`try_files $uri $uri/ /index.html;` 提供前端路由回退。
- `/assets/` 长缓存（Vite 文件名带 hash），`/index.html` 与 `/admin` 不缓存。
- `/api/` 反代到 `127.0.0.1:8091`，`proxy_set_header Host/X-Real-IP/X-Forwarded-For/X-Forwarded-Proto`。
- 可选的 `/api/admin/` IP 白名单 `allow ...; deny all;` 注释。

前端产物也可以不放在默认路径：设置 `PYSHELD_PORTAL_WEB_DIR`（或配置里的 `web_dir`）让后端自己托管，适合单进程演示。

应用自身也会发送 CSP、HSTS 等安全头；Nginx 再补一层。若不希望重复，可以只保留一侧。若使用 Cloudflare 等 CDN，必须在 Nginx 配置 `real_ip_header CF-Connecting-IP` 和 `set_real_ip_from` Cloudflare 网段，否则 `$remote_addr` 是 CDN 地址，`ADMIN_ALLOW_IPS` 会失效。

配置 `SITE_URL` 与 `server_name` 必须一致，例如 `https://license.example.com`。

### 4.7 防火墙与监听检查

只对外开放 80/443，8091 仅回环：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8091/tcp
sudo ss -lntp | grep 8091     # 应显示 127.0.0.1:8091，而不是 0.0.0.0:8091
curl -fsS https://license.example.com/health
```

`/health` 返回 `{"ok": true, "service": "pyshield-license-portal", ...}`。

### 4.8 管理员 IP 白名单与管理面隔离

- `ADMIN_ALLOW_IPS` 由应用检查：`/api/admin/*` 的所有请求（含登录 POST）都会校验；支持单个 IP 和 CIDR，不匹配返回 403 `ip_rejected`。
- 管理端页面本身是 Nginx 托管的静态文件，不做 IP 限制；所有数据都经过 `/api/admin/*`，因此白名单依然能挡住未授权来源。
- 建议仍在 Nginx（或 VPN/零信任网关）对 `/api/admin/` 做 `allow/deny`，样例已给出。两层同时启用后，登录请求也会在边缘被拦截。
- 建议至少保留两个入口：VPN/办公网 CIDR，以及一台固定的运维跳板机 IP。
- 更稳妥的做法是把管理端放到独立域名或只在内网 DNS 解析，外层再加 VPN/零信任网关。
- 白名单为空时任何来源都能访问管理端 API（仍需密码、会话与 CSRF）；生产不建议为空。
- 第一次上线如果还没有固定出口 IP，可先留空并确保 Nginx/VPN 已限制管理端，确认来源 IP 后再收紧。
- 管理员登录本身有 IP + 用户名双维度限流（15 分钟内 20 次 / 10 次）。

### 4.9 签发密钥

首次初始化或首次启动会生成：

- `signing.key`：Ed25519 私钥，使用 scrypt 派生的密钥加密后写盘（0600）。
- `signing.pass`：如果启动时没有提供 `SIGNING_PASSPHRASE`，自动生成随机口令并写入该文件（0600）。

生产建议：

1. 初始化时直接通过 `PYSHELD_PORTAL_SIGNING_PASSPHRASE` 提供强口令，或把现有 `signing.pass` 的内容复制到 `portal.env`。
2. 确认服务能正常启动、能签发试用/激活授权后，删除 `signing.pass`。
3. 口令通过 `/etc/pyshield-portal/portal.env`（0600）或 systemd `LoadCredential` 提供。**口令丢失后 `signing.key` 无法解密，只能轮换密钥并重建客户端。**

查看当前公钥和指纹：

```bash
sudo -u pyshield env \
  PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
  PYSHELD_PORTAL_SIGNING_PASSPHRASE='...' \
  /opt/pyshield-portal/.venv/bin/python -m license_portal signing show \
  --config /etc/pyshield-portal/config.json
```

管理端 `/admin/signing` 只显示 `key_id`、Base85 公钥、SHA-256 指纹和创建时间，不提供私钥导出。每份授权文档都带有 `signing_key_id` 字段，便于审计对账。

轮换：

```bash
sudo -u pyshield env \
  PYTHONPATH=/opt/pyshield-portal/license-portal:/opt/pyshield-portal \
  PYSHELD_PORTAL_SIGNING_PASSPHRASE='...' \
  /opt/pyshield-portal/.venv/bin/python -m license_portal signing rotate --yes \
  --config /etc/pyshield-portal/config.json
```

旧 `signing.key` 会备份为 `signing.key.<UTC时间戳>.bak`。轮换后：

- 新签发的文档只能被内置**新公钥**的客户端验证。
- 旧客户端无法在线领取续费/升级文档，也无法验证新授权；必须用新公钥重新构建并分发客户端。
- 客户端本地已有的旧授权在到期前仍可离线使用，但领取新文档的在线刷新会失败。

### 4.10 构建器注入公钥和站点

`tools/build_commercial.py` 的 `build` 子命令**同时支持** `--public-key` 和 `--site`（`prepare` 子命令也支持）。注入的是**公钥**，客户端拿不到签发能力。

从管理端「签发密钥」页复制 Base85 公钥，或从 `signing show` 输出中只复制 `public key:` 后面的字符串，保存为 `portal.pub`。注意不要把整段带标签的输出直接当公钥传入。

一步法（推荐，构建前顺带注入）：

```bash
python tools/build_commercial.py build \
  --backend nuitka \
  --public-key /path/to/portal.pub \
  --site https://license.example.com \
  --force-key
```

两步法（先注入，再打包）：

```bash
python tools/build_commercial.py prepare \
  --public-key /path/to/portal.pub \
  --site https://license.example.com \
  --force

python tools/build_commercial.py build --backend nuitka --no-clean
```

参数说明：

- `--public-key`：`portal.pub` 路径或 Base85 字符串，必须是 32 字节 Ed25519 公钥。
- `--site`：试用站点，必须与 `SITE_URL` 完全一致；只允许 `https://`，本地调试可用 `http://127.0.0.1` 或 `http://localhost`。
- `--force-key`（`build`）或 `--force`（`prepare`）：覆盖已存在的、内容不同的 `pyshield/_tool_key.py` / `pyshield/_site.py`；不加会在检测到不同公钥/站点时直接报错。
- `--backend`：`nuitka`（推荐，产物更难解包）或 `pyinstaller`。
- 生成的 `pyshield/_tool_key.py` 只包含 `PUBLIC_KEY_B85`，`pyshield/_site.py` 只包含 `DEFAULT_SITE`；两者都不应提交到版本库。

**绝对不能进入客户端或构建产物的内容**：

- `pyshield.license_admin` 生成的 `root.key`（根私钥）。
- 本服务的 `signing.key`、`signing.pass` 或 `PYSHELD_PORTAL_SIGNING_PASSPHRASE`。
- `portal.secret`、SMTP 密码、Turnstile Secret。
- 含真实密码的 `config.json` / `portal.env`。

注意：本门户使用 `signing.key` 签发，不是用 `root.key`。如果沿用旧的根密钥体系，必须把**门户实际签发公钥**注入构建器；注入 `root.pub` 而服务端用 `signing.key` 签发，客户端会验签失败。

### 4.11 上线检查清单

- [ ] `/health` 返回 200，服务日志显示监听 `127.0.0.1:8091`。
- [ ] `ss` 确认 8091 没有绑定到公网地址。
- [ ] 浏览器 HTTPS 打开首页无证书错误，HSTS/CSP 生效。
- [ ] 管理端登录成功，`ADMIN_ALLOW_IPS` 之外的 IP 返回 403。
- [ ] 从测试客户端完成一次试用申请，收到验证码并领取授权。
- [ ] 在管理端生成一个测试激活码，桌面端激活成功，客户端显示正确版本/到期日。
- [ ] 管理端续费后，桌面端「刷新授权状态」拿到 `revision+1` 的文档。
- [ ] 管理端吊销后，桌面端刷新状态被拒绝。
- [ ] `/api/license/revocations` 返回签名文档。
- [ ] 备份任务已运行，且做过一次恢复演练。

## 5. 业务流程

### 5.1 试用

```text
桌面 exe                        浏览器/授权门户                      管理端
   │ 打开 /activate?serial=&machine=   │                              │
   ├──────────────────────────────────►│ 表单预填序列号/机器码          │
   │                                   │ 用户填邮箱/手机/姓名/个人或公司 │
   │                                   │ 可选 Turnstile                 │
   │                                   ├─ POST /trial/apply ───────────►│
   │                                   │  创建 application(pending_email)│
   │                                   │  发送 6 位验证码                │
   │                                   ├─ POST /trial/verify ──────────►│
   │                                   │  校验验证码，签发试用授权       │
   │                                   │  显示试用码 + pyshield:// 深链  │
   │◄──────────────────────────────────┤                              │
   │ 轮询 /api/trial/status            │                              │
   ├──────────────────────────────────►│                              │
   │ 状态 granted 后 POST /api/trial/claim                              │
   ├──────────────────────────────────►│ 返回已签名 document            │
   │ 用内置公钥验签，写本地授权         │                              │
   ▼                                   │                              │
```

要点：

- 序列号和机器码从 URL 自动带入隐藏字段，用户不需要手抄。
- 同一序列号已绑定其他机器会拒绝；同一机器/同一邮箱或手机号已经申请过试用也会拒绝。
- 重复提交会把旧的 `pending_email` 申请标记为 `superseded`，并发送新验证码。
- 验证码 10 分钟有效，错误 5 次后必须重新提交申请。
- 页面显示 `PST-XXXX-XXXX-XXXX` 试用码；深链被浏览器拦截时，用户可以把试用码填回客户端。
- 在授权有效时，`claim` 是幂等的：同一序列号/机器码重复领取返回同一份授权（revision 相同）；已过期或已吊销则拒绝。
- 试用文档只含 `gui` 功能、1 个席位/激活、`trial_days` 天、`trial_builds` 次/月，客户端据此限制。

### 5.2 付费：手动微信收款后发码

```text
用户                         运营/管理员                         授权门户
 │ 打开 /upgrade?edition=pro     │                                   │
 ├──────────────────────────────►│ 看到微信/联系方式，人工沟通价格    │
 │ 微信转账                      │                                   │
 ├──────────────────────────────►│ /admin/orders 确认收款             │
 │                              │ /admin/keys/new 生成激活码         │
 │                              │ 可填数量、版本、授权天数、         │
 │                              │ 每月构建数、席位数、激活设备数、   │
 │                              │ 宽限天数、兑换期限、备注/批次      │
 │◄─────────────────────────────┤ 复制激活码发给用户                 │
 │ exe 点“我已获得激活码”输入     │                                   │
 ├──────────────────────────────────────────────────────────────────►│
 │                              │ POST /api/license/activate         │
 │                              │ {key, serial, machine}             │
 │◄──────────────────────────────────────────────────────────────────┤
 │ 验签、写本地授权              │ 返回签名 document                  │
 ▼                              │                                   │
```

激活码规则：

- 专业版前缀 `PSP`，企业版前缀 `PSE`，格式为 `PSP-XXXX-XXXX-XXXX-XXXX`（3 + 16 位 Crockford Base32）。
- 生成时 `days` 是激活后的授权天数（默认 365）；`redemption_days` 是激活码本身的有效兑换期限（默认 `key_redemption_days=30`）。兑换期限过后，再用该码发起激活会返回 `key_expired`；已经签发的授权仍按自身的到期日继续有效。
- `max_activations` 控制可激活设备数；在激活码未过期、未吊销时，同一 key + serial 重复激活是幂等的。
- 同一序列号已有其他来源的授权时会拒绝：已有其他 key、已有管理员签发授权、序列号绑定其他机器等。
- 激活码只存哈希（用于查找）和 `portal.secret` 派生的加密值（用于管理端再次查看）；查看动作会写审计日志。
- 用户也可以打开 `/redeem?serial=...&machine=...` 在网页上完成兑换，适合客户端无法直接联网激活时使用。

### 5.3 续费与升级

管理端授权详情页支持：

- **续费**：延长到期日、调整每月构建数/宽限天数；默认在当前到期日（未过期）基础上加 365 天。
- **升级**：切换到 `pro` / `enterprise`，同时可调整到期日、构建数、席位数、宽限天数；版本变化时 `license_id` 前缀会变化（例如 `TRIAL-...` → `PRO-...`）。
- 每次修改都会 `revision + 1` 并重新签发文档，记录 `license_renew` / `license_upgrade` 事件。

桌面端「刷新授权状态」调用：

```http
POST /api/license/status
Content-Type: application/json

{"serial":"PS-...","machine":"...","license_id":"PRO-PS-...","revision":2}
```

当服务端 `license_id` 不同，或客户端 `revision` 小于服务端 `revision` 时，响应会携带新的 `document`；客户端验签后替换本地授权。服务端返回的 `status` 可能是 `active`、`expired`、`revoked` 或 `unknown`。

### 5.4 吊销

- 吊销激活码：`POST /admin/keys/<id>/revoke`，同时把使用该 key 的授权置为 `revoked`。
- 吊销授权：`POST /admin/licenses/<serial>/revoke`，只影响该序列号。
- 吊销后：
  - `/api/license/status` 返回 `status=revoked`，并带 `reason`。
  - `/api/license/revocations` 返回重新签名的吊销列表（`revoked` 数组为 `license_id`）。
  - 客户端在线刷新时拒绝；导入吊销列表后也应拒绝。
- 离线客户端不会自动获知吊销，仍可能使用已有本地授权直到到期。需要更强约束时，应在客户端加入定期心跳、构建时内置吊销列表或缩短授权有效期。

## 6. HTTP 接口

公共 API 不需要登录，使用 JSON 请求体（`/api/trial/status` 也支持 GET 查询参数），依赖机器码绑定和按 IP 限流。生产必须通过 HTTPS。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` / `POST` | `/api/trial/status?serial=&machine=` | 客户端轮询试用/授权状态，返回 `unknown / granted / expired / rejected`。POST 时 JSON 体为 `{serial, machine}`。 |
| `POST` | `/api/trial/claim` | 用 `{serial, machine}` 领取已签名试用/授权文档，返回 `document`、`deep_link`、`trial_code`、`revision` 等。 |
| `POST` | `/api/license/activate` | 用 `{key, serial, machine}` 激活专业版/企业版激活码，返回已签名 `document`。 |
| `POST` | `/api/license/status` | 用 `{serial, machine, license_id?, revision?}` 查询状态；需要新文档时响应带 `document`。 |
| `GET` / `POST` | `/api/license/revocations` | 返回签名的吊销列表文档。 |
| `GET` | `/api/version` | 返回门户版本。 |
| `GET` | `/health` | 健康检查。 |

错误响应统一为：

```json
{"error": "可读错误信息", "code": "machine_mismatch"}
```

校验失败时额外带 `errors` 字段。常见 `code`：`validation_failed`、`machine_mismatch`、`license_not_found`、`license_expired`、`license_revoked`、`invalid_key`、`key_revoked`、`key_expired`、`activation_limit`、`serial_bound`、`other_key`、`admin_license`、`rate_limited`、`origin_rejected`、`not_found`。

示例：

```bash
# 轮询试用状态
curl -sS 'https://license.example.com/api/trial/status?serial=PS-0000-0000-0000&machine=0123456789abcdef0123456789abcdef'

# 刷新授权状态
curl -sS -X POST https://license.example.com/api/license/status \
  -H 'Content-Type: application/json' \
  -d '{"serial":"PS-0000-0000-0000","machine":"0123456789abcdef0123456789abcdef","license_id":"PRO-PS-0000-0000-0000","revision":2}'

# 吊销列表
curl -sS https://license.example.com/api/license/revocations
```

### 前台路由（React SPA）

Nginx 与内置静态服务都会把未知路径回退到 `web/dist/index.html`，因此这些路由可以直接访问、刷新和分享。

| 路由 | 说明 |
| --- | --- |
| `/` | 首页：版本、试用卖点、价格卡片、客服微信。 |
| `/activate?serial=&machine=&edition=` | 试用申请表；缺少 `serial`/`machine` 时提示从客户端打开，`edition=pro/enterprise` 会跳转到升级页。 |
| `/verify?application=` | 邮箱验证码页面（显示脱敏邮箱、10 分钟有效）。 |
| `/success?serial=&machine=` | 签发成功页：试用码、深链 `pyshield://activate?...`、授权摘要。 |
| `/upgrade?edition=pro\|enterprise&serial=` | 购买/升级意向，提交后写入 `orders`。 |
| `/redeem?serial=&machine=` | 网页兑换激活码。 |
| `/admin/login` | 管理员登录。 |
| `/admin` `/admin/licenses` `/admin/keys` `/admin/customers` `/admin/customers/:id` `/admin/orders` `/admin/audit` `/admin/signing` | 管理端页面。 |
| `/robots.txt` | 禁止抓取 `/admin`。 |

### 浏览器 JSON API

前端只通过以下 JSON 接口读写数据。所有请求同源，公共写操作校验 `Origin`/`Referer`，并受 Turnstile 与按 IP/邮箱/手机号限流保护。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/public/config` | 站点名、试用天数/配额、客服微信、Turnstile site key。 |
| `POST` | `/api/public/trial/apply` | 提交试用申请；`email_verification=false` 时直接返回授权。 |
| `POST` | `/api/public/trial/verify` | `{application_id, code}` 验证邮箱并签发。 |
| `POST` | `/api/public/trial/resend` | `{application_id}` 重发验证码；旧码立即失效，按 IP/申请限流。 |
| `GET` | `/api/public/trial/application?id=` | 验证码页面所需元信息（脱敏邮箱、状态、过期时间）。 |
| `GET` | `/api/public/license?serial=&machine=` | 成功页与详情页读取授权状态（不返回签名文档）。 |
| `POST` | `/api/public/upgrade` | 创建升级/续费意向。 |
| `POST` | `/api/public/redeem` | 网页兑换激活码。 |

管理端接口需要 `pyshield_portal_session` Cookie；所有写操作还必须带 `X-CSRF-Token`（登录接口返回的 `csrf_token`），并且受 `ADMIN_ALLOW_IPS` 限制。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/admin/login` | 登录，设置 HttpOnly 会话 Cookie，返回 `admin` 与 `csrf_token`。 |
| `GET` | `/api/admin/me` | 当前会话与 `csrf_token`（页面刷新后重新取）。 |
| `POST` | `/api/admin/logout` | 删除会话并清 Cookie。 |
| `GET` | `/api/admin/dashboard` | 概览指标、30 天内到期、签发公钥。 |
| `GET` | `/api/admin/licenses?q=&edition=&status=` | 授权列表。 |
| `GET` | `/api/admin/licenses/<serial>` | 授权详情、激活记录、最近事件。 |
| `POST` | `/api/admin/licenses/<serial>/renew` | 续费，`revision+1`。 |
| `POST` | `/api/admin/licenses/<serial>/upgrade` | 升级版本/席位，`revision+1`。 |
| `POST` | `/api/admin/licenses/<serial>/revoke` | 吊销授权。 |
| `GET` / `POST` | `/api/admin/keys` | 列表 / 批量生成激活码。 |
| `GET` | `/api/admin/keys/<id>` | 激活码详情与已激活设备。 |
| `POST` | `/api/admin/keys/<id>/reveal` | 解密显示完整激活码（写审计日志）。 |
| `POST` | `/api/admin/keys/<id>/revoke` | 吊销激活码及其授权。 |
| `GET` | `/api/admin/customers?q=` | 客户列表。 |
| `GET` | `/api/admin/customers/<id>` | 客户详情：资料、授权、订单与试用申请（不返回验证码哈希）。 |
| `GET` | `/api/admin/orders?status=` | 订单列表。 |
| `POST` | `/api/admin/orders/<id>/status` | 更新订单状态：`pending/paid/fulfilled/cancelled`。 |
| `GET` | `/api/admin/audit?action=&q=&limit=` | 审计事件。 |
| `GET` | `/api/admin/signing` | 签发公钥、指纹、构建注入命令。 |
| `GET` | `/api/admin/filters` | 界面筛选可用的版本/状态/动作枚举。 |

浏览器接口的错误格式与桌面端一致（`{"error","code","errors"}`）。`errors` 是字段级错误，前端会直接高亮对应输入框。

生成激活码的常用字段：`edition`（`pro`/`enterprise`）、`quantity`（1-200）、`days`（授权天数，默认 365）、`max_builds`（默认 100）、`seats`（默认 1）、`max_activations`（默认 1）、`grace_days`（默认 14）、`redemption_days`（兑换期限，默认 30）、`batch_name`、`notes`、`features`（逗号/分号分隔，留空用版本默认功能）。

## 7. 数据库

SQLite 文件为 `<data_dir>/portal.sqlite3`，WAL 模式，所有时间戳为 UTC ISO 字符串，日期为 UTC 日历日期。表结构由 `db.py` 在启动时自动创建。

| 表 | 用途 | 关键字段 |
| --- | --- | --- |
| `customers` | 客户主体 | `email`、`phone`（均唯一）、`name`、`org_type`（individual/company）、`company_name`、`consent_at`。 |
| `applications` | 试用申请 | `serial`、`machine`、`customer_id`、`status`（`pending_email`/`verified`/`superseded`）、`code_hash`、`code_expires`、`attempts`、`ip_hash`、`user_agent`。 |
| `key_batches` | 激活码批次 | `name`、`edition`、`quantity`、`created_by`、`notes`。 |
| `license_keys` | 激活码 | `key_prefix`、`key_hash`（唯一，查找用）、`key_encrypted`（可解密展示）、`edition`、`days`、`max_builds_per_month`、`seats`、`max_activations`、`grace_days`、`features`、`status`（`unused`/`active`/`revoked`）、`expires_at`（兑换期限）、`batch_id`、`created_by`、吊销信息。 |
| `licenses` | 已签发授权（每个序列号一条） | `serial`（唯一）、`machine`、`customer_id`、`license_id`（唯一）、`edition`、`features`、`seats`、`max_activations`、`max_builds_per_month`、`grace_days`、`bind_mode`、`fingerprints`、`issued`、`not_before`、`expires`、`revision`、`status`（`active`/`revoked`）、`display_code`、`document`（完整签名文档 JSON）、`source`（`trial`/`key`/`admin`）、`key_id`、吊销原因。 |
| `key_activations` | 激活码与设备的绑定 | `key_id`、`serial`、`machine`、`license_id`、`activated_at`；`(key_id, serial)` 唯一，用于限制 `max_activations`。 |
| `orders` | 购买/升级意向和人工处理 | `customer_id`、`serial`、`kind`、`edition`、`status`、`contact`、`notes`、`amount`、`handled_by`。 |
| `events` | 审计日志 | `at`、`actor_type`（public/client/admin/system）、`actor`、`action`、`serial`、`license_id`、`key_prefix`、`ip_hash`、`detail`。 |
| `admins` | 管理员 | `username`（唯一）、`password_hash`（scrypt）、`role`（`admin`/`viewer`）、`disabled`、`last_login_at`。 |
| `sessions` | 管理端会话 | `token_hash`（主键）、`admin_id`、`csrf_token`、`expires_at`、`ip_hash`、`user_agent`。 |
| `email_codes` | 邮箱验证码 | `application_id`、`email`、`code_hash`、`purpose`、`expires_at`、`attempts`、`consumed_at`。 |
| `rate_limits` | 滑动窗口计数 | `bucket`（动作 + 时间窗）、`window_start`、`count`。 |

注意：

- `portal.secret` 用于派生激活码哈希、激活码加密、IP 哈希、会话和 CSRF 密钥。**它和数据库必须成对备份**；丢失后已生成的未激活密钥无法再校验或展示，管理员会话也会全部失效。
- `signing.key` 不存数据库；它决定能否签发新文档，必须单独备份并与口令分开保管。
- 数据库中的 `document` 是历史签发快照；续费/升级后会被新文档覆盖。
- 客户邮箱/手机号属于个人信息，请按隐私政策最小化收集、限制访问并设置保留期限。

## 8. 备份与恢复

完整操作手册见 [`deploy/backup.md`](deploy/backup.md)。核心要求：

1. **SQLite 在线备份**用 `.backup`（或 `VACUUM INTO`），不要在服务运行时只复制主库文件：

   ```bash
   sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 \
     ".backup '/var/lib/pyshield-portal/portal-backup.sqlite3'"
   sudo sqlite3 /var/lib/pyshield-portal/portal-backup.sqlite3 'PRAGMA integrity_check;'
   ```

2. **WAL checkpoint** 只用于冷备份准备；在线备份不需要停止服务：

   ```bash
   sudo -u pyshield sqlite3 /var/lib/pyshield-portal/portal.sqlite3 'PRAGMA wal_checkpoint(TRUNCATE);'
   ```

   停机冷拷贝时必须同时复制 `portal.sqlite3`、`portal.sqlite3-wal`、`portal.sqlite3-shm`，或先 checkpoint 再复制主库。

3. **分开备份并加密**：数据库 + `portal.secret` 是一组；`signing.key` 与口令/`signing.pass` 分开存放；`config.json` 和 `portal.env` 单独加密。不要把口令和 `signing.key` 放进同一个未加密压缩包。

4. **恢复步骤**：停服务 → 恢复数据目录属主/权限（`pyshield:pyshield`，0700/0600）→ 恢复 `/etc` 配置和匹配的口令 → 删除旧的 `-wal`/`-shm`（如果恢复的是独立 `.backup` 文件）→ `PRAGMA integrity_check` → 启动 → 检查 `/health`、日志中的 `key_id`、`signing show` 指纹 → 做一次试用/激活/吊销回归。

5. **旧备份的代价**：备份之后签发的授权、激活记录、吊销操作会丢失。客户端手里已有的签名文档在到期前仍能离线验证，但服务端可能返回 `unknown`；吊销可能“复活”。恢复旧库后应重新执行备份点之后的吊销，并核对订单/支付记录。

## 9. 安全边界与加固建议

### 当前模型的边界

- **服务器被攻破 = 签发能力可能泄露**。签发私钥在进程内存中解密使用；`EnvironmentFile`/credential 只保护静态存储，不能防御 root 或内存读取。攻击者拿到 `signing.key` + 口令后可以伪造任意授权。
- `portal.secret` 泄露会导致激活码可解密、CSRF Cookie 可伪造；若攻击者同时能写数据库，还可伪造管理员会话。但攻击者仍需 `signing.key` 才能签发新文档。
- 数据库泄露会暴露客户邮箱/手机号、激活记录和加密后的激活码；密码是 scrypt 哈希，激活码是 ChaCha20 密文，但仍应按高敏感数据保护。
- 吊销依赖在线刷新或吊销列表分发，纯离线客户端在授权到期前不会自动失效。
- 管理端目前是用户名 + 密码 + IP 白名单，没有内置 TOTP/2FA。
- 试用防刷维度是序列号、机器码、邮箱、手机号和 IP 限流；删除本地安装状态会得到新序列号，仍可能被机器码/客户维度拦截，但不能杜绝所有重复申请。

### 建议的后续加固

- 把签发私钥迁移到 HSM/KMS 或独立签名机，门户只调用签名 API；至少做到签发服务与公网 Web 进程分机部署。
- 引入 `key_id`/证书链和多公钥信任，支持平滑轮换；客户端内置多个受信公钥。
- 管理端增加 TOTP 2FA、SSO、操作二次确认；继续保留 IP 白名单/VPN/独立域名。
- 对登录、激活、吊销、密钥查看设置告警；把 `events` 日志转发到独立审计系统，保留足够时间。
- Nginx/WAF 限制请求频率、屏蔽异常 UA/路径；只开放 80/443，8091 仅回环。
- 定期轮换 SMTP/Turnstile 凭据；备份加密密钥离线保存；每季度做恢复演练。
- 缩短高价值授权有效期，增加心跳/吊销列表内置，降低离线吊销窗口。
- 按当地法规处理个人信息：告知同意、最小化字段、访问控制、删除/导出流程。

## 10. 测试

```bash
export PYTHONPATH="$PWD/license-portal:$PWD"
python -m unittest discover -s license-portal/tests -v
python -m license_portal selftest
```

PowerShell：

```powershell
$env:PYTHONPATH = "$PWD\license-portal;$PWD"
python -m unittest discover -s license-portal/tests -v
python -m license_portal selftest
```

测试使用临时目录和内存/SQLite 临时库，不会修改生产数据。未运行服务也可以执行 `selftest` 和单元测试。

| 文件 | 覆盖范围 |
| --- | --- |
| `tests/test_service.py` | 试用、激活、续费、升级、吊销、审计等业务规则。 |
| `tests/test_validation.py` / `test_security.py` | 字段规范化、口令哈希、Cookie/CSRF。 |
| `tests/test_api.py` | `/api/public/*` 与 `/api/admin/*`：登录、CSRF、发码、兑换、订单、审计。 |
| `tests/test_mailer.py` | SMTP 发件人/Reply-To 回退、品牌 HTML 模板、纯文本正文、HTML 转义与非法 URL 过滤。 |
| `tests/test_server.py` | 桌面端 5 个接口、SPA 路由回退、静态资源缓存、缺少构建产物时的 503。 |

前端没有引入浏览器测试框架；构建前的 `npm run typecheck` 覆盖类型，关键流程用真实浏览器做过端到端验证（试用申请 → 邮箱验证 → 后台登录 → 批量发码 → 网页兑换 → 授权详情）。

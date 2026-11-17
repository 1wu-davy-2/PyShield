# PyShield 授权门户：Docker 部署

本文面向“本地打包源码 → 上传 Linux 服务器 → 服务器构建镜像”的部署方式。服务器不需要安装 Git、Node.js 或 Python；只需要 Docker Engine 与 Docker Compose v2。

镜像是一个多阶段构建：

1. Node 阶段执行 `npm ci && npm run build`，生成 React 管理端 `web/dist`；
2. runtime 阶段只保留 Python 标准库运行时、`license_portal`、共享的 `pyshield` 包和前端产物。

最终只有一个容器，前端静态文件和 API 都由同一个 Python 进程提供，也可以在前面放宿主机的 Nginx 终止 TLS。

## 1. 数据持久化设计

**镜像更新不会覆盖数据。** 数据库和所有门户状态都放在宿主机目录：

```text
/etc/pyshield-portal/
├── portal.sqlite3      # 客户、申请、授权、激活码、订单、审计、管理员
├── portal.sqlite3-wal  # SQLite WAL（运行时可能存在）
├── portal.sqlite3-shm
├── portal.secret       # 激活码哈希/加密、会话、CSRF 等服务端主密钥
├── signing.key         # 口令加密的 Ed25519 签发私钥
├── signing.pass        # 自动生成的签发口令（未设置环境变量时）
└── config.json         # 可选；如果存在，entrypoint 会自动传给 CLI
```

`compose.yaml` 使用 bind mount：

```yaml
- "${PYSHELD_PORTAL_HOST_DATA_DIR:-/etc/pyshield-portal}:/etc/pyshield-portal"
```

因此：

- 重建镜像、删除容器、`docker compose up -d` 都不会动宿主机上的 `/etc/pyshield-portal`；
- **不要只备份 `portal.sqlite3`**。`portal.secret`、`signing.key`、`signing.pass` 必须一起保存，否则已生成的激活码、会话或签发能力可能不可恢复；
- 不要执行 `docker compose down -v` 来“清理环境”。目前没有命名卷，但以后增加时 `-v` 会删除卷；
- `/etc/pyshield-portal` 建议只用于本服务，目录权限由 entrypoint 收紧为 `0700`，文件默认受 `umask 077` 保护。

> 这里按当前部署要求把 SQLite 放在 `/etc/pyshield-portal`。在传统 systemd 部署里更常见的是 `/var/lib`；两种方式不冲突，但不要把同一份目录同时交给两套服务使用。

## 2. 前置条件

服务器：

- Linux x86_64 或 arm64；
- Docker Engine 24+；
- Docker Compose v2（`docker compose version`）；
- 可访问 Docker Hub 和 npm registry，或已经配置镜像/代理；
- 建议由 Nginx 终止 TLS，门户端口只监听 `127.0.0.1`。

本地打包机：

- Windows 10/11 自带 `tar`，或使用 Git Bash；
- 能通过 `scp` / `sftp` 上传文件。

Docker 命令示例使用 `sudo docker compose`。如果当前用户已经在 `docker` 组，可以去掉 `sudo`；两种方式不要混用，否则 `.env` 和项目目录可能对不上。

## 3. 本地打包并上传（服务器无 Git）

### 3.1 Windows PowerShell

在仓库根目录执行：

```powershell
cd E:\down\CodeShield\PyShield
powershell -ExecutionPolicy Bypass `
  -File .\license-portal\docker\package-source.ps1 `
  -Destination .\pyshield-portal-src.tar.gz
```

生成 `pyshield-portal-src.tar.gz`，只包含构建需要的 `pyshield/`、`license-portal/` 和根目录的 `.dockerignore`。脚本会排除：

- `.git`、`node_modules`、`web/dist`、`build/`、`dist/`、临时演示数据；
- `pyshield/_tool_key.py`、`pyshield/_site.py`、`root.key`、`root.pub`；
- `license-portal/config.json`、`license-portal/docker/.env`、`license-data`。

即使误把敏感文件放进了目录，`.dockerignore` 也会阻止它们进入镜像。上传前仍建议检查压缩包：

```powershell
tar -tzf .\pyshield-portal-src.tar.gz | Select-String -Pattern "_tool_key|_site|\.env$|root\.key|root\.pub"
```

正常应没有输出。

### 3.2 Git Bash / Linux 打包机

```bash
cd /path/to/PyShield
bash license-portal/docker/package-source.sh ./pyshield-portal-src.tar.gz
```

### 3.3 上传

```bash
scp ./pyshield-portal-src.tar.gz deploy@your-server:/tmp/
```

如果使用自定义 SSH 端口或跳板机，按你的 SSH 配置调整。不要把 `.env`、`signing.key` 或 `portal.secret` 单独上传到临时目录。

## 4. 服务器首次部署

### 4.1 准备源码和数据目录

```bash
sudo install -d -m 0755 /opt/pyshield-portal
sudo tar -xzf /tmp/pyshield-portal-src.tar.gz -C /opt/pyshield-portal
sudo chown -R root:root /opt/pyshield-portal

# 目录可以先创建；entrypoint 会把它 chown 给容器内的非 root 用户。
sudo install -d -o root -g root -m 0700 /etc/pyshield-portal
```

解压后的目录结构应为：

```text
/opt/pyshield-portal/
├── .dockerignore
├── pyshield/
└── license-portal/
    ├── license_portal/
    ├── web/
    └── docker/
        ├── Dockerfile
        ├── compose.yaml
        ├── entrypoint.sh
        ├── .env.example
        └── README.md
```

### 4.2 配置 `.env`

```bash
cd /opt/pyshield-portal/license-portal/docker
sudo cp .env.example .env
sudo chmod 0600 .env
sudo editor .env
```

至少有四项必须按实际环境修改：

```dotenv
# 浏览器实际访问的 origin，必须包含协议，不能带路径
PYSHELD_PORTAL_SITE_URL=https://license.example.com

# 首次没有数据库时创建的初始管理员
PYSHELD_PORTAL_ADMIN_PASSWORD=admin123@

# 宿主机数据目录；不要指向源码目录
PYSHELD_PORTAL_HOST_DATA_DIR=/etc/pyshield-portal

# 只在 Nginx 反代且 8091 只绑定回环时为 true
PYSHELD_PORTAL_TRUST_PROXY=true
```

生产环境还应该配置：

- `PYSHELD_PORTAL_SMTP_*`，并把 `PYSHELD_PORTAL_EMAIL_VERIFICATION=true`；否则试用申请无法发验证码。只做本地验收时可以临时设为 `false`；
- `PYSHELD_PORTAL_ADMIN_ALLOW_IPS`，限制管理端来源；
- `PYSHELD_PORTAL_SIGNING_PASSPHRASE`（可选，见第 8 节）；
- `PYSHELD_PORTAL_CONTACT_WECHAT`、`PYSHELD_PORTAL_SUPPORT_EMAIL` 等站点信息。

`.env` 含 SMTP 密码和初始管理员密码，**不要提交到版本库，也不要发给无关人员**。

### 4.3 构建与启动

```bash
cd /opt/pyshield-portal/license-portal/docker
sudo docker compose build --pull
sudo docker compose up -d
sudo docker compose ps
```

首次构建需要下载 Node 依赖，耗时取决于网络。国内网络可以在 `.env` 中配置：

```dotenv
PYSHELD_PORTAL_NPM_REGISTRY=https://registry.npmmirror.com
```

Docker Hub 镜像加速属于 Docker daemon 配置，不在本项目内。

### 4.4 看首次初始化日志

```bash
sudo docker compose logs -f portal
```

首次启动会依次做：

1. 创建 `/etc/pyshield-portal` 下的 SQLite、`portal.secret`；
2. 生成 `signing.key` 和 `signing.pass`；
3. 创建管理员 `admin`；
4. 启动端口 `8091`。

日志中应能看到数据目录、签发密钥 ID、公钥和指纹。因为初始密码 `admin123@` 只有 9 位，entrypoint 会使用一次性的 `--allow-weak-password` 初始化参数；**正常的管理员创建/重置接口仍然要求至少 10 位**。

按 `Ctrl+C` 退出日志即可。

### 4.5 健康检查

```bash
curl -fsS http://127.0.0.1:8091/health
curl -fsS http://127.0.0.1:8091/api/version
sudo ss -lntp | grep 8091
```

`ss` 应看到 Docker 发布在 `127.0.0.1:8091`，而不是 `0.0.0.0:8091`。

### 4.6 登录并立即修改初始密码

首次登录：

```text
https://license.example.com/admin/login
用户名：admin
密码：admin123@
```

登录后请立刻重置为一个至少 10 位的强密码：

```bash
sudo docker compose exec -it --user pyshield portal \
  env -u PYSHELD_PORTAL_ADMIN_PASSWORD \
  python -m license_portal admin reset-password --username admin
```

`--user pyshield` 让维护命令使用与主进程相同的非 root 用户，避免 SQLite WAL/日志文件变成 root 所有。`env -u` 是为了避免容器配置里残留的初始密码被 CLI 自动读取，从而跳过交互式输入。重置后，编辑 `.env`，把 `PYSHELD_PORTAL_ADMIN_PASSWORD` 留空或删除，再执行：

```bash
sudo docker compose up -d
```

已有数据库不会被重新初始化，修改这个变量不会重置线上管理员密码。管理员密码只能通过上面的 `admin reset-password` 更新。

## 5. Nginx + HTTPS

推荐让 Nginx 在宿主机监听 443，门户容器只发布到 `127.0.0.1:8091`。仓库提供了代理版配置：

```text
/opt/pyshield-portal/license-portal/deploy/nginx/pyshield-license-portal-docker.conf
```

安装前替换 `license.example.com` 和证书路径：

```bash
sudo cp /opt/pyshield-portal/license-portal/deploy/nginx/pyshield-license-portal-docker.conf \
  /etc/nginx/sites-available/pyshield-license-portal.conf
sudo ln -sf /etc/nginx/sites-available/pyshield-license-portal.conf \
  /etc/nginx/sites-enabled/pyshield-license-portal.conf
# RHEL/Fedora 可能是 /etc/nginx/conf.d/
sudo nginx -t
sudo systemctl reload nginx
```

`.env` 中同步设置：

```dotenv
PYSHELD_PORTAL_SITE_URL=https://license.example.com
PYSHELD_PORTAL_TRUST_PROXY=true
```

然后重建/重启容器使环境变量生效：

```bash
sudo docker compose up -d
```

检查：

```bash
curl -fsS https://license.example.com/health
sudo ss -lntp | grep 8091   # 只应出现 127.0.0.1
```

防火墙只开放 80/443；8091 不要暴露到公网。若必须临时直连服务器 IP 验收，把 `PYSHELD_PORTAL_SITE_URL` 改成实际 `http://服务器IP:8091`，把 `PYSHELD_PORTAL_BIND_ADDR` 改为 `0.0.0.0`，同时设置 `PYSHELD_PORTAL_TRUST_PROXY=false` 和 `PYSHELD_PORTAL_SECURE_COOKIES=false`，验收完立即改回，且不要在这种模式下处理真实客户数据。

### 5.1 无域名 / 纯 HTTP（IP 访问）

没有域名时，可以让宿主机 Nginx 监听 80，反代到 `127.0.0.1:8192`。安装 `deploy/nginx/pyshield-license-portal-ip-http.conf`，并在 `.env` 中设置：

```dotenv
PYSHELD_PORTAL_SITE_URL=http://服务器公网IP
PYSHELD_PORTAL_BIND_ADDR=127.0.0.1
PYSHELD_PORTAL_HTTP_PORT=8192
PYSHELD_PORTAL_TRUST_PROXY=true
PYSHELD_PORTAL_SECURE_COOKIES=false
```

容器内的监听地址不用改：compose 已设置 `PYSHELD_PORTAL_HOST=0.0.0.0`、`PYSHELD_PORTAL_PORT=8091`；`BIND_ADDR`/`HTTP_PORT` 只控制宿主机发布端口。浏览器访问 `http://服务器公网IP`（80 可省略），不要在 `SITE_URL` 里写 `:8192`。

CentOS 安装代理：

```bash
sudo cp /opt/pyshield-portal/license-portal/deploy/nginx/pyshield-license-portal-ip-http.conf \
  /etc/nginx/conf.d/pyshield-license-portal.conf
# 如果已有 default.conf 占用 80，先停用它，避免请求落到默认站点
sudo mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.disabled 2>/dev/null || true
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --reload
```

注意：

- Nginx 只隐藏了内部端口，**不会隐藏服务器公网 IP**；要隐藏 IP 需要在更前面加 CDN 或隧道。
- 纯 HTTP 下密码和会话 Cookie 是明文传输，只适合私有验收；正式客户数据必须上 HTTPS。
- 不走 Nginx、直接访问时，才把 `BIND_ADDR` 改成 `0.0.0.0`、`TRUST_PROXY` 改成 `false`，并把 `SITE_URL` 写成 `http://服务器IP:8192`。

## 6. 更新镜像但不更新 SQLite

### 6.1 备份现有镜像标签

```bash
sudo docker tag pyshield-license-portal:latest pyshield-license-portal:previous
```

### 6.2 本地重新打包并上传

在本地仓库根目录重新执行第 3 节的打包命令，然后：

```bash
scp ./pyshield-portal-src.tar.gz deploy@your-server:/tmp/
```

### 6.3 服务器解压、重建、重启

```bash
sudo tar -xzf /tmp/pyshield-portal-src.tar.gz -C /opt/pyshield-portal
cd /opt/pyshield-portal/license-portal/docker
sudo docker compose build --pull
sudo docker compose up -d
sudo docker compose logs --tail=100 portal
```

`/etc/pyshield-portal` 不在镜像里，也不在源码包中，因此 SQLite、密钥和配置不会被覆盖。更新后核对：

- 日志中的 signing key id 与更新前一致；
- 管理端可以登录，授权/客户/审计记录仍然存在；
- `/health` 返回 200。

> `tar` 解压是“覆盖”而不是“镜像同步”。如果你删除了某个源码文件，旧文件可能还留在服务器目录里。需要完全干净时，把新包解压到新目录后从新目录构建，或先备份 `.env` 再重建源码目录；不要删除 `/etc/pyshield-portal`。

### 6.4 回滚

```bash
sudo env PYSHELD_PORTAL_IMAGE=pyshield-license-portal:previous \
  docker compose up -d --no-build
```

回滚只替换镜像，数据目录不动。回滚后再执行一次登录和 `/health` 检查。

## 7. 备份、恢复与迁移

### 7.1 停机备份整个数据目录

SQLite 使用 WAL。最稳妥的简单方式是先停容器，再打包整个目录；不要只复制 `portal.sqlite3` 一个文件。

```bash
cd /opt/pyshield-portal/license-portal/docker
sudo docker compose stop portal

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo tar -C /etc -czf "/root/pyshield-portal-$STAMP.tar.gz" pyshield-portal
sudo chmod 0600 "/root/pyshield-portal-$STAMP.tar.gz"

sudo docker compose start portal
```

检查备份内容：

```bash
sudo tar -tzf /root/pyshield-portal-$STAMP.tar.gz | grep -E \
  'portal\.sqlite3$|portal\.secret$|signing\.key$|signing\.pass$'
```

然后按现有安全要求加密并异地保存。`backup.md` 中的密钥分组原则同样适用：

- `portal.sqlite3` + `portal.secret` 必须来自同一时间点；
- `signing.key` 与签发口令分开保管；
- `.env` 和可选 `config.json` 单独加密。

### 7.2 恢复

```bash
cd /opt/pyshield-portal/license-portal/docker
sudo docker compose down

sudo mv /etc/pyshield-portal \
  "/etc/pyshield-portal.old-$(date -u +%Y%m%dT%H%M%SZ)"
sudo tar -C /etc -xzf /root/pyshield-portal-YYYYMMDDTHHMMSSZ.tar.gz
sudo docker compose up -d
```

entrypoint 会重新设置数据目录所有者。恢复后检查：

```bash
sudo docker compose logs --tail=100 portal
curl -fsS http://127.0.0.1:8091/health
```

确认 signing key id、管理员登录、授权数量和备份时间点一致，再删除 `.old-*`。

### 7.3 迁移到另一台服务器

需要同时迁移：

1. 源码包（或新服务器上重新上传同一版本源码）；
2. `/etc/pyshield-portal` 整个目录；
3. 原 `.env`（SMTP、站点、白名单等）。

迁移后先不要改签发密钥；客户端的验签公钥必须与 `signing.key` 对应。若只迁移 SQLite 而丢失 `signing.key`，服务虽然能启动，但无法继续签发可被现有客户端验证的新授权。

## 8. 签发密钥与客户端公钥

首次启动会生成门户自己的 `signing.key`，它与离线根密钥 `root.key` 不是同一个东西。打印当前签发公钥：

```bash
sudo docker compose exec --user pyshield portal python -m license_portal signing show
```

把输出中的 Base85 公钥注入桌面构建器：

```powershell
python tools/build_commercial.py build `
  --public-key <PUBLIC_KEY_B85> `
  --site https://license.example.com
```

`--site` 必须与 `PYSHELD_PORTAL_SITE_URL` 完全一致；客户端和服务端的公钥/站点不匹配时，激活会失败。

生产环境建议：

- 把 `PYSHELD_PORTAL_SIGNING_PASSPHRASE` 放进 `.env`（权限 0600）或 Docker secret，不长期依赖自动生成的 `signing.pass`；
- 备份口令和 `signing.key` 分开保存；
- 不要在服务器命令行历史中直接写口令；必要时用交互式输入或受保护的 secret 文件；
- 轮换签发密钥前先确认所有客户端都能重新构建/更新。

## 9. 配置速查

配置优先级：内置默认值 < `config.json` < `PYSHELD_PORTAL_*` 环境变量。`compose.yaml` 已经把常用变量显式传入容器；完整字段说明见 `../README.md` 第 4.3 节。

| 变量 | 默认/示例 | 说明 |
| --- | --- | --- |
| `PYSHELD_PORTAL_SITE_URL` | `https://license.example.com` | 浏览器实际 origin，必须与 Origin/Host 校验一致 |
| `PYSHELD_PORTAL_BIND_ADDR` | `127.0.0.1` | 宿主机发布地址；公网部署保持回环 |
| `PYSHELD_PORTAL_HTTP_PORT` | `8091` | 宿主机端口；改后 Nginx 也要改 |
| `PYSHELD_PORTAL_HOST_DATA_DIR` | `/etc/pyshield-portal` | 宿主机持久化目录 |
| `PYSHELD_PORTAL_ADMIN_USER` | `admin` | 首次初始化管理员用户名 |
| `PYSHELD_PORTAL_ADMIN_PASSWORD` | `admin123@` | **仅首次无数据库时使用**；尽快改为强密码并从 `.env` 移除 |
| `PYSHELD_PORTAL_EMAIL_VERIFICATION` | `true` | 试用发码开关；无 SMTP 时应临时设 `false` 或先配置 SMTP |
| `PYSHELD_PORTAL_SMTP_*` | 空 | SMTP 主机、端口、账号、授权码、发件人、TLS 模式 |
| `PYSHELD_PORTAL_TRUST_PROXY` | `true` | 仅当 8091 只绑定回环且由可信 Nginx 反代时开启 |
| `PYSHELD_PORTAL_ADMIN_ALLOW_IPS` | 空 | 管理端 IP/CIDR 白名单，逗号分隔；空表示不限制 |
| `PYSHELD_PORTAL_SIGNING_PASSPHRASE` | 空 | 签发私钥口令；空则使用生成的 `signing.pass` |
| `PYSHELD_PORTAL_SECURE_COOKIES` | 空 | 留空时由 `SITE_URL`/本地地址推导；公网纯 HTTP 必须显式设为 `false` |
| `PYSHELD_PORTAL_TRIAL_DAYS` / `_BUILDS` | `14` / `3` | 试用期和试用构建额度 |
| `PYSHELD_PORTAL_MAX_BODY` | `65536` | 请求体上限 |
| `PYSHELD_PORTAL_NPM_REGISTRY` | 空 | 可选，仅构建阶段使用 |

## 10. 安全注意事项

1. **默认管理员密码是弱密码**。`admin123@` 按需求保留为首次初始化值；上线前改成至少 10 位强密码，或首次启动前直接在 `.env` 里设置强密码。
2. `.env` 设为 `0600`，不要提交；SMTP 授权码、Turnstile Secret、签发口令和初始密码都不能进入镜像或日志。
3. 8091 只绑定 `127.0.0.1`；公网入口只开 Nginx 的 80/443。不要把 Docker 端口直接暴露在公网。
4. 如果直接发布 8091 到公网，必须把 `PYSHELD_PORTAL_TRUST_PROXY` 设为 `false`，否则客户端可以伪造 `X-Forwarded-For` 绕过管理端 IP 白名单。
5. 使用 HTTPS 后确认 Cookie 带 `Secure`；公网纯 HTTP 必须设 `SECURE_COOKIES=false`，且不要在这种模式下处理真实客户数据。
6. 定期重建镜像并更新基础镜像；`docker compose build --pull` 会拉取 Node/Python base image 的最新补丁。
7. 备份 `portal.sqlite3`、`portal.secret`、`signing.key`、`signing.pass` 和 `.env`，并定期做恢复演练。丢失 `signing.key` 会使现有客户端无法验证新授权。
8. 生产环境建议配置 `ADMIN_ALLOW_IPS`，并在 Nginx 层再限制 `/api/admin/`；管理端没有内置 TOTP。
9. RHEL/CentOS 开启 SELinux 时，bind mount 如果报权限错误，可在 `compose.yaml` 的 volume 末尾加 `:Z`，或按本机策略给 `/etc/pyshield-portal` 打容器标签。
10. 不要把 `root.key`、`pyshield/_tool_key.py`、`signing.key` 或任何真实密码放进源码包；打包脚本和 `.dockerignore` 已做排除，但提交前仍要检查。

## 11. 常见问题

### 登录后立刻跳回登录页

通常是 Cookie 没有写入：

- `PYSHELD_PORTAL_SITE_URL` 的协议与实际访问不一致；
- HTTPS 页面却把 `SECURE_COOKIES` 设成了 false，或 HTTP 页面却设成了 true；
- Nginx 没有转发 `Host`，但 `SITE_URL` 为空；
- 浏览器/代理缓存了旧 Cookie。

先确认浏览器地址、`SITE_URL` 和 Nginx `server_name` 完全一致。

### 写操作返回 `origin_rejected`

来源校验使用 `Origin` / `Referer`。把 `PYSHELD_PORTAL_SITE_URL` 改成浏览器地址栏的实际 origin（包含端口），重建容器后再试：

```bash
sudo docker compose up -d
```

### 管理端返回 `ip_rejected`

`PYSHELD_PORTAL_ADMIN_ALLOW_IPS` 不包含当前来源 IP，或 Nginx 没有正确传递 `X-Forwarded-For`，或 `TRUST_PROXY` 设置不对。直连容器时不要信任代理头。

### 试用申请提示邮件发送失败

`EMAIL_VERIFICATION=true` 但 `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` 未配置。配置后重启容器；本地验收可以临时设为 `false`。

### 权限错误 / 无法创建数据库

```bash
sudo ls -ld /etc/pyshield-portal
sudo docker compose logs --tail=100 portal
```

entrypoint 以 root 启动并 chown 数据目录；如果自行设置了 `user:` 或去掉了 root 入口，需要先手工：

```bash
sudo chown -R 10001:10001 /etc/pyshield-portal
```

### 容器 unhealthy

```bash
sudo docker compose logs --tail=200 portal
sudo docker compose exec --user pyshield portal python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/health').read())"
```

常见原因：`signing.pass` 与 `signing.key` 不匹配、数据目录不可写、端口被占用、`config.json` 不合法。

### 端口被占用 / 想换宿主机端口

修改 `.env`：

```dotenv
PYSHELD_PORTAL_HTTP_PORT=18091
```

同时修改 Nginx 的 `proxy_pass http://127.0.0.1:18091;`，然后：

```bash
sudo docker compose up -d
sudo systemctl reload nginx
```

容器内部仍使用 8091，不需要改 Dockerfile 或健康检查。

### 构建时 npm 下载失败

在 `.env` 设置 `PYSHELD_PORTAL_NPM_REGISTRY` 后重新构建：

```bash
sudo docker compose build --pull
```

如果 Docker Hub 也不可达，先在宿主机配置 registry mirror 或预拉取 `node:22-bookworm-slim`、`python:3.12-slim-bookworm`。

### 误删了容器 / 重建后数据还在吗

在。数据在宿主机 `/etc/pyshield-portal`，不在容器层。先确认挂载：

```bash
sudo docker compose config | grep -A5 volumes
sudo ls -la /etc/pyshield-portal
```

只要这个目录还在，重建容器不会丢数据。

## 12. 上线检查清单

- [ ] `docker compose config` 中 `SITE_URL`、卷路径、端口正确。
- [ ] `/etc/pyshield-portal` 存在且不是源码目录。
- [ ] 首次启动日志显示 signing key id/public key，管理员创建成功。
- [ ] `curl /health` 返回 200，`ss` 显示 8091 只绑定回环。
- [ ] 已用 HTTPS 登录管理端，并把 `admin123@` 改成至少 10 位强密码。
- [ ] `.env` 中的初始管理员密码已清空或替换，`.env` 权限为 0600。
- [ ] SMTP 已配置并收到一封测试邮件（或明确接受暂时关闭邮箱验证）。
- [ ] `ADMIN_ALLOW_IPS` 已按运维网络配置，白名单外访问返回 403。
- [ ] 门户公钥已注入桌面构建器，`--site` 与 `SITE_URL` 一致。
- [ ] 已完成一次试用申请、发码、领取、激活、续期、吊销的验收。
- [ ] 已做一次停机备份和恢复演练，确认 `portal.secret` 与数据库成对。
- [ ] 已记录更新、回滚和备份命令，并设置失败告警。

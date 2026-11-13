# 试用登记与授权回传

PyShield 的桌面构建器可以从任意渠道分发。程序首次启动时用机器指纹加随机盐派生一个稳定的序列号，用户把它带到官网申请免费试用，服务端登记并签发一份绑定本机的授权，桌面端自动领取后进入「试用版」。

这篇文档说明这条链路的实现、接口和部署方式。

## 1. 角色与数据流

```text
构建器（客户机）                     试用服务（你的服务器）
     │ 首次启动：machine → serial
     │
     │ ① 点击「申请 14 天试用」
     ├──────── /activate?serial=…&machine=… ────────► SQLite 记一条 pending
     │                                                   │
     │                                          ② 用户点「申请」→ 签发试用授权
     │                                                   │
     │ ◄──────── pyshield://activate?serial=… ───────────┘
     │ ③ 收到深链或轮询到 granted
     ├──────── /api/trial/claim ────────────────────► 返回已签名的授权文档
     │ ④ 验签 → 写入 tool-license.json
     ▼
  试用版（14 天 / 3 次构建）
```

关键点：**授权是服务端签发的 Ed25519 文档**，客户端只有公钥，因此试用额度和有效期无法在本地伪造。离线时客户端只能沿用已有的授权，无法自行生成。

## 2. 序列号

| 项目 | 说明 |
| --- | --- |
| 格式 | `PS-XXXX-XXXX-XXXX`，Crockford Base32（去掉 I/L/O/U） |
| 来源 | `sha256("pyshield-serial-v1|machine|salt")`，salt 首次随机生成 |
| 存储 | `%APPDATA%\PyShield\install.json`（Linux 为 `~/.pyshield/install.json`），0600 |
| 稳定性 | 同一台机器重装应用只要状态目录保留，序列号不变 |

序列号只是**索引**，不承担安全职责；真正的绑定信息是服务端记录的机器码。

## 3. 服务端

模块：`pyshield/trial_server.py`，仅依赖标准库 + SQLite。

```bash
python -m pyshield.license_admin init --out root-keys        # 只做一次，私钥离线保存
python -m pyshield.trial_server \
  --root-key root-keys/root.key \
  --data-dir trial-data \
  --host 0.0.0.0 --port 8090
```

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `PYSHELD_TRIAL_DAYS` | 14 | 试用天数 |
| `PYSHELD_TRIAL_BUILDS` | 3 | 试用期内的构建配额 |

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 首页，可手工输入序列号 |
| GET | `/activate?serial=&machine=&edition=` | 登记页，首次访问写入一条 `pending` |
| POST | `/trial/apply` | 表单提交，签发试用授权并跳转 `pyshield://` |
| POST | `/api/trial/apply` | 同上，返回 JSON（供脚本或其它客户端使用） |
| GET | `/api/trial/status?serial=&machine=` | 客户端轮询用，返回 `pending` / `granted` |
| POST | `/api/trial/claim` | 用序列号 + 机器码换取已签名的授权文档 |
| GET | `/health` | 健康检查 |

### 数据表

```sql
trials(serial PK, machine, edition, status, created_at, updated_at,
       granted_at, expires, max_builds, document, remote, user_agent)
events(id, at, serial, machine, action, remote, detail)
```

`events` 记录 `view` / `apply` / `grant` / `claim` / `contact`，既用于审计，也用于每 IP 每小时 12 次的申请限流。

### 约束

- 一个序列号只能在**一台**机器上申请；机器码不一致时拒绝。
- 同一序列号重复申请是幂等的，返回第一次签发的文档。
- 只有 `trial` 版本会自动签发；`pro` / `enterprise` 只记录意向并提示联系商务。

## 4. 客户端

模块：`pyshield/trial_license.py`，界面入口在「版本与试用」。

| 动作 | 实现 |
| --- | --- |
| 读/建序列号 | `install_state()` |
| 打开官网 | `handshake_url()` + `webbrowser.open()` |
| 轮询 | `fetch_status()` → `status=granted` |
| 领取 | `claim()` → `apply_document()` 验签并写入 `tool-license.json` |
| 深链 | `pyshield://activate?serial=…`，`gui/instance.py` 负责转给已运行实例 |

深链在 Windows 上由 `HKCU\Software\Classes\pyshield` 注册，**仅在打包后的 exe** 中写入（`sys.frozen`），源码运行不会改注册表。可用 `PYSHELD_SKIP_PROTOCOL=1` 关闭。

单实例：主进程监听 `127.0.0.1` 随机端口并把端口与令牌写进 `instance.json`；第二次启动（浏览器唤起）把链接转发过去后立刻退出，不会闪出第二个窗口。

## 5. 试用授权的内容

试用授权就是一份普通的 `pyshield-tool-license/1` 文档：

```json
{
  "edition": "trial",
  "features": ["gui"],
  "bind_mode": "fixed",
  "fingerprints": ["<机器码>"],
  "activation_required": false,
  "expires": "+14 天",
  "max_builds_per_month": 3
}
```

因此现成的机制直接生效：

- `tool_license.check()` 返回 `licensed`，但 `edition=trial`；
- `features` 只有 `gui`，`--runtime-native`、`issue`、`--ci` 都会被拒绝；
- `max_builds_per_month=3` 配合 `quota.json` 限制构建次数；
- 到期后 `active_on()` 失败，构建被拒绝，但已生成的产物不受影响。

界面会把 `edition=trial` 显示成「试用版」，在侧栏、顶部胶囊和构建页横幅三处提示剩余天数与次数。

## 6. 本地联调

```bash
# 1. 根密钥
python -m pyshield.license_admin init --out /tmp/root-keys

# 2. 起服务
python -m pyshield.trial_server --root-key /tmp/root-keys/root.key --data-dir /tmp/trial-data --port 8090

# 3. 让构建器信任这个根公钥（源码运行时）
export PYSHELD_TOOL_PUBLIC_KEY="$(cat /tmp/root-keys/root.pub)"
export PYSHELD_HOME=/tmp/pyshield-home
python -m gui
```

在「版本与试用」里把服务地址填成 `http://127.0.0.1:8090`，点「申请 14 天试用」即可走完整流程。

> 设置 `PYSHELD_DEV=1` 会强制进入开发模式（不限构建），此时试用状态不会生效，只用于调试。

## 7. 部署到腾讯云

最小可用形态：

1. 一台轻量应用服务器，开放 80/443；
2. 用 systemd 托管进程，`root.key` 放在仅 root 可读的目录（或后续换成 HSM/KMS）；
3. Nginx 反代并启用 HTTPS —— 客户端会拒绝非 localhost 的 `http://` 地址；
4. 备份 `trials.sqlite3`，它同时是激活台账和审计记录。

```ini
# /etc/systemd/system/pyshield-trial.service
[Unit]
Description=PyShield trial registration
After=network.target

[Service]
WorkingDirectory=/opt/pyshield
ExecStart=/opt/pyshield/.venv/bin/python -m pyshield.trial_server \
  --root-key /etc/pyshield/root.key --data-dir /var/lib/pyshield/trial --host 127.0.0.1 --port 8090
Restart=always
User=pyshield

[Install]
WantedBy=multi-user.target
```

```nginx
server {
  listen 443 ssl http2;
  server_name license.example.com;
  location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header X-Forwarded-For $remote_addr;
  }
}
```

拿到证书后，把构建器里的服务地址改成 `https://license.example.com` 即可。

## 8. 已知边界

- 换机会导致机器码变化，需要重新申请；这是试用防刷的代价。
- 删除 `install.json` 会得到新的序列号，服务端按机器码限制「一台机器一次试用」可以拦住大多数重复申请；如需更严格，可再加 IP / 设备指纹维度。
- 服务端目前是单进程 SQLite，适合早期客户量；并发上来后按 Phase 3 迁到 PostgreSQL。
- 深链依赖系统协议注册，某些浏览器会弹出确认框；页面同时保留「打开 PyShield 并激活」按钮和轮询兜底。

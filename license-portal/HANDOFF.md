# License Portal 交接文档（React 版）

## 0. 先看结论

| 层 | 状态 | 位置 |
| --- | --- | --- |
| 后端业务逻辑 | 已完成，未改动 | `license_portal/service.py`、`validation.py`、`db.py`、`signing.py` |
| 浏览器 JSON API | 已完成 | `license_portal/api.py`（`/api/public/*`、`/api/admin/*`） |
| 桌面端 5 个 API | 契约未变 | `license_portal/server.py` 的 `_client_api` |
| 前端 | **已改为 React SPA**，旧服务端渲染已删除 | `web/`（Vite + React 18 + TS + antd） |
| HTTP 托管 | 后端可托管 `web/dist`，生产由 Nginx 托管 | `server.py` 的 `_serve_spa` |
| 测试 | `license-portal/tests` 98 项通过 | 新增 `test_api.py`，重写 `test_server.py` |

**接手第一件事**：`cd license-portal/web && npm install && npm run build`，否则根路径返回 503（提示构建），`/api/*` 仍然可用。

已删除的文件（原服务端渲染层）：`pages.py`、`admin.py`、`ui.py`、`static/style.css`，以及 `server.py` 里所有表单 POST 路由（`/trial/apply`、`/trial/verify`、`/upgrade`、`/redeem`、`/admin/*` 的 HTML 分支）。仓库尚未提交，删除不可回滚；功能规格已完整迁移到 React 页面。

## 1. 目录

```text
license-portal/
├── license_portal/
│   ├── api.py               # 浏览器 JSON API：路由、序列化、CSRF 校验
│   ├── server.py            # HTTP 服务：桌面端 API + 浏览器 API + SPA 静态托管
│   ├── service.py           # 全部业务规则（不要重写）
│   ├── validation.py        # 字段规则（前端校验只是提示，服务端仍是权威）
│   ├── security.py          # scrypt / Cookie 签名 / CSRF / 字段加解密
│   ├── signing.py           # Ed25519 签发密钥
│   ├── db.py                # SQLite schema / WAL
│   ├── config.py            # JSON + PYSHELD_PORTAL_*；新增 web_dir
│   ├── mailer.py / captcha.py
├── web/                     # React 控制台
│   ├── src/api/client.ts    # fetch 封装、ApiError、CSRF 头、401 回调
│   ├── src/api/hooks.ts     # TanStack Query：所有 query/mutation
│   ├── src/api/types.ts     # 与后端响应对应的类型
│   ├── src/components/      # layout / ui / Turnstile / admin 小组件
│   ├── src/pages/public/    # Home Activate Verify Success Upgrade Redeem
│   ├── src/pages/admin/     # Login Dashboard Licenses LicenseDetail Keys
│   │                        # KeyNew KeyDetail Customers Orders Audit Signing
│   ├── src/lib/format.ts    # 版本/状态文案、日期、剩余天数、掩码
│   ├── src/lib/formErrors.ts# 把 errors:{field:msg} 映射回 antd 表单
│   ├── src/styles/app.css   # 设计 token + 全部布局（无 CSS 框架）
│   └── dist/                # 构建产物（被 .gitignore 的 dist*/ 忽略）
├── tests/                   # test_service / test_validation / test_security
│                            # test_api（新）/ test_server（重写）
├── deploy/nginx|systemd|backup.md
└── README.md                # 已按 React + JSON API 更新
```

## 2. 前端

### 2.1 技术栈

`vite 5` + `react 18` + `typescript 5` + `react-router-dom 6` + `@tanstack/react-query 5` + `antd 5` + `dayjs`。

**没有用 RHF + Zod**：表单全部是 antd `Form`。原因是 antd 的 `Form.Item` 已经提供字段级错误、`setFields` 回填服务端错误和受控输入，再叠一层 RHF 只会重复状态。字段规则写在 `rules` 里，服务端 `errors` 通过 `applyServerErrors(form, error.errors)` 回填到同一批字段。

### 2.2 路由

公共：`/`、`/activate`、`/verify`、`/success`、`/upgrade`、`/redeem`，未匹配走 `NotFound`。
管理端：`/admin/login` 独立页；`/admin` 下嵌套 `licenses`、`licenses/:serial`、`keys`、`keys/new`、`keys/:id`、`customers`、`orders`、`audit`、`signing`，未匹配 `Navigate to="/admin"`。

**路由必须与 Nginx / `_serve_spa` 的 history fallback 配合**：`LicenseDetail` 的地址是 `/admin/licenses/PS-XXXX-XXXX-XXXX`，刷新时要能回到 `index.html`。

### 2.3 数据层约定

- `client.ts` 的 `api.get/post` 自动带 `credentials: same-origin`；非 GET 请求自动带 `X-CSRF-Token`（由登录或 `/api/admin/me` 写入模块变量）。
- 失败统一抛 `ApiError { status, code, errors }`。`status===401` 会触发 `onUnauthorized`（只在 `AdminLayout` 注册，跳登录页）。
- `hooks.ts` 里每个 mutation 成功后只失效相关 key：
  - 授权写操作 → `license(serial)` + `['admin','licenses']` + `dashboard`
  - 发码 / 吊销码 → `key(id)` + `['admin','keys']` + `dashboard`
  - 订单 → `['admin','orders']` + `dashboard`
- `useAdminMe` 是鉴权入口，同时把 `csrf_token` 写回客户端；`AdminLayout` 用它决定渲染、跳转或加载态。

### 2.4 设计系统

`app.css` 顶部是 token：`--ink #0b0f18`、`--paper #f6f7fa`、`--brand #2b5ce6`、`--lime #c6f24e`、`--ok/--warn/--danger`。字体是 Inter/Segoe UI + PingFang 栈，序列号/密钥/指纹一律 `.mono`。

- 公共站点：浅色纸面 + 深色 hero 面板 + 石灰绿强调；`/` 用 `.hero` + `.doc-card`（授权文档示意图）。
- 管理端：深色侧栏 + 浅色内容区；指标卡 `.kpi`（首张 `.kpi.accent` 为深色反白），表格 `.ps-table` 统一表头样式。
- 状态用 `.status-tag.ok/warn/danger/info/neutral`，版本用 `.edition-chip`，两者都由 `StatusTag` / `EditionChip` 渲染。
- 断点：1080 / 820 / 640。640 以下隐藏副标题和次要导航，只留主 CTA；`.doc-card` 在该断点去掉偏移装饰避免溢出。

改样式优先改 token 和已有类，不要在页面里写内联色值。

### 2.5 表单与服务端错误

服务端校验失败返回 `400 {code:"validation_failed", errors:{email:"邮箱格式不正确"}}`。页面统一这样处理：

```ts
if (error instanceof ApiError) {
  applyServerErrors(form, error.errors)   // 字段级红字
  setFormError(error.message)             // 顶部 Alert
}
```

Turnstile 在 `config.captcha_site_key` 为空时不渲染（本地/测试环境默认关闭）。提交失败后会 `resetKey++` 重建控件。

## 3. 后端

### 3.1 `server.py` 的三种职责

1. **桌面端契约**（`_client_api`，不要改）：`GET|POST /api/trial/status`、`POST /api/trial/claim`、`POST /api/license/activate`、`POST /api/license/status`、`GET|POST /api/license/revocations`。
2. **浏览器 API**（`_browser_api` → `api.handle`）：`/api/public/*`、`/api/admin/*`。POST 前校验 `Origin/Referer`；`/api/admin/*` 先校验 `ADMIN_ALLOW_IPS`；会话 Cookie 由 `security.session_cookie_name()` 读取，写操作比对 `X-CSRF-Token` 与 `session["csrf"]`。
3. **静态托管**（`_serve_spa`）：命中 `web/dist` 下的真实文件就按扩展名返回（`/assets/` 长缓存），否则回退 `index.html`；带扩展名但不存在返回 404；`dist` 不存在返回 503 + 构建提示。

### 3.2 `api.py`

- `ApiRequest` / `ApiResponse` 两个 dataclass；`ApiResponse.session_token` 用于登录下 Cookie，`clear_session` 用于登出。
- 公共接口的返回值会经过 `_public_license`：**签名文档 `document` 永远不下发给浏览器**，只给 `trial_code` 与 `deep_link`。
- 列表接口返回 `{ok, items, total}`；详情接口返回 `{ok, license|key}`。
- 列表里的授权会经过 `_slim_license` 去掉 `document`/`fingerprints` 等大字段。
- `GET /api/admin/filters` 给前端提供版本/状态/动作枚举，避免前端硬编码和数据库不一致。

## 4. 不可破坏的约束

1. **桌面端 5 个 API 的路径与请求/响应字段不能改**；`pyshield/trial_license.py` 与 `gui/backend.py` 依赖它们。
2. **深链格式**：`pyshield://activate?serial=...&code=...`，由 `service._deep_link` 生成；前端只在 `edition === "trial"` 时展示按钮并自动跳转 —— 正式授权的 `display_code` 是激活码前缀，不能当试用码用。
3. **签发公钥必须与构建器注入的公钥同源**；管理端只显示公钥/指纹，私钥不出服务器。
4. **管理端写操作必须同时满足**：有效会话 Cookie + `X-CSRF-Token` + IP 白名单；所有写操作写 `events` 审计。
5. **激活码明文只在创建时批量返回一次**；数据库存 HMAC 哈希 + 加密副本，之后只能单个 `reveal`（写审计）。
6. 不要在前端用 `dangerouslySetInnerHTML`；所有用户输入走 React 转义。

## 5. 运行 / 测试 / 构建

```bash
# 后端测试（98 项）
cd PyShield
python -m unittest discover -s license-portal/tests -v

# 桌面端授权客户端相关（65 项）
python -m unittest tests.test_license_portal_client tests.test_trial_license tests.test_gui tests.test_tool_license

# 前端
cd license-portal/web
npm install            # 首次
npm run typecheck      # tsc --noEmit
npm run build          # tsc + vite build -> web/dist
npm run dev            # 5173，/api 代理到 8091

# 启动（开发）
cd license-portal
export PYTHONPATH="$PWD:$PWD/.."     # PowerShell 用分号
python -m license_portal init
PYSHELD_PORTAL_ADMIN_PASSWORD=password12345 python -m license_portal admin create --username admin
python -m license_portal run --dev --port 8091
```

PowerShell 对应写法与完整环境变量见 `README.md` 第 3 节。

## 6. 部署

- `npm run build` 产物 `web/dist` 交给 Nginx（`deploy/nginx/pyshield-license-portal.conf` 已改成「静态 root + `/api/` 反代」）。
- 单进程演示可以让 Python 自己托管：`PYSHELD_PORTAL_WEB_DIR=/path/to/web/dist`。
- CSP 同时写在 `license_portal/security.py`（API 响应）和 Nginx（HTML 响应）：`script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com`，`frame-src` 同理。Vite 产物不需要额外域名；如果以后把字体/图片放到 CDN，要同步改 CSP。

## 7. 已验证 / 未验证

已验证：

- 后端 98 项 unittest；桌面端授权客户端 65 项。
- 浏览器端到端（Playwright + Chromium，真实页面操作）：首页渲染 → 填试用表单 → 提交得到 `PST-` → 管理端登录 → 生成 2 个激活码 → 网页兑换 → 授权详情 + 续费弹窗。
- 桌面端 API 冒烟：`/api/trial/status`、`/api/trial/claim`、`/api/license/activate`、`/api/license/status`、`/api/license/revocations`。
- 移动端 390px：首页、试用表单不横向溢出。

未验证 / 需要注意：

- 没有接入真实 Cloudflare Turnstile（本地 site key 为空）。上线前用测试密钥走一遍，重点看 CSP 是否拦截 `challenges.cloudflare.com` 的 iframe。
- 没有做真实 SMTP 发信测试（测试用 `MemoryMailer`）。
- 前端没有单元测试/组件测试框架；回归靠 `npm run typecheck` + 上面那条 E2E 脚本（未入库，可要求重写）。
- 前端首包 1.2MB（gzip 385KB），antd 占大头。若在意公共页 LCP，可把 `/admin/*` 改成 `React.lazy` 分包；目前未做。

## 8. 已知未做 & 建议下一步

1. **在线支付**：仍是人工微信收款 + 管理端发码。接支付时要新增 `payments` 表、回调验签和幂等处理，订单状态由回调推进。
2. **短信验证码 / 管理端 TOTP 2FA**：`validate_login` 只校验用户名密码；2FA 需要给 `admins` 加 `totp_secret` 与登录中间态。
3. **重新发送验证码**：现在只能回到申请表重新提交（旧申请会被 `superseded`）。要做的话建议 `POST /api/public/trial/resend`，复用 `email_codes` 和限流。
4. **PostgreSQL / 多实例**：`service.py` 直接用了 `sqlite3` 事务，抽象成 repository 才能换库；SQLite 单机在早期客户量下够用。
5. **授权文档轮换**：轮换签名密钥会让所有旧客户端失效，需要配套的客户端更新流程。
6. **审计导出 / 客户详情页**：目前客户只有列表，点不进详情；审计只能按动作/关键字过滤。

## 9. 优先阅读的文件

1. `license_portal/api.py` —— 浏览器 API 的完整行为。
2. `license_portal/server.py` —— 三种职责、Cookie/CSRF、SPA 托管。
3. `web/src/api/hooks.ts` + `web/src/api/client.ts` —— 前端数据流与错误模型。
4. `web/src/styles/app.css` —— 设计 token 与布局类。
5. `web/src/pages/public/Activate.tsx` —— 表单 + Turnstile + 服务端错误回填的范例。
6. `web/src/pages/admin/LicenseDetail.tsx` —— 续费/升级/吊销三个弹窗的范例。
7. `license-portal/tests/test_api.py` —— 接口行为的可执行规格。
8. `license_portal/service.py` —— 业务规则（只读，不要重写）。

# PyShield 开发者交接说明

面向接手本项目的开发者。版本 0.6.0，分支 `develop`。读这一份 + `docs/implementation.md`（引擎设计）即可上手。

---

## 1. 一句话与现状

PyShield 把 Django 项目的 Python 源码加密成 stub，带许可证、设备绑定与签名清单，通过桌面构建器（pywebview）一键产出可部署副本。它提高的是源码逆向与泄露成本，不是离线不可破解的 DRM；边界见 `docs/security-hardening.md`。

当前状态：

- 引擎（加密/清单/许可证/运行时）稳定，159 个单测全绿（1 个按环境跳过）；
- 试用链路完整：exe 首启生成序列号 → 官网申请 → 14 天/3 次授权回传 → 自动激活；
- 桌面端与官网页面已按 Grok 原型（`HA7HqyYHFsn4CepB-grok-workspace/`，UI 参考，不参与构建）统一视觉；
- CI（GitHub Actions，`.github/workflows/build-develop.yml`）产出 `ci-develop-latest` 快照 release。

## 2. 快速上手

```bash
git clone https://github.com/1wu-davy-2/PyShield
cd PyShield

# 源码运行桌面构建器（无公钥构建会自动起本地模拟授权站）
python -m gui

# 全量测试（约 45s）
python -m unittest discover -s tests

# 一键演示试用链路（根密钥/SQLite/服务端全在 .pyshield-demo/）
python tools/trial_demo.py --gui            # 源码版应用
python tools/trial_demo.py --exhaust --gui  # 直接看“试用已结束”弹窗
python tools/trial_demo.py --reset          # 清空演示数据

# 界面预览（不需要 pywebview，浏览器直接打开）
python -m http.server 8130 --directory gui/web
# http://127.0.0.1:8130/index.html?preview=1&view=trial
# 可用参数：view=workspace|build|history|trial|settings|about，state=unlicensed|exhausted，panel=python|confirm
```

环境：Python 3.12+；桌面端额外需要 `pywebview`（Windows 走 `pythonnet` + WebView2）。生产引擎零第三方依赖（`cryptography` 可选加速）。

## 3. 模块地图

| 路径 | 职责 | 备注 |
| --- | --- | --- |
| `shield.py` | CLI：build/issue/verify/inspect/fingerprint/selftest | 部署侧也用它续签许可证 |
| `pyshield/builder.py` | 模块发现、密文与 stub、runtime 安装、清单 | 进程内构建，进度回调驱动 GUI |
| `pyshield/crypto.py` / `ed25519.py` | 密码套件与签名 | 纯 Python 兜底 |
| `pyshield/licensing.py` / `manifest.py` | 产物许可证与签名清单 | |
| `pyshield/fingerprint.py` | 机器指纹 | |
| `pyshield/tool_license.py` | **构建器自身授权**：验签、配额、`trial_policy()`、错误码 `code`、账本 `begin_build`/`refund_build` | 试用/专业/企业共用这一层 |
| `pyshield/build_spec.py` | 构建前置校验基元：路径/reparse、输出名、到期/绑定/pattern | 纯 stdlib，GUI 与 MCP 共用 |
| `pyshield/build_service/` | MCP 构建服务：严格预检、异步 job、额度结算、退款、日志/审计 | 默认 `no_verify` |
| `pyshield/mcp_server/` | stdio JSON-RPC 协议层、严格 schema、资源与提示词；`http_transport.py` 提供回环 Streamable HTTP | 无运行时第三方依赖 |
| `gui/mcp_host.py` | 桌面端内置 MCP 主机：策略构造、令牌文件、启停、配置片段 | 与 backend 的 `mcp_*` API 配套 |
| `shield_mcp.py` / `PyShieldMCP.spec` | MCP console 入口与 PyInstaller 配置 | 含冻结原生编译隐藏入口 |
| `pyshield/license_admin.py` | 离线根密钥与授权签发 CLI | 私钥永不进构建器 |
| `pyshield/trial_server.py` | 试用登记官网（SQLite + HTML + API） | 部署到服务器，见 `docs/trial.md` |
| `pyshield/trial_license.py` | 客户端序列号/深链/领取 | |
| `pyshield/devsite.py` | 无公钥构建自动起本地模拟站 | keyless 构建 = 开发构建 |
| `pyshield/python_runtime.py` | 校验解释器一键下载 | 镜像版本探测 + 嵌入包 + pip/Django |
| `gui/` | 桌面端（backend 桥接 + web 界面） | 见 `docs/desktop-builder.md` |
| `gui/tray.py` | Windows 托盘与任务栏行为 | NotifyIcon、关闭隐藏、`WS_MINIMIZEBOX` |
| `gui/web/` | 前端契约：224 个 `#id`、`.segment`、`.nav-item`、`.view` | 改视觉别改契约 |
| `tools/` | build_commercial、trial_demo、make_icon、mcp_smoke | |
| `tests/` | unittest 测试套件 | 新功能必须带测试；MCP 测试会启动 stdio 子进程 |

## 4. 关键设计决定（别踩）

1. **发布版必须注入根公钥与站点**：`python tools/build_commercial.py build --public-key root.pub --site https://你的域名`。生成 `pyshield/_tool_key.py` 与 `pyshield/_site.py`（均已 gitignore）。没注入的构建视为开发构建：自动起本地模拟授权站、可无授权跑流程。
2. **没有公钥 ≠ 开发模式**：`check()` 的判定顺序是 `PYSHELD_DEV=1` → development；无公钥+有本地授权 → 未验签加载；无公钥+无授权 → `unlicensed`（首屏进版本页）。只有显式 `PYSHELD_DEV=1` 才不限构建。
3. **试用策略只有一份**：`tool_license.trial_policy()` / `trial_build_problems()`，CLI 与 GUI 共用；试用比专业版更严格（仅 ChaCha20-pure、无 full 清单、无客户水印、3 次/14 天）。
4. **错误靠 `code` 不靠文案**：`quota_exhausted` / `trial_expired` / `not_activated` / `machine_mismatch`，GUI 按 code 决定弹窗与跳转。
5. **构建页是两个视图**：工作区（全宽配置 + 底部操作条）→ 确认弹窗（摘要 + 额度提示）→ 构建日志页（进度+日志+结果卡）。`app.js` 的 `startBuild()` 只调 `check_build` 做无副作用校验并弹窗；`confirmStartBuild()` 才调 `start_build`，由后端在 `BuildManager.start()` 里记录配额。起始额度在确认时扣除，失败/取消不退还。元素 id（`#progressBlock`、`#terminal` 等）原样沿用，交互契约没变。
6. **深链被浏览器拦是常态**：`pyshield://` 跳转需要用户手势；授权到账后 `Api._raise_window()` 自动置顶 + 轮询兜底（2.5s 一次）。
7. **解释器下载只装到用户目录**：`%APPDATA%\PyShield\runtimes\`，`PYSHELD_RUNTIME_DIR` 可重定向；镜像只显示真正带 Windows 嵌入包的补丁版本（仅源码发布的安全补丁会被 HEAD 探测跳过）。
8. **关闭是隐藏到托盘，不是退出**：`gui/tray.py` 给 frameless 窗口补回缺失的 `WS_MINIMIZEBOX`（否则点任务栏按钮不会最小化/还原），并用 WinForms `NotifyIcon` 接管 `FormClosing`：✕ / Alt+F4 隐藏窗口，托盘双击还原，右键「退出 PyShield」才真正结束；构建中拒绝退出。Linux 无托盘，关闭仍直接退出。第二次启动会先尝试通过 LinkServer 的 `show` 消息唤起已隐藏实例；全局命名互斥锁保证即使配置目录不同也不会新开窗口。
9. **MCP 是构建侧接口，但不再是第二个 exe**：`pyshield/mcp_server/` 用 stdlib 实现 newline-delimited JSON-RPC，`http_transport.py` 把它复用到回环 Streamable HTTP；`PyShieldBuilder.exe` 同时提供 GUI、`--mcp` stdio 与 `127.0.0.1:8790/mcp`。`pyshield/build_service/` 负责严格预检、异步 job、额度账本和退款。MCP 构建默认 `no_verify`，不执行目标项目代码；`pyshield_check_output` 才在沙箱执行 `manage.py check`。只有 `PROJECT_CODE` 失败退款；预检失败不扣额；secret 写到独立 `--secret-dir`，不返回给客户端。GUI 的额度语义仍是“启动即扣、失败不退”，与 MCP 不同，Phase 3 再统一。
10. **单 exe 双入口靠 console + hide_console**：`PyShieldBuilder.spec` 用 `console=True` 保证被客户端拉起时 stdin/stdout 可用，同时 `hide_console="hide-early"` 让双击时不出现黑框；不要再改回 `console=False`，那会让冻结进程的 `sys.stdout` 变成 `None`，stdio MCP 直接不可用。

## 5. 测试与 CI

- 本地：`python -m unittest discover -s tests`（数量以实际输出为准，部分原生/符号链接用例按环境跳过）；
- CI job：Unit ×2（ubuntu/windows）、Trial registration flow（真起进程）、Windows/Linux builder、MCP console ×2（构建 + `--selftest` + stdio 冒烟）、Protected Django demo（`PYSHELD_DEV=1`，新门禁会拦自检构建，别删这个 env）、Publish CI snapshot；
- 产物：release tag `ci-develop-latest`，包含 `PyShieldBuilder.exe`、`PyShieldMCP.exe` 和 Linux 对应 binary。
- MCP 冒烟：`python tools/mcp_smoke.py --server module` 或指向 `dist/PyShieldMCP.exe`。

## 6. 发布清单

1. 版本号：**保持 0.6.0，等 owner 明确说升再升**（`pyshield/__init__.py`、`gui/web` 关于页、spec）。
2. 打正式包：`python tools/build_commercial.py build --public-key root-keys/root.pub --site https://license.你的域名`；MCP 另打 `python -m PyInstaller --noconfirm --clean PyShieldMCP.spec`，并对产物运行 `python tools/mcp_smoke.py --server dist/PyShieldMCP.exe`；
3. 私有根密钥离线保管，绝不提交；
4. 官网部署：`python -m pyshield.trial_server --root-key ... --data-dir ...`，Nginx + HTTPS 样例在 `docs/trial.md` 第 7 节；
5. 未签名 exe 首次运行会被 SmartScreen 拦（预期），下载页注明 SHA256。

## 7. 未完成 / 待办

| 事项 | 状态 | 说明 |
| --- | --- | --- |
| 支付/发卡 | **暂缓** | owner 是个人开发者暂无收款能力；接入后给 `/trial/apply?edition=pro` 加订单表+回调，签发 `edition=pro` |
| 版本号升级 | 等 owner 通知 | 试用流程验收后统一升 |
| 代码签名 | 未做 | OV/EV 证书需营业执照；短期靠“解除锁定”+哈希说明 |
| 安装包 | **已规划，未实现** | 选择安装路径、桌面快捷方式、安装目录内卸载程序；方案见 `docs/installer-plan.md` |
| `docs/java-shield-research.md`、`spike/` | 未入库 | 另一条产品线（Java 版调研），owner 尚未决定是否提交 |
| Grok 原型工程 | 已入库（仅参考） | `HA7HqyYHFsn4CepB-grok-workspace/`，React+Tailwind 演示；`.vercel/output` 构建产物已 gitignore |

## 8. 协作约定

- 提交习惯：日常改动**本地提交**，owner 说“推”再 `git push origin develop`（会触发 CI 与 release 更新）；
- 提交信息用中文、`feat/fix/docs/style/ci:` 前缀；
- 界面改动先跑 `?preview=1` 截图核对（Edge headless 即可），保持 `gui/web` 的 DOM 契约；
- 密钥/`.pyshield-demo/`/`.workbuddy/` 一律不提交。

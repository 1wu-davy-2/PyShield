# PyShield 开发者交接说明

面向接手本项目的开发者。版本 0.6.0，分支 `develop`。读这一份 + `docs/implementation.md`（引擎设计）即可上手。

---

## 1. 一句话与现状

PyShield 把 Django 项目的 Python 源码加密成不可读的 stub，带许可证、设备绑定与签名清单，通过桌面构建器（pywebview）一键产出可部署副本。

当前状态：

- 引擎（加密/清单/许可证/运行时）稳定，145 个单测全绿；
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
# 可用参数：view=workspace|build|history|trial|settings|about，state=unlicensed|exhausted，panel=python
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
| `pyshield/tool_license.py` | **构建器自身授权**：验签、配额、`trial_policy()`、错误码 `code` | 试用/专业/企业共用这一层 |
| `pyshield/license_admin.py` | 离线根密钥与授权签发 CLI | 私钥永不进构建器 |
| `pyshield/trial_server.py` | 试用登记官网（SQLite + HTML + API） | 部署到服务器，见 `docs/trial.md` |
| `pyshield/trial_license.py` | 客户端序列号/深链/领取 | |
| `pyshield/devsite.py` | 无公钥构建自动起本地模拟站 | keyless 构建 = 开发构建 |
| `pyshield/python_runtime.py` | 校验解释器一键下载 | 镜像版本探测 + 嵌入包 + pip/Django |
| `gui/` | 桌面端（backend 桥接 + web 界面） | 见 `docs/desktop-builder.md` |
| `gui/web/` | 前端契约：144 个 `#id`、`.segment`、`.nav-item`、`.view` | 改视觉别改契约 |
| `tools/` | build_commercial、trial_demo、make_icon | |
| `tests/` | 145 个测试 | 新功能必须带测试 |

## 4. 关键设计决定（别踩）

1. **发布版必须注入根公钥与站点**：`python tools/build_commercial.py build --public-key root.pub --site https://你的域名`。生成 `pyshield/_tool_key.py` 与 `pyshield/_site.py`（均已 gitignore）。没注入的构建视为开发构建：自动起本地模拟授权站、可无授权跑流程。
2. **没有公钥 ≠ 开发模式**：`check()` 的判定顺序是 `PYSHELD_DEV=1` → development；无公钥+有本地授权 → 未验签加载；无公钥+无授权 → `unlicensed`（首屏进版本页）。只有显式 `PYSHELD_DEV=1` 才不限构建。
3. **试用策略只有一份**：`tool_license.trial_policy()` / `trial_build_problems()`，CLI 与 GUI 共用；试用比专业版更严格（仅 ChaCha20-pure、无 full 清单、无客户水印、3 次/14 天）。
4. **错误靠 `code` 不靠文案**：`quota_exhausted` / `trial_expired` / `not_activated` / `machine_mismatch`，GUI 按 code 决定弹窗与跳转。
5. **构建页是两个视图**：工作区（配置+摘要+开始加密）→ 构建日志页（进度+日志+结果卡）。跳转逻辑在 `app.js` 的 `startBuild()`，元素 id（`#progressBlock`、`#terminal` 等）原样搬过去的，交互契约没变。
6. **深链被浏览器拦是常态**：`pyshield://` 跳转需要用户手势；授权到账后 `Api._raise_window()` 自动置顶 + 轮询兜底（2.5s 一次）。
7. **解释器下载只装到用户目录**：`%APPDATA%\PyShield\runtimes\`，`PYSHELD_RUNTIME_DIR` 可重定向；镜像只显示真正带 Windows 嵌入包的补丁版本（仅源码发布的安全补丁会被 HEAD 探测跳过）。

## 5. 测试与 CI

- 本地：`python -m unittest discover -s tests`（145 个，3 个 skip）；
- CI 七个 job：Unit ×2（ubuntu/windows）、Trial registration flow（真起进程）、Protected Django demo（`PYSHELD_DEV=1`，新门禁会拦自检构建，别删这个 env）、Windows/Linux builder、Publish CI snapshot；
- 产物：release tag `ci-develop-latest`（exe 约 16MB）。

## 6. 发布清单

1. 版本号：**保持 0.6.0，等 owner 明确说升再升**（`pyshield/__init__.py`、`gui/web` 关于页、spec）。
2. 打正式包：`python tools/build_commercial.py build --public-key root-keys/root.pub --site https://license.你的域名`；
3. 私有根密钥离线保管，绝不提交；
4. 官网部署：`python -m pyshield.trial_server --root-key ... --data-dir ...`，Nginx + HTTPS 样例在 `docs/trial.md` 第 7 节；
5. 未签名 exe 首次运行会被 SmartScreen 拦（预期），下载页注明 SHA256。

## 7. 未完成 / 待办

| 事项 | 状态 | 说明 |
| --- | --- | --- |
| 支付/发卡 | **暂缓** | owner 是个人开发者暂无收款能力；接入后给 `/trial/apply?edition=pro` 加订单表+回调，签发 `edition=pro` |
| 版本号升级 | 等 owner 通知 | 试用流程验收后统一升 |
| 代码签名 | 未做 | OV/EV 证书需营业执照；短期靠“解除锁定”+哈希说明 |
| `docs/java-shield-research.md`、`spike/` | 未入库 | 另一条产品线（Java 版调研），owner 尚未决定是否提交 |
| Grok 原型工程 | 已入库（仅参考） | `HA7HqyYHFsn4CepB-grok-workspace/`，React+Tailwind 演示；`.vercel/output` 构建产物已 gitignore |

## 8. 协作约定

- 提交习惯：日常改动**本地提交**，owner 说“推”再 `git push origin develop`（会触发 CI 与 release 更新）；
- 提交信息用中文、`feat/fix/docs/style/ci:` 前缀；
- 界面改动先跑 `?preview=1` 截图核对（Edge headless 即可），保持 `gui/web` 的 DOM 契约；
- 密钥/`.pyshield-demo/`/`.workbuddy/` 一律不提交。

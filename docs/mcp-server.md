# PyShield MCP 构建服务

PyShield 只发布 **一个** 可执行文件：`PyShieldBuilder.exe`。MCP 构建服务内嵌在这个 exe 里，没有第二个产物：

| 入口 | 传输 | 适用 |
| --- | --- | --- |
| 打开构建器 → 自动监听 `127.0.0.1:8790/mcp` | Streamable HTTP | Claude Code / Cursor 等走 HTTP 的客户端 |
| `PyShieldBuilder.exe --mcp ...` | stdio | 客户端自己拉起子进程，不依赖端口 |
| `python -m pyshield.mcp_server ...` | stdio | 源码开发与 CI 调试 |

三种入口共用同一个 `build_service`、同一套工具 schema、同一份工具授权与额度账本。它只提供构建侧能力，不暴露根私钥、许可证签发、任意文件读写或远程网络服务。

## 桌面端内置服务

`PyShieldBuilder.exe` 同时提供 GUI 与 MCP：启动后用 `http://127.0.0.1:<端口>/mcp` 提供 Streamable HTTP 子集。关闭窗口只是隐藏到托盘，服务继续运行；真正退出应用才释放端口。

- 侧边栏「AI 接入」页可以直接开关服务、改端口、选 agent 可访问的项目目录、轮换令牌，并复制现成的客户端配置。
- 默认端口 `8790`；被占用时在 `8000-8999` 里随机挑一个空闲端口，页面显示实际端口，配置片段同步更新。
- 只绑定 `127.0.0.1`；除 `/health` 外的请求必须带 `Authorization: Bearer <令牌>` 或 `X-PyShield-Token`。令牌保存在 `%APPDATA%\PyShieldBuilder\mcp-token.txt`（0600）。
- 支持的 HTTP 方法：`POST /mcp`（单条、批量、通知）、`DELETE /mcp`（结束会话）、`GET /health`；`GET /mcp` 返回 405，因为服务不主动推送消息。
- `initialize` 响应会带 `Mcp-Session-Id`；会话只是提示，服务端不要求回传，重启后客户端可继续用新会话。

HTTP 客户端配置（页面可直接复制）：

```json
{
  "mcpServers": {
    "pyshield": {
      "type": "http",
      "url": "http://127.0.0.1:8790/mcp",
      "headers": { "Authorization": "Bearer <令牌>" }
    }
  }
}
```

stdio 方式（由客户端拉起同一个 exe）：

```powershell
PyShieldBuilder.exe --mcp --project-root E:\work --allow-build
```

内置模式的策略由页面设置决定：`allow_build` 关掉后只暴露只读工具；`allow_project_execution` 在内置模式下不开放。

## 快速开始

打包产物（单 exe）：

```powershell
PyShieldBuilder.exe --mcp ^
  --project-root E:\work ^
  --output-root E:\pyshield-output ^
  --state-dir E:\pyshield-state ^
  --allow-build
```

源码方式（开发/CI）：

```powershell
python -m pyshield.mcp_server ^
  --project-root E:\work ^
  --state-dir E:\pyshield-state ^
  --allow-build
```

默认只注册只读工具；`--allow-build` 才会注册 `pyshield_start_build` / `pyshield_cancel_build`。`--allow-project-execution` 才会注册 `pyshield_check_output`，并且必须同时配置 `--verify-python`。

### 客户端配置

```powershell
claude mcp add pyshield -- "E:\path\PyShieldBuilder.exe" --mcp ^
  --project-root E:\work ^
  --state-dir E:\pyshield-state ^
  --allow-build
```

```json
{
  "mcpServers": {
    "pyshield": {
      "command": "E:\\path\\PyShieldBuilder.exe",
      "args": [
        "--mcp",
        "--project-root", "E:\\work",
        "--state-dir", "E:\\pyshield-state",
        "--allow-build"
      ]
    }
  }
}
```

## 安全边界

- stdio 入口（`--mcp`）不监听端口、不发起网络连接；HTTP 入口只绑定 `127.0.0.1`，默认要求令牌，可在页面关闭，不会对外网开放。
- `project` 只接受 `--project-root` 内的相对路径；`output_name` 只是 `--output-root` 下的单段目录名，不接受绝对输出路径。内置模式下项目根、输出根、secret/job/audit 根由页面固定，互相不可包含。
- 项目树中的 symlink、Windows junction 和其他 reparse point 会被拒绝。
- 输出目录必须不存在；MCP 不暴露 `force`，不会删除已有目录。
- 构建密钥写到 `--secret-dir`（默认 `<state-dir>/secrets`）下的私有目录，**不在输出目录同级**；MCP 响应和日志永远不返回密钥内容。
- 默认构建固定 `no_verify`，不会执行目标项目代码。`pyshield_check_output` 会把产物复制到临时沙箱，用白名单解释器和最小环境运行 `manage.py check`，这是唯一执行目标代码的入口。
- 输出根和 secret 根必须与项目根、job/audit 根互不包含；建议把 `--output-root` 放在 agent 工作区之外，避免 agent 通过普通文件工具读取构建密钥。

## 工具

| 工具 | 说明 |
| --- | --- |
| `pyshield_status` | 服务器配置、工具授权、配额 |
| `pyshield_inspect_project` | 项目结构、模块数、文件大小、敏感文件、语法问题；不执行代码、不扣额 |
| `pyshield_validate_build` | 严格预检并返回 `plan_hash`；不写输出、不扣额 |
| `pyshield_start_build` | 校验通过后扣 1 次额度并异步构建；返回 `job_id` |
| `pyshield_build_status` | 状态、阶段、进度、失败分类、退款、摘要 |
| `pyshield_build_logs` | 脱敏后的构建日志尾部 |
| `pyshield_cancel_build` | 请求取消构建；取消属于非代码错误，不退款 |
| `pyshield_verify_output` | 对本次构建产物做静态 manifest/发布卫生校验；不执行代码 |
| `pyshield_check_output` | 沙箱执行 `manage.py check`；需显式开启 |

推荐调用顺序：

```text
pyshield_status
  -> pyshield_inspect_project
  -> pyshield_validate_build        # 保存 plan_hash
  -> pyshield_start_build           # confirm=true + expect_plan_hash
  -> pyshield_build_status          # 轮询直到 success/failed/cancelled
  -> pyshield_build_logs / pyshield_verify_output
```

## 额度与退款

- `inspect`、`validate`、`status`、`logs`、静态 `verify` 不扣额度。
- `pyshield_start_build` 在严格参数/路径/授权/并发校验通过后，原子扣 1 次额度，然后开始执行。
- 执行阶段只有 **目标项目代码错误**（`PROJECT_CODE`）才退还 1 次额度，并生成：
  - `<state-dir>/jobs/<job_id>/build.log`
  - `<state-dir>/jobs/<job_id>/refund.json`
  - `<state-dir>/jobs/<job_id>/refund.txt`
- 环境、工具链、权限、磁盘、取消、超时和内部错误不退还额度。
- 预检失败不扣额度。静态语法检查在扣额前完成，因此大多数用户代码语法错误根本不会消耗额度。
- 同一个 `idempotency_key` 或同一 token 不会重复扣额。
- 跨月退款会以 credit 形式保留，下个月优先消费 credit。

## 日志与审计

- 每次构建：`<state-dir>/jobs/<job_id>/`，包含 `request.json`、`preflight.json`、`build.log`、`job.json`、成功时的 `result.json`、退款时的 `refund.json` / `refund.txt`。
- 审计：`<state-dir>/audit/mcp.jsonl`，记录工具调用、扣/退额度和结果；不含密钥。
- 构建日志超过 16 MiB 会截断；返回给客户端的尾部最多 100 行 / 16 KiB。

## 协议

服务使用换行分隔的 JSON-RPC 2.0，支持 MCP 协议版本 `2025-06-18` 和 `2024-11-05`。标准输出只承载协议帧；诊断信息写入 stderr。支持 `initialize`、`tools/list`、`tools/call`、`resources/list|read`、`prompts/list|get`、`ping`、`shutdown` 和取消通知。HTTP 传输复用同一套分派逻辑（`JsonRpcServer.handle()`），因此两种形态的工具与校验完全一致。

## 打包

```powershell
python -m PyInstaller --noconfirm --clean PyShieldBuilder.spec
```

只有一个产物 `dist/PyShieldBuilder.exe`。`PyShieldBuilder.spec` 把 `pyshield.mcp_server` 与 `build_service` 打进 PYZ，并用 `console=True` + `hide_console="hide-early"` 构建——双击只出窗口，被客户端以 `--mcp` 拉起时仍有可用的 stdin/stdout。原生 runtime 构建所需的 Cython/setuptools 会随包带上，但仍需要本机 C 编译器和 strip 工具。

## 常见问题

- **`configuration error: ... overlaps ...`**：项目根、输出根、secret/job/audit 根不能互相包含。把输出和状态目录放到项目外。
- **`BUILD_DISABLED`**：启动时没有加 `--allow-build`。
- **`EXECUTION_DISABLED`**：没有加 `--allow-project-execution`。
- **`INTERPRETER_UNKNOWN`**：`interpreter_id` 不在 `--verify-python` 白名单中。
- **`OUTPUT_EXISTS`**：`output_name` 已存在；MCP 不覆盖目录，请换一个名字。
- **`SENSITIVE_FILE`**：项目里有 `.env`、私钥等敏感文件；移除它们，或在明确接受风险时加 `--allow-sensitive-files`。

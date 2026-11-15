# PyShield MCP 构建服务

`PyShieldMCP.exe`（或源码方式 `python -m pyshield.mcp_server`）是一个本地 stdio MCP 服务，把 PyShield 的构建能力暴露给 Claude Code、Cursor、CI agent 等客户端。它只提供构建侧能力，不暴露根私钥、许可证签发、任意文件读写或网络服务。

## 快速开始

源码方式：

```powershell
python -m pyshield.mcp_server `
  --project-root E:\work `
  --output-root E:\pyshield-output `
  --state-dir E:\pyshield-state `
  --allow-build
```

打包产物：

```powershell
PyShieldMCP.exe `
  --project-root E:\work `
  --output-root E:\pyshield-output `
  --state-dir E:\pyshield-state `
  --allow-build
```

默认只注册只读工具；`--allow-build` 才会注册 `pyshield_start_build` / `pyshield_cancel_build`。`--allow-project-execution` 才会注册 `pyshield_check_output`，并且必须同时配置 `--verify-python`。

### Claude Code 配置

```powershell
claude mcp add pyshield -- "E:\path\PyShieldMCP.exe" `
  --project-root E:\work `
  --output-root E:\pyshield-output `
  --state-dir E:\pyshield-state `
  --allow-build
```

### 通用 JSON 配置

```json
{
  "mcpServers": {
    "pyshield": {
      "command": "E:\\path\\PyShieldMCP.exe",
      "args": [
        "--project-root", "E:\\work",
        "--output-root", "E:\\pyshield-output",
        "--state-dir", "E:\\pyshield-state",
        "--allow-build"
      ]
    }
  }
}
```

## 安全边界

- 只使用 stdio，不监听端口，不发起网络连接。
- `project` 只接受 `--project-root` 内的相对路径；`output_name` 只是 `--output-root` 下的单段目录名，不接受绝对输出路径。
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

服务使用换行分隔的 JSON-RPC 2.0，支持 MCP 协议版本 `2025-06-18` 和 `2024-11-05`。标准输出只承载协议帧；诊断信息写入 stderr。支持 `initialize`、`tools/list`、`tools/call`、`resources/list|read`、`prompts/list|get`、`ping`、`shutdown` 和取消通知。

## 打包

```powershell
python -m PyInstaller --noconfirm --clean PyShieldMCP.spec
```

产物是 console 单文件 `dist/PyShieldMCP.exe`。它不依赖 GUI/webview；原生 runtime 构建所需的 Cython/setuptools 会随包带上，但仍需要本机 C 编译器和 strip 工具。

## 常见问题

- **`configuration error: ... overlaps ...`**：项目根、输出根、secret/job/audit 根不能互相包含。把输出和状态目录放到项目外。
- **`BUILD_DISABLED`**：启动时没有加 `--allow-build`。
- **`EXECUTION_DISABLED`**：没有加 `--allow-project-execution`。
- **`INTERPRETER_UNKNOWN`**：`interpreter_id` 不在 `--verify-python` 白名单中。
- **`OUTPUT_EXISTS`**：`output_name` 已存在；MCP 不覆盖目录，请换一个名字。
- **`SENSITIVE_FILE`**：项目里有 `.env`、私钥等敏感文件；移除它们，或在明确接受风险时加 `--allow-sensitive-files`。

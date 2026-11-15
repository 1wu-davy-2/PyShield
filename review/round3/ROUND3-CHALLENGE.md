# PyShield Round 3 受保护 Django 测试工程（第五轮修复版）

本目录是一个用 PyShield 0.6.0 保护的 Django 示例工程，运行时为 sourceless `.pyc`，没有提供源码、构建密钥或明文 flag。build id：`0d13b86aeefd81f8`。

本包由第五轮修复源码（`9365307`）重建：不包含任何 `__pycache__` 或非 runtime 的 `.pyc`；runtime 在导入时隔离字节码缓存，并在加载首个受保护模块前拒绝未签名的 `.pyc`（`unsigned bytecode cache`）。`FLAG` 赋值位于无条件 `raise` 之前，确保该常量实际进入加密代码对象；重建后已离线解密 `demo.hidden_flag` 并确认 `co_consts` 中存在该值（SHA-256 与旧包一致）。

## 目标

从受保护目录中恢复：

```python
demo.hidden_flag.FLAG
```

然后提交该字符串的 SHA-256：

```
a036bf169f93cf499e7496082e3d088e5f3c453b3620cf8eed00941b2d96c4ce
```

仅运行 `python manage.py check` / `python manage.py reveal` 不会打印 flag；允许使用任何静态分析、反编译、动态调试、内存 dump 或自动化手段。请同时说明实际利用路径。

## 运行环境

- CPython 3.12.x
- Django 5.x（仅用于管理命令；检查/启动不需要数据库迁移）
- 工作目录：本目录
- 许可证 `pyshield.lic` 已随包提供；不要使用或猜测构建方密钥

## 交付物

请按 `VULNERABILITY-REPORT-FORMAT.md` 提交：

1. 恢复出的 flag；
2. 可复现步骤与最小 PoC；
3. 根因与影响（如适用）；
4. 建议修复（如适用）；
5. 是否还存在不依赖内存 dump 的离线路径。

本样本仅用于授权安全评估，不得再分发或用于其他目标。

# 自保护构建 PoC

这是实验性的 `tools/selfbuild.py`：先用 PyShield 保护 PyShieldBuilder 自身的 `pyshield/` 与 `gui/` 源码，再交给 PyInstaller 打成 exe。它不替换默认的 `PyShieldBuilder.spec`，也不进入正式发布链路。

## 1. 使用

构建机需要与现有桌面构建相同的依赖：

```bash
python -m pip install pywebview==6.2.1 pyinstaller==6.22.3 cryptography
# Windows 额外需要
python -m pip install pythonnet==3.2.1

python tools/selfbuild.py
```

Linux 还需要现有的 GTK/WebKit2GTK 依赖（见 `docs/desktop-builder.md`）。正式发布构建仍需按现有流程注入 `pyshield/_tool_key.py`；PoC 不改变工具授权逻辑。

产物默认在 `dist-selfbuild/PyShieldBuilder-selfbuild.exe`（Linux 无 `.exe` 后缀），同时生成 SHA256 文件。

它与普通 `PyShieldBuilder.exe` 共用单实例指针：如果旧实例正在托盘运行，新 exe 会转发参数后退出。测试时先退出旧实例，或设置独立的 `PYSHELD_GUI_CONFIG_DIR`。

只准备受保护源码、不运行 PyInstaller：

```bash
python tools/selfbuild.py --stage-only
```

排查启动问题时构建控制台版本，异常会打印到终端而不是弹出窗口：

```bash
python tools/selfbuild.py --console --outdir dist-selfbuild-debug
```

中间目录通常会被清理；排查时可加 `--keep-work`，但该目录包含 runtime 明文源码和自用许可证，**不能交付**。

## 2. 流程

`tools/selfbuild.py` 执行四步：

1. 复制 `pyshield/`、`gui/`、`gui/web`、图标资源到临时 staging，排除 `runtime_template/`、`__pycache__`、`.pyc`；
2. 生成一个只负责启动 GUI 的 `manage.py`，用 `builder.build()` 做一次普通 PyShield 构建：
   - `cipher="chacha-pure"`；
   - `runtime_source=True`（原因见下）；
   - `integrity="off"`；
   - 永久、不绑定设备的自用许可证；
3. 校验受保护目录：所有 `pyshield/*.py` 和 `gui/*.py` 都是 stub，许可证为格式 3 且不含 `build_key`；删除构建密钥；在一个独立子进程里导入受保护的 `pyshield.builder`；
4. 用 `PyShieldBuilder.selfbuild.spec` 打包，产物复制到 `dist-selfbuild/`。

### 为什么要 `runtime_source=True`

PyInstaller 需要能静态分析 `_shield_runtime` 包，才能把它收进 PYZ。这里让 runtime 在 staging 中保留 `.py`，但 `selfbuild` 不会把该目录作为 `datas` 交付；最终 exe 里的 runtime 仍是编译后的字节码。中间目录在构建结束后删除。

### 为什么 `integrity="off"`

PyInstaller onefile 把模块放进 PYZ，运行时解压到 `_MEIPASS`，与 PyShield 默认按输出目录扫描的签名清单布局不一致。PoC 先关闭运行时清单校验，发布完整性依赖 Authenticode；后续若要保留清单，需要为 onefile 单独实现文件落盘与扫描路径。

### 许可证放哪

`pyshield.lic` 被作为 data 打包到 `_MEIPASS` 根目录，runtime 通过 `_dist_dir()/pyshield.lic` 找到它。它是永久、`any` 绑定的自用授权，仅用于让受保护的 builder 能启动。

## 3. 结果

- 解包 exe 后，`pyshield.builder`、`pyshield.licensing`、`gui.backend` 等不再是可直接反编译的业务源码，只有 stub；
- 实际代码以密文形式存在 stub 里的 Base85 payload 中；
- `PyShieldBuilder.selfbuild.spec` 自动收集全部受保护模块，并显式补 `webview` / `cryptography` / Windows 的 `clr` 等 PyInstaller 静态分析看不到的依赖；
- `tools/selfbuild.py` 会先用 AST 扫描原始 staging 源码，补上 `sqlite3`、`http.server` 等 stub 里隐藏的导入，避免冻结后出现 `ModuleNotFoundError`。

## 4. 边界

这层保护的收益是**提高静态逆向成本**，不是解决离线分发问题：

- `_shield_runtime` 的主密钥仍在 exe 内（以拆分/掩码的 `_keyring` 形式存在，不再是单个 Base85 常量）；
- 自用 `pyshield.lic` 也随 exe 分发；
- 攻击者提取这两样后，仍可复现 HKDF 与模块解密；
- 运行时内存 dump、调试器附加不受影响；
- 关闭完整性清单后，exe 被替换或局部修改不会被 runtime 发现，需要代码签名补足；
- `_runtime_bundle.py` 只是 base64+zlib+zip，解包成本约为一次解压，不是机密容器；
- 自保护 exe 不把 `runtime_source=True` 的中间树作为发布物；该中间树含明文 runtime 与主密钥，只用于 PyInstaller 静态分析，必须丢弃。

需要更强边界时，应改用 `--runtime-native` 编译 runtime，并配合 Authenticode、服务端解锁或硬件密钥。注意：自保护 spec 仍排除了 Cython/setuptools，因此自保护 exe 的 GUI 不提供原生 runtime 编译能力；正式桌面构建器已打包这些依赖。详见 [security-hardening.md](security-hardening.md)。

## 5. 验证

```bash
python -m unittest tests.test_selfbuild -v
python tools/selfbuild.py --stage-only
```

`tests/test_selfbuild.py` 会检查 staging 不包含 runtime 模板、受保护树全部为 stub、许可证不含 `build_key`，并在无 PyInstaller 的环境下实际导入受保护的 `pyshield.builder`。

CI 的 `.github/workflows/selfbuild-poc.yml` 会在 Windows 上构建并上传实验产物；它不参与 `build-develop` 的正式 release。

供安全审查使用的“PyInstaller 打包前受保护源码树”样本及其校验方法见 [selfbuild-review-sample.md](selfbuild-review-sample.md)。

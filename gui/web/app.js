(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const CIPHER_LABELS = {
    "chacha-pure": "ChaCha20 · 纯 Python",
    chacha: "ChaCha20-Poly1305",
    aesgcm: "AES-256-GCM",
  };
  const INTEGRITY_LABELS = { protected: "保护核心", full: "完整校验", off: "关闭" };
  const RUNTIME_LABELS = { source: "源码运行时", pyc: "字节码运行时" };
  const BINDING_LABELS = {
    any: "不绑定设备",
    "first-run": "首次启动自动绑定",
    fixed: "指定指纹",
  };
  const HISTORY_STATUS = {
    success: ["成功", "success"],
    failed: ["失败", "danger"],
    cancelled: ["已取消", "warning"],
  };
  const VIEW_META = {
    workspace: {
      title: "加密构建",
      subtitle: "选择项目目录，配置保护参数，一键生成可部署的受保护副本。",
      pill: "Django",
    },
    history: {
      title: "构建记录",
      subtitle: "查看本机最近的构建输出、状态与耗时。",
      pill: "Local",
    },
    trial: {
      title: "版本与试用",
      subtitle: "选择版本，用本机序列号在官网自助申请试用授权。",
      pill: "License",
    },
    settings: {
      title: "设置",
      subtitle: "配置新构建的默认算法、运行时与解释器检测策略。",
      pill: "Preferences",
    },
    about: {
      title: "关于",
      subtitle: "了解 PyShield 的保护链路与安全边界。",
      pill: "Info",
    },
  };

  const state = {
    app: null,
    settings: null,
    history: [],
    machine: "",
    view: "workspace",
    busy: false,
    pollTimer: null,
    renderedLogs: 0,
    lastStatus: "idle",
    lastSummary: null,
    errors: [],
    outputAuto: true,
    verifyAuto: true,
    verifyWasAuto: false,
    inspectTimer: null,
    inspectSeq: 0,
    maximized: false,
    preview: new URLSearchParams(location.search).get("preview") === "1",
    previewState: new URLSearchParams(location.search).get("state") || "",
    license: null,
    trial: null,
    trialTimer: null,
    trialTries: 0,
  };

  const LICENSE_MODE_LABELS = {
    development: "开发模式",
    unlicensed: "未激活",
    trial: "试用版",
    licensed: "正式授权",
    invalid: "授权异常",
  };
  const EDITION_LABELS = {
    trial: "试用版",
    pro: "专业版",
    enterprise: "企业版",
    oem: "OEM 版",
    development: "开发模式",
  };

  // ---------------------------------------------------------------- bridge

  function bridgeReady() {
    if (window.pywebview && window.pywebview.api) return Promise.resolve(true);
    if (state.preview) return Promise.resolve(false);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), 8000);
      window.addEventListener(
        "pywebviewready",
        () => {
          clearTimeout(timer);
          resolve(true);
        },
        { once: true }
      );
    });
  }

  async function call(name, ...args) {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === "function") {
      try {
        const result = await window.pywebview.api[name](...args);
        return result == null ? { ok: false, error: "后端没有返回结果" } : result;
      } catch (error) {
        return { ok: false, error: String((error && error.message) || error) };
      }
    }
    if (state.preview) return mockApi(name, args);
    return { ok: false, error: "界面桥接尚未就绪，请重新启动应用。" };
  }

  // ---------------------------------------------------------------- toast

  let toastTimer = null;
  function toast(message, kind = "info") {
    const node = $("#toast");
    $("#toastText").textContent = message;
    node.classList.remove("is-error", "is-success");
    if (kind === "error") node.classList.add("is-error");
    if (kind === "success") node.classList.add("is-success");
    node.hidden = false;
    requestAnimationFrame(() => node.classList.add("is-visible"));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.classList.remove("is-visible");
      setTimeout(() => {
        node.hidden = true;
      }, 220);
    }, 3400);
  }

  // ---------------------------------------------------------------- segments

  function setSegment(groupId, value) {
    const group = document.getElementById(groupId);
    if (!group) return;
    let matched = false;
    $$(".segment", group).forEach((button) => {
      const active = button.dataset.value === value;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-checked", active ? "true" : "false");
      if (active) matched = true;
    });
    if (!matched) {
      const first = $(".segment", group);
      if (first) first.classList.add("is-active");
    }
  }

  function getSegment(groupId) {
    const active = $(`.segment.is-active[data-value]`, document.getElementById(groupId));
    return active ? active.dataset.value : "";
  }

  function onSegment(groupId, handler) {
    const group = document.getElementById(groupId);
    if (!group) return;
    group.addEventListener("click", (event) => {
      const button = event.target.closest(".segment");
      if (!button) return;
      setSegment(groupId, button.dataset.value);
      if (handler) handler(button.dataset.value);
      syncSummary();
    });
  }

  // ---------------------------------------------------------------- form

  function splitList(text, separators) {
    const seen = new Set();
    const result = [];
    String(text || "")
      .split(separators)
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach((item) => {
        const value = separators === /[\s,;，；]+/ ? item.toLowerCase() : item;
        if (!seen.has(value)) {
          seen.add(value);
          result.push(value);
        }
      });
    return result;
  }

  function collect() {
    return {
      project: $("#projectPath").value.trim(),
      output: $("#outputPath").value.trim(),
      verify_python: $("#verifyPython").value.trim(),
      cipher: getSegment("cipherGroup"),
      integrity: getSegment("integrityGroup"),
      runtime: getSegment("runtimeGroup"),
      expiry_mode: getSegment("expiryGroup"),
      expire_days: $("#expireDays").value,
      expire_date: $("#expireDate").value,
      grace_days: $("#graceDays").value,
      bind_mode: getSegment("bindingGroup"),
      fingerprints:
        getSegment("bindingGroup") === "fixed"
          ? splitList($("#bindDevice").value, /[\s,;，；]+/)
          : [],
      customer: $("#customer").value.trim(),
      encrypt_migrations: $("#encryptMigrations").checked,
      encrypt_tests: $("#encryptTests").checked,
      mutable_patterns: $("#mutablePatterns").value,
      excludes: $("#excludes").value,
      no_verify: $("#noVerify").checked,
      force: $("#forceOverwrite").checked,
    };
  }

  function expiresLabel(form) {
    if (!form) return "永久";
    if (form.expiry_mode === "days") return `${form.expire_days || "?"} 天`;
    if (form.expiry_mode === "date") return form.expire_date || "指定日期";
    return "永久";
  }

  function bindingLabel(form) {
    if (!form || form.bind_mode === "any") return BINDING_LABELS.any;
    if (form.bind_mode === "first-run") return BINDING_LABELS["first-run"];
    const count = form.fingerprints.length;
    return count ? `指定指纹 · ${count} 个` : "指定指纹 · 未填写";
  }

  function baseName(path) {
    const text = String(path || "").replace(/[\\/]+$/, "");
    const parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || "";
  }

  function syncExpiryFields() {
    const mode = getSegment("expiryGroup");
    $("#expireDaysField").hidden = mode !== "days";
    $("#expireDateField").hidden = mode !== "date";
  }

  function syncBindingFields() {
    const mode = getSegment("bindingGroup");
    $("#bindDeviceField").hidden = mode !== "fixed";
    const hint = $("#bindingHint");
    if (mode === "first-run") {
      hint.textContent =
        "客户机指纹未知时用此模式：程序第一次运行会锁定该机器，部署包只能在首台启动的客户机运行。";
    } else if (mode === "fixed") {
      hint.textContent = "构建时写入指定指纹；更换机器需重新签发许可证或在许可证中追加新指纹。";
    } else {
      hint.textContent = "不绑定：部署包可在任意兼容机器运行。首次启动绑定适合交付时无法获取客户机指纹的场景。";
    }
  }

  function syncSecretPreview() {
    const output = $("#outputPath").value.trim();
    $("#secretPreview").textContent = output
      ? `${baseName(output)}.shield-secret.json`
      : "…shield-secret.json";
  }

  function syncSummary() {
    const form = collect();
    const project = form.project || "—";
    $("#summaryProject").textContent = project;
    $("#summaryProject").title = project;
    $("#summaryOutput").textContent = form.output || "—";
    $("#summaryOutput").title = form.output || "";
    $("#summaryCipher").textContent = CIPHER_LABELS[form.cipher] || form.cipher;
    $("#summaryRuntime").textContent = RUNTIME_LABELS[form.runtime] || form.runtime;
    $("#summaryIntegrity").textContent = INTEGRITY_LABELS[form.integrity] || form.integrity;
    $("#summaryBinding").textContent = bindingLabel(form);
    $("#summaryLicense").textContent = expiresLabel(form);
    $("#statLicense").textContent = expiresLabel(form);
    if (!state.lastSummary) {
      $("#statLicenseHint").textContent = "当前表单未签发";
    }
    $("#suffixHint").textContent = (state.settings && state.settings.output_suffix) || "-shielded";
    syncSecretPreview();
    if (state.busy) return;
    if (state.lastSummary) {
      const badge = $("#summaryBadge");
      badge.textContent = "待重新构建";
      badge.className = "status-badge warning";
    }
  }

  function scheduleInspect(delay = 420) {
    clearTimeout(state.inspectTimer);
    state.inspectTimer = setTimeout(inspectNow, delay);
  }

  async function inspectNow() {
    const sequence = ++state.inspectSeq;
    const form = collect();
    const response = await call("inspect", form);
    if (sequence !== state.inspectSeq || !response.ok) return;

    if (state.outputAuto && response.output && !$("#outputPath").value.trim()) {
      $("#outputPath").value = response.output;
      syncSecretPreview();
    }

    const project = response.project || {};
    const badge = $("#projectBadge");
    if (form.project && project.valid) {
      badge.textContent = "已识别";
      badge.className = "status-badge success";
      const packageNames = Array.isArray(project.packages) ? project.packages : [];
      const hintPackages = packageNames.slice(0, 3).join("、");
      $("#projectHint").textContent = hintPackages
        ? `发现 ${project.modules} 个待加密模块 · 应用包：${hintPackages}${packageNames.length > 3 ? " 等" : ""}`
        : `发现 ${project.modules} 个待加密模块`;
      $("#statModules").textContent = String(project.modules);
      $("#statModulesHint").textContent = packageNames.length
        ? `${packageNames.length} 个应用包 · 点击开始执行`
        : "点击开始执行加密";
    } else if (form.project) {
      badge.textContent = "无效目录";
      badge.className = "status-badge danger";
      $("#projectHint").textContent = "该目录下没有 manage.py，请重新选择 Django 项目根目录。";
      $("#statModules").textContent = "—";
      $("#statModulesHint").textContent = "未检测到可加密模块";
    } else {
      badge.textContent = "未选择";
      badge.className = "status-badge";
      $("#projectHint").textContent = "选择项目后自动识别应用包与待加密模块。";
      $("#statModules").textContent = "—";
      $("#statModulesHint").textContent = "选择项目后自动检测";
    }

    state.errors = Array.isArray(response.errors) ? response.errors : [];

    const verifyInput = $("#verifyPython");
    if (
      response.suggested_verify &&
      state.verifyAuto &&
      !verifyInput.value.trim()
    ) {
      verifyInput.value = response.suggested_verify.executable;
      state.verifyWasAuto = true;
    }

    const notice = $("#verifyNotice");
    const noticeText = $("#verifyNoticeText");
    if (response.verify_error) {
      notice.hidden = false;
      noticeText.textContent = response.verify_error;
      $("#verifyHint").textContent = "校验将不会执行，构建仍可继续。";
    } else if (response.verify) {
      notice.hidden = true;
      $("#verifyHint").textContent = `${response.verify.summary} · 将执行 manage.py check`;
    } else if (response.suggested_verify) {
      notice.hidden = true;
      $("#verifyHint").textContent = `${response.suggested_verify.summary} · 自动检测`;
    } else {
      notice.hidden = false;
      noticeText.textContent =
        "未找到安装了 Django 且 Python 版本匹配的解释器，构建将跳过 manage.py check。";
      $("#verifyHint").textContent = "可手动选择项目虚拟环境中的 Python 解释器。";
    }

    if (!state.machine && response.machine) {
      state.machine = response.machine;
      $("#machineFingerprint").value = response.machine;
      $("#infoMachine").textContent = response.machine;
    }
  }

  function onProjectInput() {
    if (state.outputAuto) {
      const project = $("#projectPath").value.trim();
      const suffix = (state.settings && state.settings.output_suffix) || "-shielded";
      $("#outputPath").value = project ? `${project.replace(/[\\/]+$/, "")}${suffix}` : "";
    }
    if (state.verifyWasAuto) {
      $("#verifyPython").value = "";
      state.verifyWasAuto = false;
    }
    state.verifyAuto = true;
    syncSummary();
    scheduleInspect();
  }

  // ---------------------------------------------------------------- browse

  async function browseProject() {
    const response = await call("browse_project");
    if (!response.ok) {
      if (!response.cancelled && response.error) toast(response.error, "error");
      return;
    }
    state.outputAuto = true;
    $("#projectPath").value = response.project || "";
    $("#outputPath").value = response.output || "";
    state.verifyAuto = true;
    state.verifyWasAuto = false;
    syncSummary();
    await inspectNow();
    toast("已载入项目目录");
  }

  async function browseOutput() {
    const response = await call(
      "browse_output",
      $("#projectPath").value.trim(),
      $("#outputPath").value.trim()
    );
    if (!response.ok) {
      if (!response.cancelled && response.error) toast(response.error, "error");
      return;
    }
    state.outputAuto = false;
    $("#outputPath").value = response.output || "";
    syncSecretPreview();
    scheduleInspect(200);
  }

  async function browsePython() {
    const response = await call("browse_python");
    if (!response.ok) {
      if (!response.cancelled && response.error) toast(response.error, "error");
      return;
    }
    state.verifyAuto = false;
    state.verifyWasAuto = false;
    $("#verifyPython").value = response.python.executable;
    toast(`已选择 Python ${response.python.version}`, "success");
    await inspectNow();
  }

  async function copyFingerprint() {
    const value = $("#machineFingerprint").value.trim();
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      toast("机器指纹已复制", "success");
    } catch (error) {
      const input = $("#machineFingerprint");
      input.removeAttribute("readonly");
      input.select();
      document.execCommand("copy");
      input.setAttribute("readonly", "readonly");
      toast("机器指纹已复制", "success");
    }
  }

  function useMachine() {
    const input = $("#bindDevice");
    const current = splitList(input.value, /[\s,;，；]+/);
    if (!state.machine) {
      toast("尚未读取到本机指纹", "error");
      return;
    }
    if (!current.includes(state.machine)) current.push(state.machine);
    input.value = current.join(", ");
    syncSummary();
    toast("已加入本机指纹");
  }

  // ---------------------------------------------------------------- errors

  function showErrors(errors) {
    const box = $("#formErrors");
    const list = $("#formErrorsList");
    list.textContent = "";
    if (!errors || !errors.length) {
      box.hidden = true;
      return;
    }
    errors.forEach((message) => {
      const item = document.createElement("li");
      item.textContent = message;
      list.appendChild(item);
    });
    box.hidden = false;
  }

  // ---------------------------------------------------------------- build

  function setBusy(busy) {
    state.busy = busy;
    $("#startBtn").disabled = busy;
    $("#startBtn").querySelector("span").textContent = busy ? "构建中…" : "开始加密";
    $("#cancelBtn").hidden = !busy;
    $("#navBuildDot").hidden = !busy;
    $(".stack").classList.toggle("is-locked", busy);
    $("#progressBlock").hidden = !busy && state.lastStatus === "idle";
    if (busy) {
      const badge = $("#summaryBadge");
      badge.textContent = "构建中";
      badge.className = "status-badge warning";
      $("#resultBanner").hidden = true;
      $("#resultActions").hidden = true;
    }
  }

  function resetTerminal() {
    const terminal = $("#terminal");
    terminal.textContent = "";
    state.renderedLogs = 0;
  }

  function appendLogLine(line) {
    const terminal = $("#terminal");
    const placeholder = terminal.querySelector(".terminal-placeholder");
    if (placeholder) placeholder.remove();
    const nearBottom =
      terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 48;
    const node = document.createElement("span");
    node.className = "log-line " + logClass(line);
    node.textContent = line;
    terminal.appendChild(node);
    if (nearBottom || state.renderedLogs < 2) terminal.scrollTop = terminal.scrollHeight;
  }

  function logClass(line) {
    const text = String(line).toLowerCase();
    if (text.includes("error") || text.includes("failed") || text.includes("cannot") || text.includes("取消")) {
      return "is-error";
    }
    if (text.includes("warn") || text.includes("note:") || text.includes("skipping")) {
      return "is-warn";
    }
    if (text.includes("done:") || text.includes("protected ") || text.includes("build complete")) {
      return "is-good";
    }
    return "";
  }

  function renderLogs(logs) {
    if (!Array.isArray(logs)) return;
    if (logs.length < state.renderedLogs) {
      resetTerminal();
    }
    for (let index = state.renderedLogs; index < logs.length; index += 1) {
      appendLogLine(logs[index]);
    }
    state.renderedLogs = logs.length;
    if (logs.length && $("#liveDot").classList.contains("is-live")) {
      $("#terminal").scrollTop = $("#terminal").scrollHeight;
    }
  }

  function renderProgress(snapshot) {
    const percent = Math.max(0, Math.min(100, Number(snapshot.percent) || 0));
    $("#progressBlock").hidden = false;
    $("#progressStage").textContent = snapshot.stage_label || "准备中";
    $("#progressPercent").textContent = `${percent}%`;
    $("#progressBar").style.width = `${percent}%`;
    $("#liveDot").classList.toggle("is-live", snapshot.status === "running");
  }

  async function startBuild() {
    if (state.busy) return;
    showErrors([]);
    const form = collect();
    resetTerminal();
    $("#progressBlock").hidden = false;
    $("#progressBar").style.width = "0%";
    $("#progressPercent").textContent = "0%";
    $("#progressStage").textContent = "校验配置";
    $("#resultBanner").hidden = true;
    $("#resultActions").hidden = true;
    setBusy(true);
    state.lastStatus = "running";

    const response = await call("start_build", form);
    if (!response.ok) {
      setBusy(false);
      state.lastStatus = "idle";
      $("#progressBlock").hidden = true;
      showErrors(response.errors || (response.error ? [response.error] : ["构建无法启动。"]));
      if (!maybeShowTrialEnded(response.license)) {
        if (response.license && response.license.code === "not_activated") {
          switchView("trial");
          toast("请先申请试用或导入授权", "error");
        } else {
          toast("请先修正表单问题", "error");
        }
      }
      return;
    }
    if (response.note) appendLogLine(response.note);
    toast("构建已开始");
    pollBuild();
  }

  async function pollBuild() {
    clearTimeout(state.pollTimer);
    if (!state.busy) return;
    const response = await call("snapshot");
    if (!response.ok) {
      state.pollTimer = setTimeout(pollBuild, 800);
      return;
    }
    const snapshot = response.state || {};
    renderProgress(snapshot);
    renderLogs(snapshot.logs);
    if (snapshot.status === "running") {
      state.pollTimer = setTimeout(pollBuild, 350);
    } else {
      finishBuild(snapshot);
    }
  }

  function finishBuild(snapshot) {
    setBusy(false);
    state.lastStatus = snapshot.status;
    $("#progressBlock").hidden = false;
    $("#liveDot").classList.remove("is-live");
    renderProgress(snapshot);

    const banner = $("#resultBanner");
    const icon = $("#resultIcon");
    banner.hidden = false;
    banner.className = "result-banner";

    if (snapshot.status === "success") {
      const summary = snapshot.summary || {};
      state.lastSummary = summary;
      const badge = $("#summaryBadge");
      badge.textContent = "构建完成";
      badge.className = "status-badge success";
      icon.innerHTML = '<svg class="icon"><use href="#i-check"/></svg>';
      $("#resultTitle").textContent = "构建完成";
      $("#resultText").textContent = `已生成 ${summary.modules} 个加密模块 · ${summary.size_text} · 用时 ${summary.elapsed}s`;
      $("#resultActions").hidden = false;
      $("#statModules").textContent = String(summary.modules);
      $("#statSize").textContent = summary.size_text;
      $("#statElapsed").textContent = `${summary.elapsed}s`;
      $("#statLicense").textContent = summary.expires || "永久";
      $("#statSizeHint").textContent = "上一次构建输出";
      $("#statElapsedHint").textContent = "内置引擎，无需外部依赖";
      $("#statLicenseHint").textContent = "许可证已写入输出目录";
      toast("加密构建完成", "success");
    } else if (snapshot.status === "cancelled") {
      const badge = $("#summaryBadge");
      badge.textContent = "已取消";
      badge.className = "status-badge warning";
      banner.classList.add("is-warning");
      icon.innerHTML = '<svg class="icon"><use href="#i-alert"/></svg>';
      $("#resultTitle").textContent = "构建已取消";
      $("#resultText").textContent = "临时目录已清理，未发布新的输出。";
      toast("构建已取消", "error");
    } else {
      const badge = $("#summaryBadge");
      badge.textContent = "构建失败";
      badge.className = "status-badge danger";
      banner.classList.add("is-error");
      icon.innerHTML = '<svg class="icon"><use href="#i-alert"/></svg>';
      $("#resultTitle").textContent = "构建失败";
      $("#resultText").textContent = snapshot.error || "构建过程中出现未知错误。";
      $("#statLicenseHint").textContent = "构建失败，未签发许可证";
      toast("构建失败", "error");
      appendLogLine(`ERROR ${snapshot.error || "unknown error"}`);
    }
    refreshHistory();
  }

  async function cancelBuild() {
    const response = await call("cancel_build");
    if (!response.ok) {
      toast(response.error || "取消失败", "error");
      return;
    }
    $("#cancelBtn").disabled = true;
    toast("正在取消构建…");
    setTimeout(() => {
      $("#cancelBtn").disabled = false;
    }, 1200);
  }

  // ---------------------------------------------------------------- history

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const pad = (number) => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function renderHistory(entries) {
    state.history = Array.isArray(entries) ? entries : [];
    $("#historyCount").textContent = String(state.history.length);
    const body = $("#historyBody");
    const empty = $("#historyEmpty");
    const table = $(".data-table");
    body.textContent = "";
    const hasRows = state.history.length > 0;
    table.hidden = !hasRows;
    empty.hidden = hasRows;
    if (!hasRows) return;

    state.history.forEach((entry) => {
      const row = document.createElement("tr");

      const projectCell = document.createElement("td");
      const projectWrap = document.createElement("div");
      projectWrap.className = "cell-title";
      const projectName = document.createElement("strong");
      projectName.textContent = entry.project_name || baseName(entry.project) || "未命名项目";
      const projectMeta = document.createElement("span");
      projectMeta.textContent = entry.customer || entry.build_id || "Django 项目";
      projectWrap.append(projectName, projectMeta);
      projectCell.appendChild(projectWrap);

      const outputCell = document.createElement("td");
      const output = document.createElement("div");
      output.className = "cell-path";
      output.textContent = entry.output || "—";
      output.title = entry.output || "";
      outputCell.appendChild(output);

      const modulesCell = document.createElement("td");
      modulesCell.textContent = entry.modules == null ? "—" : String(entry.modules);

      const cipherCell = document.createElement("td");
      cipherCell.textContent = CIPHER_LABELS[entry.cipher] || entry.cipher || "—";

      const elapsedCell = document.createElement("td");
      elapsedCell.textContent = entry.elapsed == null ? "—" : `${entry.elapsed}s`;

      const statusCell = document.createElement("td");
      const status = HISTORY_STATUS[entry.status] || [entry.status || "未知", "soft"];
      const statusBadge = document.createElement("span");
      statusBadge.className = `status-badge ${status[1]}`;
      statusBadge.textContent = status[0];
      statusCell.appendChild(statusBadge);

      const timeCell = document.createElement("td");
      timeCell.className = "cell-mono";
      timeCell.textContent = formatTime(entry.created);

      const actionCell = document.createElement("td");
      const actions = document.createElement("div");
      actions.className = "cell-actions";

      const openButton = document.createElement("button");
      openButton.className = "table-action";
      openButton.title = "打开输出目录";
      openButton.innerHTML = '<svg class="icon"><use href="#i-folder"/></svg>';
      openButton.disabled = !entry.output || entry.missing === true || entry.status !== "success";
      openButton.addEventListener("click", () => reveal(entry.output));

      const secretButton = document.createElement("button");
      secretButton.className = "table-action";
      secretButton.title = "定位构建密钥";
      secretButton.innerHTML = '<svg class="icon"><use href="#i-key"/></svg>';
      secretButton.disabled = !entry.secret;
      secretButton.addEventListener("click", () => reveal(entry.secret));

      actions.append(openButton, secretButton);
      actionCell.appendChild(actions);

      row.append(
        projectCell,
        outputCell,
        modulesCell,
        cipherCell,
        elapsedCell,
        statusCell,
        timeCell,
        actionCell
      );
      if (entry.error) row.title = entry.error;
      body.appendChild(row);
    });
  }

  async function refreshHistory() {
    const response = await call("get_history");
    if (response.ok) renderHistory(response.history);
    else if (Array.isArray(response.history)) renderHistory(response.history);
  }

  async function clearHistory() {
    if (!state.history.length) {
      toast("当前没有构建记录");
      return;
    }
    if (!window.confirm("确定清空所有本地构建记录？此操作不影响已生成的输出目录。")) return;
    const response = await call("clear_history");
    if (!response.ok) {
      toast(response.error || "清空失败", "error");
      return;
    }
    renderHistory([]);
    toast("构建记录已清空", "success");
  }

  async function reveal(path) {
    if (!path) return;
    const response = await call("reveal", path);
    if (!response.ok) toast(response.error || "路径不存在", "error");
  }

  // ---------------------------------------------------------------- settings

  function syncSettingsForm() {
    const settings = state.settings || {};
    $("#settingCipher").value = settings.cipher || "chacha-pure";
    $("#settingIntegrity").value = settings.integrity || "protected";
    $("#settingRuntime").value = settings.runtime || "pyc";
    $("#settingSuffix").value = settings.output_suffix || "-shielded";
    $("#settingAutoPython").checked = settings.auto_detect_python !== false;
    $("#settingNoVerify").checked = settings.no_verify === true;
    $("#licenseServer").value = settings.activation_server || "";
  }

  async function saveSettings() {
    const payload = {
      cipher: $("#settingCipher").value,
      integrity: $("#settingIntegrity").value,
      runtime: $("#settingRuntime").value,
      output_suffix: $("#settingSuffix").value.trim(),
      auto_detect_python: $("#settingAutoPython").checked,
      no_verify: $("#settingNoVerify").checked,
      activation_server: $("#licenseServer").value.trim(),
    };
    const response = await call("save_settings", payload);
    if (!response.ok) {
      toast(response.error || "保存失败", "error");
      return;
    }
    state.settings = response.settings;
    $("#suffixHint").textContent = response.settings.output_suffix;
    if (state.outputAuto) {
      const project = $("#projectPath").value.trim();
      if (project) $("#outputPath").value = `${project.replace(/[\\/]+$/, "")}${response.settings.output_suffix}`;
    }
    syncSecretPreview();
    syncSummary();
    toast("默认设置已保存", "success");
  }

  // ---------------------------------------------------------------- licensing

  function licenseQuotaText(license) {
    if (!license) return "—";
    if (license.quota && license.quota.limit) {
      return `${license.quota.used || 0} / ${license.quota.limit} 次每月`;
    }
    if (license.mode === "trial") {
      const trial = license.trial || {};
      return `${trial.builds || 0} / ${trial.max_builds || 0} 次试用`;
    }
    if (license.mode === "licensed") return "不限";
    return "—";
  }

  function licenseTrialDays(expires) {
    if (!expires) return null;
    const target = new Date(`${expires}T00:00:00`);
    if (Number.isNaN(target.getTime())) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.max(0, Math.round((target - today) / 86400000));
  }

  function licenseExpiryText(license) {
    if (!license) return "—";
    if (license.mode === "trial") {
      const days = (license.trial || {}).days_left;
      return days == null ? "—" : `剩余 ${days} 天`;
    }
    if (license.mode === "licensed") {
      return license.expires ? `${license.expires}（宽限 ${license.grace_days || 0} 天）` : "永久";
    }
    return "—";
  }

  function renderLicense(license) {
    const data = license || { mode: "invalid", valid: false, reason: "尚未读取授权状态" };
    state.license = data;
    const mode = data.mode || "invalid";
    const tone = !data.valid ? (mode === "unlicensed" ? "warning" : "danger") : mode === "licensed" ? "success" : "warning";
    const pill = $("#licensePill");
    const pillText = $("#licensePillText");
    pill.classList.remove("is-success", "is-warning", "is-danger");
    pill.classList.add(`is-${tone}`);
    const editionLabel = EDITION_LABELS[data.edition] || LICENSE_MODE_LABELS[mode] || "未知";
    if (mode === "licensed" && data.edition === "trial") {
      const days = licenseTrialDays(data.expires);
      pillText.textContent = days == null ? "试用版" : `试用 · ${days} 天`;
    } else if (mode === "licensed") {
      pillText.textContent = editionLabel;
    } else if (mode === "trial") {
      const days = (data.trial || {}).days_left;
      pillText.textContent = days == null ? "试用版" : `试用 · ${days} 天`;
    } else {
      pillText.textContent = LICENSE_MODE_LABELS[mode] || "授权异常";
    }
    pill.title = data.reason || "查看工具授权状态";

    const badge = $("#licenseBadge");
    badge.className = `status-badge ${data.valid ? (mode === "licensed" ? "success" : "warning") : "danger"}`;
    badge.textContent = LICENSE_MODE_LABELS[mode] || "未知状态";

    $("#licenseReason").textContent = data.reason || "—";
    $("#licenseEdition").textContent = editionLabel;
    $("#licenseCustomer").textContent = data.customer || "—";
    $("#licenseId").textContent = data.license_id || "—";
    $("#licenseExpires").textContent = licenseExpiryText(data);
    $("#licenseQuota").textContent = licenseQuotaText(data);
    $("#licenseMachine").textContent = data.machine || state.machine || "—";
    applyEditionLimits(data);
  }

  function isTrialEdition(license) {
    return Boolean(license && license.edition === "trial");
  }

  function applyEditionLimits(license) {
    const trial = isTrialEdition(license);
    document.body.classList.toggle("is-trial", trial);
    $$("#cipherGroup .segment").forEach((button) => {
      const locked = trial && button.dataset.value !== "chacha-pure";
      button.classList.toggle("is-locked", locked);
      button.setAttribute("aria-disabled", locked ? "true" : "false");
    });
    $$("#integrityGroup .segment").forEach((button) => {
      const locked = trial && button.dataset.value === "full";
      button.classList.toggle("is-locked", locked);
      button.setAttribute("aria-disabled", locked ? "true" : "false");
    });
    const customer = $("#customer");
    if (customer) {
      customer.disabled = trial;
      if (trial && customer.value) customer.value = "";
    }
    if (!trial) return;
    if (getSegment("cipherGroup") && getSegment("cipherGroup") !== "chacha-pure") {
      setSegment("cipherGroup", "chacha-pure");
    }
    if (getSegment("integrityGroup") === "full") {
      setSegment("integrityGroup", "protected");
    }
    syncSummary();
  }

  const TRIAL_END_CODES = ["quota_exhausted", "trial_expired", "expired"];

  function maybeShowTrialEnded(license) {
    if (!license || license.valid) return false;
    if (!isTrialEdition(license)) return false;
    if (!TRIAL_END_CODES.includes(license.code || "")) return false;
    $("#trialEndedText").textContent = license.code === "quota_exhausted"
      ? "3 次试用构建额度已用完。购买专业版后可以继续构建，并解锁原生运行时与产物许可证签发。"
      : "14 天试用期已结束。购买专业版后可以继续构建，并解锁原生运行时与产物许可证签发。";
    $("#trialEndedModal").hidden = false;
    return true;
  }

  async function refreshLicense() {
    const response = await call("license_status");
    if (!response.ok) {
      renderLicense(response.license || null);
      return;
    }
    renderLicense(response.license);
    if (response.server && !$("#licenseServer").value.trim()) {
      $("#licenseServer").value = response.server;
    }
  }

  async function pickLicenseFile() {
    const picked = await call("browse_license");
    if (!picked || !picked.ok) {
      if (picked && picked.error) toast(picked.error, "error");
      return "";
    }
    return picked.path;
  }

  async function persistLicenseServer() {
    const server = $("#licenseServer").value.trim().replace(/\/+$/, "");
    $("#licenseServer").value = server;
    if (((state.settings || {}).activation_server || "") === server) return server;
    const response = await call("save_settings", {
      ...(state.settings || {}),
      activation_server: server,
    });
    if (response.ok) state.settings = response.settings;
    return server;
  }

  function applyLicenseResponse(response, message) {
    if (!response.ok) {
      toast(response.error || "授权操作失败", "error");
      if (response.license) renderLicense(response.license);
      return false;
    }
    if (response.license) renderLicense(response.license);
    toast(message, "success");
    return true;
  }

  async function licenseInstall() {
    const path = await pickLicenseFile();
    if (!path) return;
    const response = await call("license_install", path);
    if (applyLicenseResponse(response, "授权文件已导入")) await refreshLicense();
  }

  async function licenseActivate() {
    const server = await persistLicenseServer();
    if (!server) {
      toast("请先填写激活服务器地址", "error");
      $("#licenseServer").focus();
      return;
    }
    const path = await pickLicenseFile();
    if (!path) return;
    const response = await call("license_activate", path, server);
    if (applyLicenseResponse(response, "已在线激活并绑定本机")) await refreshLicense();
  }

  async function licenseRequest() {
    const path = await pickLicenseFile();
    if (!path) return;
    const response = await call("license_request", path);
    if (!applyLicenseResponse(response, "离线激活请求已生成")) return;
    toast(`请求文件：${response.path}`, "success");
    await reveal(response.path);
  }

  async function licenseApply() {
    const path = await pickLicenseFile();
    if (!path) return;
    const response = await call("license_apply", path);
    if (applyLicenseResponse(response, "离线激活响应已应用")) await refreshLicense();
  }

  async function licenseHeartbeat() {
    const server = await persistLicenseServer();
    if (!server) {
      toast("请先填写激活服务器地址", "error");
      $("#licenseServer").focus();
      return;
    }
    const response = await call("license_heartbeat");
    if (applyLicenseResponse(response, "心跳校验通过")) await refreshLicense();
  }

  async function licenseRevocations() {
    const server = await persistLicenseServer();
    if (!server) {
      toast("请先填写激活服务器地址", "error");
      $("#licenseServer").focus();
      return;
    }
    const response = await call("license_revocations");
    if (!applyLicenseResponse(response, "撤销列表已同步")) return;
    toast(`已同步 ${response.revoked || 0} 条撤销记录`, "success");
    await refreshLicense();
  }

  async function licenseDeactivate() {
    if (!window.confirm("解除本机授权后将删除本机保存的授权文件，确定继续？")) return;
    const response = await call("license_deactivate");
    if (applyLicenseResponse(response, "本机授权已解除")) await refreshLicense();
  }

  // ---------------------------------------------------------------- trial

  const TRIAL_MODE_TEXT = {
    unlicensed: "未激活",
    licensed: "正式授权",
    trial: "试用中",
    development: "开发模式",
    invalid: "授权异常",
  };

  function shortMachine(machine) {
    const value = String(machine || "");
    if (!value) return "—";
    return value.length > 20 ? `${value.slice(0, 20)}…` : value;
  }

  function renderTrial(response) {
    const data = (response && response.trial) || null;
    const badge = $("#trialBadge");
    if (!data || !data.serial) {
      badge.className = "status-badge danger";
      badge.textContent = "不可用";
      $("#trialSub").textContent = (data && data.error) || "无法读取本机序列号";
      return;
    }
    state.trial = data;
    const server = data.server || "";
    const devConfig = $("#trialDevConfig");
    const devMode = Boolean(state.app && state.app.dev);
    if (devConfig) {
      devConfig.hidden = !devMode;
      const input = $("#trialServer");
      if (devMode && server && input && !input.value.trim()) input.value = server;
    }

    const active = Boolean(data.active && data.trial);
    const ended = Boolean(data.trial && !data.active);
    const licensed = data.mode === "licensed" && !data.trial;
    const invalid = data.mode === "invalid";
    badge.className = `status-badge ${active ? "warning" : licensed ? "success" : invalid || ended ? "danger" : ""}`;
    badge.textContent = active
      ? "试用中"
      : ended
        ? "试用已结束"
        : licensed
          ? "正式授权"
          : TRIAL_MODE_TEXT[data.mode] || "未激活";

    $("#trialHeadline").textContent = active
      ? "试用版已激活"
      : ended
        ? "试用已结束"
        : licensed
          ? "当前为正式授权"
          : "选择适合你的版本";
    $("#trialSub").textContent = active
      ? `剩余 ${data.days_left == null ? "—" : data.days_left} 天、${data.builds_left == null ? "—" : data.builds_left} 次构建。到期后加密构建会被拒绝，已生成的产物不受影响。`
      : ended
        ? "14 天试用期或 3 次构建额度已用完，购买专业版后可以继续构建。"
        : licensed
          ? "已安装正式授权，可以继续使用全部已购买功能。"
          : "免费版可自助开通：14 天、3 次加密构建，无需付款。";

    $("#trialSerial").textContent = data.serial || "—";
    $("#trialMachine").textContent = shortMachine(data.machine);
    $("#trialDays").textContent = data.days_left == null ? "—" : `${data.days_left} 天`;
    $("#trialBuilds").textContent = data.builds_left == null
      ? "—"
      : `${data.builds_left}${data.build_limit ? ` / ${data.build_limit}` : ""} 次`;
    $("#trialExpires").textContent = data.expires || "—";

    $("#trialServerLine").textContent = server
      ? "已连接，可申请试用与刷新授权"
      : "未配置，请联系供应商获取授权";
    const serverBadge = $("#trialServerBadge");
    serverBadge.textContent = server ? "已连接" : "不可用";
    serverBadge.className = `status-badge ${server ? "success" : "danger"}`;

    $("#navTrialBadge").hidden = !active;
    $("#planTrial").classList.toggle("is-current", active);
    const trialButton = $('[data-plan-open="trial"]');
    if (trialButton) trialButton.textContent = active ? "重新打开官网" : "申请 14 天试用";
    $$("[data-plan-open]").forEach((button) => {
      button.disabled = !server;
      button.title = server ? "" : "授权服务未配置，暂时无法在线申请";
    });

    const needsActivation = data.mode === "unlicensed";
    const banner = $("#trialBanner");
    banner.hidden = !(active || ended || needsActivation);
    if (!banner.hidden) {
      const bannerBadge = $("#trialBannerBadge");
      bannerBadge.textContent = active ? "试用版" : ended ? "试用已结束" : "未激活";
      bannerBadge.className = `status-badge ${active ? "warning" : "danger"}`;
      $("#trialBannerText").textContent = active
        ? `试用版 · 剩余 ${data.days_left == null ? "—" : data.days_left} 天 · 剩余 ${data.builds_left == null ? "—" : data.builds_left} 次构建`
        : ended
          ? "试用额度已用完，购买专业版后可继续构建"
          : "尚未激活，先申请 14 天免费试用再开始构建";
      $("#trialBannerAction").textContent = active ? "升级版本" : "去申请";
    }
  }

  async function refreshTrial(options = {}) {
    const response = await call("trial_state");
    if (!response || !response.ok) {
      renderTrial(response);
      if (!options.silent) toast((response && response.error) || "无法读取试用状态", "error");
      return response;
    }
    renderTrial(response);
    return response;
  }

  function stopTrialPoll(message) {
    if (state.trialTimer) clearInterval(state.trialTimer);
    state.trialTimer = null;
    const hint = $("#trialPollHint");
    if (hint) hint.hidden = true;
    if (message) toast(message, "error");
  }

  function startTrialPoll() {
    stopTrialPoll();
    state.trialTries = 0;
    $("#trialPollHint").hidden = false;
    const tick = async () => {
      state.trialTries += 1;
      if (state.trialTries > 48) {
        stopTrialPoll("等待授权超时，可稍后点「刷新授权状态」");
        return;
      }
      const response = await call("trial_poll");
      if (response && response.granted) {
        stopTrialPoll();
        toast("试用授权已激活", "success");
        renderTrial(response);
        if (response.license) renderLicense(response.license);
        else await refreshLicense();
        return;
      }
      if (response && response.trial) renderTrial(response);
    };
    tick();
    state.trialTimer = setInterval(tick, 2500);
  }

  async function trialOpen(edition) {
    const input = $("#trialServer").value.trim().replace(/\/+$/, "");
    $("#trialServer").value = input;
    if (input && ((state.settings || {}).trial_server || "") !== input) {
      const saved = await call("trial_set_server", input);
      if (saved && saved.ok) state.settings = { ...(state.settings || {}), trial_server: saved.server };
    }
    const response = await call("trial_open", edition);
    if (!response || !response.ok) {
      toast((response && response.error) || "无法打开官网", "error");
      return;
    }
    toast("已打开浏览器，请在网页上完成申请", "success");
    if (edition === "trial") startTrialPoll();
  }

  async function trialSaveServer() {
    const value = $("#trialServer").value.trim().replace(/\/+$/, "");
    $("#trialServer").value = value;
    const response = await call("trial_set_server", value);
    if (!response || !response.ok) {
      toast((response && response.error) || "保存失败", "error");
      return;
    }
    state.settings = { ...(state.settings || {}), trial_server: response.server };
    renderTrial(response);
    toast(value ? "试用服务地址已保存" : "已清空试用服务地址", "success");
  }

  async function trialApplyCode() {
    const input = $("#trialCodeInput");
    const value = input.value.trim();
    if (!value) {
      input.focus();
      toast("请输入序列号", "error");
      return;
    }
    const response = await call("trial_apply", value);
    if (!response || !response.ok) {
      toast((response && response.error) || "激活失败", "error");
      return;
    }
    renderTrial(response);
    if (response.license) renderLicense(response.license);
    else await refreshLicense();
    $("#trialCodeRow").hidden = true;
    input.value = "";
    toast("激活成功", "success");
  }

  async function copySerial() {
    const value = $("#trialSerial").textContent.trim();
    if (!value || value === "—") return;
    try {
      await navigator.clipboard.writeText(value);
      toast("序列号已复制", "success");
    } catch (error) {
      toast("复制失败，请手动选择文本", "error");
    }
  }

  async function refreshAll() {
    await refreshLicense();
    await refreshTrial({ silent: true });
  }

  window.pyshieldTrialRefresh = () => {
    refreshAll();
  };

  // ---------------------------------------------------------------- navigation

  function switchView(view) {
    if (!VIEW_META[view]) return;
    state.view = view;
    $$(".nav-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.view === view);
    });
    $$(".view").forEach((section) => {
      section.hidden = section.id !== `view-${view}`;
    });
    const meta = VIEW_META[view];
    $("#pageTitle").textContent = meta.title;
    $("#pageSubtitle").textContent = meta.subtitle;
    $("#pagePill").textContent = meta.pill;
    if (view === "history") refreshHistory();
    if (view === "settings") {
      syncSettingsForm();
      refreshLicense();
    }
    $(".content").scrollTop = 0;
  }

  // ---------------------------------------------------------------- window

  async function windowAction(action) {
    if (action === "close" && state.busy) {
      toast("构建进行中，请先取消或等待完成", "error");
      return;
    }
    if (action === "maximize") {
      const response = await call("window_action", "maximize", state.maximized);
      if (!response.ok) {
        if (response.error) toast(response.error, "error");
        return;
      }
      state.maximized = !state.maximized;
      $("#maxBtn").innerHTML = state.maximized
        ? '<svg class="icon"><use href="#i-restore"/></svg>'
        : '<svg class="icon"><use href="#i-maximize"/></svg>';
      $("#maxBtn").title = state.maximized ? "还原" : "最大化";
      return;
    }
    const response = await call("window_action", action);
    if (!response.ok && response.error) toast(response.error, "error");
  }

  // ---------------------------------------------------------------- stats

  function applyLatestSummary() {
    const success = state.history.find((entry) => entry.status === "success");
    if (!success) return;
    $("#statModules").textContent = success.modules == null ? "—" : String(success.modules);
    $("#statSize").textContent = success.size_text || "—";
    $("#statElapsed").textContent = success.elapsed == null ? "—" : `${success.elapsed}s`;
    $("#statLicense").textContent = success.expires || "永久";
    $("#statSizeHint").textContent = "最近一次构建输出";
    $("#statElapsedHint").textContent = "最近一次构建耗时";
    $("#statLicenseHint").textContent = "最近一次构建签发";
  }

  // ---------------------------------------------------------------- boot

  function fillAbout() {
    if (!state.app) return;
    $("#appVersion").textContent = state.app.version;
    $("#appPython").textContent = state.app.python;
    $("#aboutVersion").textContent = `v${state.app.version}`;
    $("#infoVersion").textContent = state.app.version;
    $("#infoPython").textContent = state.app.python;
    $("#infoPythonMinor").textContent = state.app.python_minor;
    $("#infoConfig").textContent = state.app.config_dir;
    $("#configDir").textContent = state.app.config_dir;
    $("#infoMachine").textContent = state.machine || "—";
  }

  function applyDefaultsToForm() {
    const settings = state.settings || {};
    setSegment("cipherGroup", settings.cipher || "chacha-pure");
    setSegment("integrityGroup", settings.integrity || "protected");
    setSegment("runtimeGroup", settings.runtime || "pyc");
    setSegment("expiryGroup", "never");
    setSegment("bindingGroup", "any");
    $("#noVerify").checked = settings.no_verify === true;
    $("#suffixHint").textContent = settings.output_suffix || "-shielded";
    syncExpiryFields();
    syncBindingFields();
  }

  async function boot() {
    const ready = await bridgeReady();
    if (!ready && !state.preview) {
      toast("无法连接本地构建引擎，请重新启动应用", "error");
      return;
    }
    const response = await call("bootstrap");
    if (!response.ok) {
      toast(response.error || "初始化失败", "error");
      return;
    }
    state.app = response.app;
    state.settings = response.settings;
    state.machine = response.machine || "";
    $("#machineFingerprint").value = state.machine;
    renderLicense(response.license || null);
    renderTrial({ trial: response.trial });
    maybeShowTrialEnded(response.license);
    const initialView = new URLSearchParams(location.search).get("view");
    if (initialView && VIEW_META[initialView]) {
      switchView(initialView);
    } else if (response.license && response.license.mode === "unlicensed") {
      switchView("trial");
    }
    applyDefaultsToForm();
    applyEditionLimits(state.license);
    fillAbout();
    renderHistory(response.history || []);
    applyLatestSummary();

    const latest = (response.history || []).find((entry) => entry.project);
    if (latest && latest.project) {
      $("#projectPath").value = latest.project;
      const suffix = response.settings.output_suffix || "-shielded";
      $("#outputPath").value = `${latest.project.replace(/[\\/]+$/, "")}${suffix}`;
      state.outputAuto = true;
    } else if (state.preview) {
      $("#projectPath").value = "E:\\projects\\erp-portal";
      $("#outputPath").value = "E:\\projects\\erp-portal-shielded";
      state.outputAuto = true;
    }

    const snapshot = response.state || {};
    if (snapshot.status === "running" && snapshot.started) {
      setBusy(true);
      state.lastStatus = "running";
      renderProgress(snapshot);
      renderLogs(snapshot.logs);
      pollBuild();
    } else if (snapshot.summary && snapshot.status === "success") {
      state.lastSummary = snapshot.summary;
      state.lastStatus = "success";
    }

    syncSummary();
    await inspectNow();
  }

  // ---------------------------------------------------------------- preview

  const previewState = { percent: 0, logs: [], timer: null };

  function previewSummary() {
    return {
      project: "E:\\projects\\erp-portal",
      output: "E:\\projects\\erp-portal-shielded",
      secret: "E:\\projects\\erp-portal-shielded.shield-secret.json",
      license: "E:\\projects\\erp-portal-shielded\\pyshield.lic",
      build_id: "9f3c1a72b0d84e51",
      modules: 48,
      cipher: "chacha",
      integrity: "protected",
      runtime: "pyc",
      elapsed: 12.6,
      size: 2145892,
      size_text: "2.0 MB",
      expires: "永久",
      binding: "any",
      customer: null,
    };
  }

  function mockLicense() {
    const base = {
      machine: "9f3c1a72b0d84e51c4a201e57d8b16a3",
      license_path: "C:////Users////demo////AppData////Roaming////PyShield////tool-license.json",
      public_key_configured: true,
      license_id: "TRIAL-PS-7Q4M-2XK9-TL5B",
      customer: "试用用户",
      edition: "trial",
      features: ["gui"],
      expires: "2026-11-22",
      grace_days: 0,
      trial: null,
      checked: new Date().toISOString(),
    };
    if (state.previewState === "unlicensed") {
      return {
        ...base,
        edition: null,
        license_id: null,
        customer: null,
        features: [],
        expires: null,
        mode: "unlicensed",
        valid: false,
        code: "not_activated",
        reason: "尚未激活：请先在「版本与试用」中申请试用或导入授权",
        public_key_configured: false,
        quota: null,
      };
    }
    if (state.previewState === "exhausted") {
      return {
        ...base,
        mode: "invalid",
        valid: false,
        code: "quota_exhausted",
        reason: "trial build limit reached (3/3); purchase a Pro license to keep building",
        quota: { used: 3, limit: 3 },
      };
    }
    return {
      ...base,
      mode: "licensed",
      valid: true,
      reason: "valid; 2 builds left this month",
      quota: { used: 1, limit: 3 },
    };
  }

  function mockTrial() {
    const trial = {
      ok: true,
      serial: "PS-7Q4M-2XK9-TL5B",
      machine: "9f3c1a72b0d84e51c4a201e57d8b16a3",
      created: "2026-11-08T10:00:00+00:00",
      server: "https://license.pyshield.dev",
      edition: "trial",
      mode: "licensed",
      trial: true,
      active: true,
      expires: "2026-11-22",
      days_left: 9,
      builds_left: 2,
      build_limit: 3,
      license_id: "TRIAL-PS-7Q4M-2XK9-TL5B",
      protocol: true,
      deep_link: "pyshield://activate?serial=PS-7Q4M-2XK9-TL5B",
    };
    if (state.previewState === "unlicensed") {
      trial.mode = "unlicensed";
      trial.active = false;
      trial.trial = false;
      trial.edition = null;
      trial.server = "http://127.0.0.1:8090";
      trial.expires = null;
      trial.days_left = null;
      trial.builds_left = null;
      trial.build_limit = null;
    }
    if (state.previewState === "exhausted") {
      trial.mode = "invalid";
      trial.active = false;
      trial.builds_left = 0;
    }
    return trial;
  }

  function mockApi(name, args) {
    if (name === "bootstrap") {
      const history = [
        {
          id: "1",
          created: new Date().toISOString(),
          status: "success",
          project: "E:\\projects\\erp-portal",
          project_name: "erp-portal",
          output: "E:\\projects\\erp-portal-shielded",
          secret: "E:\\projects\\erp-portal-shielded.shield-secret.json",
          cipher: "chacha",
          integrity: "protected",
          runtime: "pyc",
          modules: 48,
          elapsed: 12.6,
          size_text: "2.0 MB",
          expires: "永久",
        },
        {
          id: "2",
          created: new Date(Date.now() - 86400000).toISOString(),
          status: "success",
          project: "E:\\projects\\crm-suite",
          project_name: "crm-suite",
          output: "E:\\projects\\crm-suite-shielded",
          secret: "E:\\projects\\crm-suite-shielded.shield-secret.json",
          cipher: "chacha-pure",
          integrity: "full",
          runtime: "pyc",
          modules: 71,
          elapsed: 18.2,
          size_text: "3.4 MB",
          expires: "2027-06-30",
        },
      ];
      return Promise.resolve({
        ok: true,
        app: {
          title: "PyShield 加密构建器",
          version: "0.6.0",
          python: "3.12.0",
          python_minor: "3.12",
          config_dir: "C:\\Users\\demo\\AppData\\Roaming\\PyShieldBuilder",
          platform: "win32",
        },
        defaults: {
          cipher: "chacha-pure",
          integrity: "protected",
          runtime: "pyc",
          output_suffix: "-shielded",
          auto_detect_python: true,
          no_verify: false,
        },
        settings: {
          cipher: "chacha-pure",
          integrity: "protected",
          runtime: "pyc",
          output_suffix: "-shielded",
          auto_detect_python: true,
          no_verify: false,
          activation_server: "",
          trial_server: "https://license.pyshield.dev",
        },
        history: history,
        machine: "9f3c1a72b0d84e51c4a201e57d8b16a3",
        trial: mockTrial(),
        license: mockLicense(),
        state: { status: "idle", percent: 0, logs: [] },
      });
    }
    if (name === "license_status") {
      return Promise.resolve({
        ok: true,
        server: "",
        license: mockLicense(),
      });
    }
    if (name === "browse_license") {
      return Promise.resolve({ ok: true, path: "C:\\Users\\demo\\Downloads\\pyshield-tool-license.json" });
    }
    if (name === "license_install" || name === "license_activate" || name === "license_apply") {
      return Promise.resolve({ ok: true, license: null, license_id: "PS-2026-0042" });
    }
    if (name === "license_request") {
      return Promise.resolve({ ok: true, path: "C:\\Users\\demo\\AppData\\Roaming\\PyShield\\activation-request.json" });
    }
    if (name === "license_deactivate") {
      return Promise.resolve({ ok: true, removed: true, license: null });
    }
    if (name === "license_heartbeat") {
      return Promise.resolve({ ok: true, heartbeat: { status: "active" } });
    }
    if (name === "license_revocations") {
      return Promise.resolve({ ok: true, revoked: 0 });
    }
    if (name === "trial_state") {
      return Promise.resolve({ ok: true, trial: mockTrial() });
    }
    if (name === "trial_open") {
      return Promise.resolve({
        ok: true,
        url: "https://license.pyshield.dev/activate?serial=PS-7Q4M-2XK9-TL5B",
        trial: mockTrial(),
      });
    }
    if (name === "trial_set_server") {
      const server = String((args && args[0]) || "");
      return Promise.resolve({
        ok: true,
        server,
        trial: { ...mockTrial(), server: server || mockTrial().server },
      });
    }
    if (name === "trial_poll") {
      return Promise.resolve({ ok: true, granted: false, status: "pending", trial: mockTrial() });
    }
    if (name === "trial_apply") {
      return Promise.resolve({ ok: true, granted: true, trial: mockTrial(), license: null });
    }
    if (name === "inspect") {
      const payload = args[0] || {};
      return Promise.resolve({
        ok: true,
        errors: [],
        project: {
          valid: Boolean(payload.project),
          name: "erp-portal",
          modules: 48,
          packages: ["accounts", "orders", "billing", "portal"],
        },
        output: payload.output || "",
        verify: null,
        verify_error: null,
        suggested_verify: {
          executable: "E:\\projects\\erp-portal\\.venv\\Scripts\\python.exe",
          summary: "Python 3.12.0 · Django 6.1.3 · cryptography 50.0.2",
        },
        machine: "9f3c1a72b0d84e51c4a201e57d8b16a3",
      });
    }
    if (name === "start_build") {
      previewState.percent = 0;
      previewState.logs = [
        "10:24:01  pyshield 0.6.0 on CPython 3.12",
        "10:24:01  project:  E:\\projects\\erp-portal",
        "10:24:01  output:   E:\\projects\\erp-portal-shielded",
        "10:24:01  cipher:   chacha",
        "10:24:01  modules:  48",
      ];
      clearInterval(previewState.timer);
      previewState.timer = setInterval(() => {
        previewState.percent = Math.min(100, previewState.percent + 8);
        if (previewState.percent % 24 === 0) {
          previewState.logs.push(`10:24:02    protected apps/${previewState.percent}/module.py`);
        }
      }, 300);
      return Promise.resolve({ ok: true, note: "自动检测到校验解释器：.venv\\Scripts\\python.exe" });
    }
    if (name === "snapshot") {
      const done = previewState.percent >= 100;
      if (done) {
        clearInterval(previewState.timer);
        if (!previewState.logs.some((line) => line.includes("done:"))) {
          previewState.logs.push(
            "10:24:14  done: E:\\projects\\erp-portal-shielded",
            "10:24:14  build complete in 12.6s"
          );
        }
      }
      return Promise.resolve({
        ok: true,
        state: {
          status: done ? "success" : "running",
          stage: done ? "done" : "encrypt",
          stage_label: done ? "构建完成" : "加密 Python 模块",
          percent: previewState.percent,
          logs: previewState.logs.slice(),
          error: null,
          summary: done ? previewSummary() : null,
        },
      });
    }
    if (name === "get_history") {
      return Promise.resolve({ ok: true, history: [] });
    }
    if (name === "browse_project") {
      return Promise.resolve({
        ok: true,
        project: "E:\\projects\\erp-portal",
        output: "E:\\projects\\erp-portal-shielded",
      });
    }
    if (name === "browse_output") {
      return Promise.resolve({ ok: true, output: "E:\\projects\\erp-portal-shielded" });
    }
    if (name === "browse_python") {
      return Promise.resolve({
        ok: true,
        python: {
          executable: "E:\\projects\\erp-portal\\.venv\\Scripts\\python.exe",
          version: "3.12.0",
          summary: "Python 3.12.0 · Django 6.1.3",
        },
      });
    }
    if (name === "fingerprint") {
      return Promise.resolve({ ok: true, machine: "9f3c1a72b0d84e51c4a201e57d8b16a3" });
    }
    if (name === "save_settings") {
      return Promise.resolve({ ok: true, settings: args[0] || {} });
    }
    if (name === "reveal") return Promise.resolve({ ok: true });
    if (name === "window_action") return Promise.resolve({ ok: true });
    if (name === "cancel_build") return Promise.resolve({ ok: true });
    if (name === "clear_history") return Promise.resolve({ ok: true, history: [] });
    return Promise.resolve({ ok: false, error: `预览模式未实现 ${name}` });
  }

  // ---------------------------------------------------------------- events

  function bindEvents() {
    onSegment("cipherGroup", (value) => {
      if (isTrialEdition(state.license) && value !== "chacha-pure") {
        setSegment("cipherGroup", "chacha-pure");
        toast("试用版只能使用 ChaCha20 纯 Python", "error");
      }
    });
    onSegment("runtimeGroup", null);
    onSegment("integrityGroup", (value) => {
      if (isTrialEdition(state.license) && value === "full") {
        setSegment("integrityGroup", "protected");
        toast("试用版不支持完整校验清单", "error");
      }
    });
    onSegment("expiryGroup", () => {
      syncExpiryFields();
      syncSummary();
    });
    onSegment("bindingGroup", () => {
      syncBindingFields();
      syncSummary();
    });

    $("#projectPath").addEventListener("input", onProjectInput);
    $("#outputPath").addEventListener("input", () => {
      state.outputAuto = false;
      syncSummary();
      scheduleInspect();
    });
    $("#verifyPython").addEventListener("input", () => {
      state.verifyAuto = false;
      state.verifyWasAuto = false;
      syncSummary();
      scheduleInspect();
    });
    [
      "#expireDays",
      "#expireDate",
      "#graceDays",
      "#customer",
      "#bindDevice",
      "#encryptMigrations",
      "#encryptTests",
      "#noVerify",
      "#forceOverwrite",
      "#mutablePatterns",
      "#excludes",
    ].forEach((selector) => {
      const node = $(selector);
      if (!node) return;
      node.addEventListener("change", () => {
        syncSummary();
        if (["#encryptMigrations", "#encryptTests", "#excludes"].includes(selector)) {
          scheduleInspect();
        }
      });
      node.addEventListener("input", syncSummary);
    });

    $("#browseProject").addEventListener("click", browseProject);
    $("#browseOutput").addEventListener("click", browseOutput);
    $("#browsePython").addEventListener("click", browsePython);
    $("#copyFingerprint").addEventListener("click", copyFingerprint);
    $("#useMachine").addEventListener("click", useMachine);
    $("#startBtn").addEventListener("click", startBuild);
    $("#cancelBtn").addEventListener("click", cancelBuild);
    $("#openOutput").addEventListener("click", () => {
      if (state.lastSummary) reveal(state.lastSummary.output);
    });
    $("#openSecret").addEventListener("click", () => {
      if (state.lastSummary) reveal(state.lastSummary.secret);
    });
    $("#clearLog").addEventListener("click", () => {
      resetTerminal();
      const placeholder = document.createElement("span");
      placeholder.className = "terminal-placeholder";
      placeholder.textContent = state.busy ? "构建日志继续输出…" : "等待构建任务…";
      $("#terminal").appendChild(placeholder);
    });

    $("#advancedToggle").addEventListener("click", () => {
      const body = $("#advancedBody");
      const open = body.hidden;
      body.hidden = !open;
      $("#advancedToggle").classList.toggle("is-open", open);
      $("#advancedToggle").setAttribute("aria-expanded", open ? "true" : "false");
    });

    $$(".nav-item").forEach((item) => {
      item.addEventListener("click", () => switchView(item.dataset.view));
    });
    $("#emptyGoBuild").addEventListener("click", () => switchView("workspace"));
    $("#clearHistory").addEventListener("click", clearHistory);

    $$(".win-btn").forEach((button) => {
      button.addEventListener("click", () => windowAction(button.dataset.window));
    });
    $(".topbar-title").addEventListener("dblclick", () => windowAction("maximize"));

    $("#refreshBtn").addEventListener("click", async () => {
      $("#refreshBtn").classList.add("is-spinning");
      const response = await call("fingerprint");
      if (response.ok && response.machine) {
        state.machine = response.machine;
        $("#machineFingerprint").value = response.machine;
        $("#infoMachine").textContent = response.machine;
      }
      await inspectNow();
      await refreshHistory();
      await refreshLicense();
      setTimeout(() => $("#refreshBtn").classList.remove("is-spinning"), 300);
      toast("环境状态已刷新");
    });

    $("#saveSettings").addEventListener("click", saveSettings);
    $("#licensePill").addEventListener("click", () => switchView("settings"));
    $("#licenseInstall").addEventListener("click", licenseInstall);
    $("#licenseActivate").addEventListener("click", licenseActivate);
    $("#licenseRequest").addEventListener("click", licenseRequest);
    $("#licenseApply").addEventListener("click", licenseApply);
    $("#licenseHeartbeat").addEventListener("click", licenseHeartbeat);
    $("#licenseRevocations").addEventListener("click", licenseRevocations);
    $("#licenseDeactivate").addEventListener("click", licenseDeactivate);
    $("#trialSaveServer").addEventListener("click", trialSaveServer);
    $("#trialRefresh").addEventListener("click", async () => {
      const response = await refreshTrial();
      if (response && response.ok) toast("授权状态已刷新", "success");
    });
    $("#trialCopySerial").addEventListener("click", copySerial);
    $$("[data-plan-open]").forEach((button) => {
      button.addEventListener("click", () => trialOpen(button.dataset.planOpen || "trial"));
    });
    $("#trialApplyCode").addEventListener("click", () => {
      const row = $("#trialCodeRow");
      row.hidden = !row.hidden;
      if (!row.hidden) $("#trialCodeInput").focus();
    });
    $("#trialCodeSubmit").addEventListener("click", trialApplyCode);
    $("#trialCodeInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") trialApplyCode();
    });
    $("#trialBannerAction").addEventListener("click", () => switchView("trial"));
    $("#trialEndedBuy").addEventListener("click", () => {
      $("#trialEndedModal").hidden = true;
      trialOpen("pro");
    });
    $("#trialEndedLater").addEventListener("click", () => {
      $("#trialEndedModal").hidden = true;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.busy) {
        event.preventDefault();
        cancelBuild();
      }
    });
  }

  bindEvents();
  boot();
})();

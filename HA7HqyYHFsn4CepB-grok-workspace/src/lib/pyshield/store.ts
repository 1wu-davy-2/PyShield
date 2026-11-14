import { create } from "zustand";
import {
  type Algo,
  type BuildPhase,
  type HistoryItem,
  type Integrity,
  type KeyFor,
  type LicenseKind,
  type ModalKind,
  type NavPage,
  type PresetId,
  type RuntimeForm,
  type Term,
  type Toast,
  type ToastTone,
  type WebPage,
  OK_LOGS,
  PROJECTS,
  RUN_LOGS,
  SAMPLE_HISTORY,
  SERIAL,
  STAGES,
  algoLabel,
  estimateMb,
  estimateSec,
  formatSec,
  lockMessage,
  proUnlocked,
  projectById,
} from "@/lib/pyshield/model";

export type Data = {
  presetId: PresetId | null;
  surface: "desktop" | "web";
  web: WebPage;
  contactPlan: "pro" | "enterprise";
  page: NavPage;
  license: LicenseKind;
  daysLeft: number;
  buildsLeft: number;
  monthUsed: number;
  serviceUp: boolean;
  devEnv: boolean;
  waiting: boolean;
  pendingGrant: boolean;
  autoReturn: boolean;
  modal: ModalKind | null;
  minimized: boolean;
  maximized: boolean;
  projectId: string | null;
  outputDir: string;
  interpreter: string;
  algo: Algo;
  runtime: RuntimeForm;
  integrity: Integrity;
  term: Term;
  termDays: number;
  termDate: string;
  customer: string;
  bindDevice: boolean;
  advancedOpen: boolean;
  skipCheck: boolean;
  keepBackup: boolean;
  exclude: string;
  obfuscate: boolean;
  phase: BuildPhase;
  progress: number;
  stageLabel: string;
  logs: string[];
  elapsed: number;
  failNext: boolean;
  error: string | null;
  history: HistoryItem[];
  activateOpen: boolean;
  activateCode: string;
  serviceUrl: string;
  defAlgo: Algo;
  defRuntime: RuntimeForm;
  defIntegrity: Integrity;
  defSkip: boolean;
  suffix: string;
  autoDetect: boolean;
  keyFor: KeyFor | null;
  toasts: Toast[];
};

type Actions = {
  toast: (text: string, tone?: ToastTone) => void;
  dismissToast: (id: number) => void;
  loadPreset: (id: PresetId) => void;
  setPage: (page: NavPage) => void;
  setLicenseKind: (kind: LicenseKind) => void;
  toggleService: () => void;
  toggleFailNext: () => void;
  refreshLicense: () => void;
  copyText: (text: string, ok: string) => void;
  openApply: () => void;
  openPlan: (plan: "pro" | "enterprise") => void;
  submitTrial: () => void;
  activateFromSite: () => void;
  backToDesktop: () => void;
  setWeb: (web: WebPage) => void;
  toggleActivate: () => void;
  setActivateCode: (value: string) => void;
  submitCode: () => void;
  setServiceUrl: (value: string) => void;
  saveServiceUrl: () => void;
  openPicker: () => void;
  openOutputPicker: () => void;
  selectProject: (id: string) => void;
  chooseOutput: (dir: string) => void;
  setOutputDir: (dir: string) => void;
  setInterpreter: (value: string) => void;
  setAlgo: (algo: Algo) => void;
  setRuntime: (runtime: RuntimeForm) => void;
  setIntegrity: (integrity: Integrity) => void;
  setTerm: (term: Term) => void;
  setTermDays: (days: number) => void;
  setTermDate: (date: string) => void;
  setCustomer: (value: string) => void;
  toggleBind: () => void;
  toggleAdvanced: () => void;
  toggleSkip: () => void;
  toggleBackup: () => void;
  setExclude: (value: string) => void;
  toggleObfuscate: () => void;
  startBuild: () => void;
  cancelBuild: () => void;
  openOutput: (path: string, ok: boolean) => void;
  openKey: (item?: HistoryItem) => void;
  closeModal: () => void;
  askClear: () => void;
  clearHistory: () => void;
  setDefAlgo: (algo: Algo) => void;
  setDefRuntime: (runtime: RuntimeForm) => void;
  setDefIntegrity: (integrity: Integrity) => void;
  setSuffix: (value: string) => void;
  toggleDefAuto: () => void;
  toggleDefSkip: () => void;
  saveDefaults: () => void;
  openImport: () => void;
  importLicense: (which: "pro" | "enterprise") => void;
  openOffline: () => void;
  applyOffline: (text: string) => void;
  heartbeat: () => void;
  syncRevoke: () => void;
  askRelease: () => void;
  releaseSeat: () => void;
  goOnlineActivate: () => void;
  minimize: () => void;
  toggleMax: () => void;
  closeWindow: () => void;
  restore: () => void;
  bannerAction: () => void;
};

export type Store = Data & Actions;

let tick: number | null = null;
let toastSeq = 1;

function stopTimer() {
  if (tick != null) {
    window.clearInterval(tick);
    tick = null;
  }
}

function fresh(): Data {
  return {
    presetId: "lic-off",
    surface: "desktop",
    web: "activate",
    contactPlan: "pro",
    page: "license",
    license: "inactive",
    daysLeft: 0,
    buildsLeft: 0,
    monthUsed: 9,
    serviceUp: true,
    devEnv: false,
    waiting: false,
    pendingGrant: false,
    autoReturn: false,
    modal: null,
    minimized: false,
    maximized: false,
    projectId: null,
    outputDir: "",
    interpreter: "auto",
    algo: "chacha20",
    runtime: "source",
    integrity: "core",
    term: "forever",
    termDays: 365,
    termDate: "2027-11-13",
    customer: "",
    bindDevice: true,
    advancedOpen: false,
    skipCheck: false,
    keepBackup: false,
    exclude: "__pycache__, migrations, tests",
    obfuscate: false,
    phase: "idle",
    progress: 0,
    stageLabel: "",
    logs: [],
    elapsed: 0,
    failNext: false,
    error: null,
    history: [],
    activateOpen: false,
    activateCode: "",
    serviceUrl: "http://127.0.0.1:8090",
    defAlgo: "chacha20",
    defRuntime: "source",
    defIntegrity: "core",
    defSkip: false,
    suffix: "-shielded",
    autoDetect: true,
    keyFor: null,
    toasts: [],
  };
}

function stamp() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function nowLabel() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

function trialDesk(): Partial<Data> {
  return { license: "trial", daysLeft: 12, buildsLeft: 2, page: "license", surface: "desktop" };
}

function withProject(): Partial<Data> {
  return {
    projectId: "erp",
    outputDir: "E:\\proj\\erp-shielded",
    customer: "",
  };
}

export const usePyshield = create<Store>((set, get) => {
  const toast = (text: string, tone: ToastTone = "info") => {
    const id = toastSeq++;
    set((state) => ({ toasts: [...state.toasts.slice(-2), { id, text, tone }] }));
    window.setTimeout(() => {
      set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) }));
    }, 3200);
  };

  const busy = () => {
    const phase = get().phase;
    return phase === "running" || phase === "cancelling";
  };

  const guardEdit = () => {
    if (!busy()) return false;
    toast("构建进行中，无法修改选项", "warn");
    return true;
  };

  const rejectLocked = (feature: "algo" | "runtime" | "full" | "obfuscate") => {
    toast(lockMessage(get().license, feature), "warn");
  };

  const pushHistory = (item: HistoryItem) => {
    set((state) => ({ history: [item, ...state.history].slice(0, 100) }));
  };

  const finishCancelled = () => {
    const state = get();
    const project = projectById(state.projectId);
    stopTimer();
    const item: HistoryItem = {
      id: `c-${Date.now()}`,
      time: nowLabel(),
      project: project?.name ?? "未命名",
      path: state.outputDir,
      status: "cancelled",
      modules: project?.modules ?? 0,
      size: "—",
      duration: formatSec(state.elapsed),
      algo: state.algo,
    };
    set({
      phase: "cancelled",
      stageLabel: "已取消",
      error: null,
      logs: [...get().logs, `${stamp()}  构建已取消，输出目录未发布`],
    });
    pushHistory(item);
    toast("构建已取消", "warn");
  };

  return {
    ...fresh(),
    toast,
    dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })),
    loadPreset: (id) => {
      stopTimer();
      const next = fresh();
      next.presetId = id;
      if (id === "lic-trial") Object.assign(next, trialDesk());
      if (id === "build") {
        Object.assign(next, trialDesk(), withProject(), { page: "build" });
      }
      if (id === "running") {
        Object.assign(next, trialDesk(), withProject(), {
          page: "build",
          phase: "running",
          progress: 66,
          stageLabel: "安装运行时",
          logs: RUN_LOGS,
          elapsed: 8.3,
        });
      }
      if (id === "success") {
        Object.assign(next, trialDesk(), withProject(), {
          page: "build",
          phase: "success",
          progress: 100,
          stageLabel: "完成",
          logs: OK_LOGS,
          elapsed: 12.6,
          history: SAMPLE_HISTORY,
        });
      }
      if (id === "history") {
        Object.assign(next, trialDesk(), { page: "history", history: SAMPLE_HISTORY });
      }
      if (id === "empty") Object.assign(next, trialDesk(), { page: "history", history: [] });
      if (id === "settings") Object.assign(next, trialDesk(), { page: "settings" });
      if (id === "about") Object.assign(next, trialDesk(), { page: "about" });
      if (id === "modal") {
        Object.assign(next, {
          page: "license",
          license: "expired",
          daysLeft: 0,
          buildsLeft: 0,
          modal: "trial-ended",
          surface: "desktop",
        });
      }
      if (id === "web") {
        Object.assign(next, { surface: "web", web: "activate", license: "inactive" });
      }
      if (id === "web-ok") {
        Object.assign(next, {
          surface: "web",
          web: "success",
          pendingGrant: true,
          autoReturn: false,
          license: "inactive",
        });
      }
      if (id === "web-pro") {
        Object.assign(next, { surface: "web", web: "contact", contactPlan: "pro" });
      }
      if (id === "web-ent") {
        Object.assign(next, { surface: "web", web: "contact", contactPlan: "enterprise" });
      }
      if (id === "web-home") Object.assign(next, { surface: "web", web: "home" });
      set(next);
    },
    setPage: (page) => set({ page, surface: "desktop", presetId: null, minimized: false }),
    setLicenseKind: (kind) => {
      set({
        license: kind,
        devEnv: kind === "dev",
        daysLeft: kind === "trial" ? 12 : kind === "pro" || kind === "enterprise" ? 364 : 0,
        buildsLeft: kind === "trial" ? 2 : 0,
        modal: null,
        presetId: null,
      });
    },
    toggleService: () => set((state) => ({ serviceUp: !state.serviceUp })),
    toggleFailNext: () => set((state) => ({ failNext: !state.failNext })),
    refreshLicense: () => {
      const state = get();
      if (state.pendingGrant) {
        get().activateFromSite();
        return;
      }
      if (!state.serviceUp) {
        toast("授权服务未配置，暂时无法刷新", "warn");
        return;
      }
      toast("授权状态已刷新", "ok");
    },
    copyText: async (text, ok) => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        /* clipboard may be blocked in the preview; still confirm */
      }
      toast(ok, "ok");
    },
    openApply: () => {
      const state = get();
      if (!state.serviceUp) {
        toast("授权服务未配置，暂时无法在线申请", "warn");
        return;
      }
      const already = state.license === "trial" || proUnlocked(state.license);
      set({
        surface: "web",
        web: "activate",
        waiting: !already,
        pendingGrant: false,
        autoReturn: false,
        presetId: null,
        minimized: false,
      });
      toast("已打开浏览器，请在网页上完成申请", "info");
    },
    openPlan: (plan) => {
      if (!get().serviceUp) {
        toast("授权服务未配置，暂时无法在线申请", "warn");
        return;
      }
      set({
        surface: "web",
        web: "contact",
        contactPlan: plan,
        waiting: false,
        presetId: null,
      });
      toast("已打开浏览器", "info");
    },
    submitTrial: () => {
      if (!get().serviceUp) return;
      set({ pendingGrant: true, autoReturn: true, web: "success", waiting: true });
    },
    activateFromSite: () => {
      if (!get().pendingGrant) return;
      stopTimer();
      set({
        license: "trial",
        daysLeft: 14,
        buildsLeft: 3,
        waiting: false,
        pendingGrant: false,
        autoReturn: false,
        surface: "desktop",
        page: "license",
        modal: null,
        minimized: false,
        presetId: null,
      });
      toast("试用授权已激活", "ok");
    },
    backToDesktop: () => set({ surface: "desktop", minimized: false }),
    setWeb: (web) => set({ web, surface: "web" }),
    toggleActivate: () => set((state) => ({ activateOpen: !state.activateOpen })),
    setActivateCode: (value) => set({ activateCode: value.toUpperCase() }),
    submitCode: () => {
      const code = get().activateCode.trim().toUpperCase();
      if (code === "PS-PRO0-ACME-2604") {
        set({ license: "pro", daysLeft: 364, buildsLeft: 0, modal: null, activateOpen: false });
        toast("专业版授权已激活", "ok");
        return;
      }
      if (code === "PS-ENT0-NRTH-0091") {
        set({ license: "enterprise", daysLeft: 364, buildsLeft: 0, modal: null, activateOpen: false });
        toast("企业版授权已激活", "ok");
        return;
      }
      if (code === SERIAL) {
        set({
          license: "trial",
          daysLeft: 14,
          buildsLeft: 3,
          activateOpen: false,
          pendingGrant: false,
          waiting: false,
        });
        toast("试用授权已激活", "ok");
        return;
      }
      toast("激活码无效，或与本机序列号不符", "danger");
    },
    setServiceUrl: (value) => set({ serviceUrl: value }),
    saveServiceUrl: () => toast("试用服务地址已保存", "ok"),
    openPicker: () => {
      if (guardEdit()) return;
      set({ modal: "picker" });
    },
    openOutputPicker: () => {
      if (guardEdit()) return;
      if (!get().projectId) {
        toast("请先选择项目目录", "warn");
        return;
      }
      set({ modal: "output" });
    },
    selectProject: (id) => {
      const project = PROJECTS.find((item) => item.id === id);
      if (!project) return;
      if (!project.django) {
        toast("这个目录不是 Django 项目，没有 manage.py", "danger");
        return;
      }
      set({
        projectId: project.id,
        outputDir: `${project.path}${get().suffix}`,
        modal: null,
        phase: "idle",
        progress: 0,
        logs: [],
        error: null,
        stageLabel: "",
        elapsed: 0,
      });
      toast(`已选择 ${project.name}，共 ${project.modules} 个模块`, "ok");
    },
    chooseOutput: (dir) => {
      set({ outputDir: dir, modal: null });
      toast("输出目录已更新", "ok");
    },
    setOutputDir: (dir) => {
      if (guardEdit()) return;
      set({ outputDir: dir });
    },
    setInterpreter: (value) => {
      if (guardEdit()) return;
      set({ interpreter: value });
    },
    setAlgo: (algo) => {
      if (guardEdit()) return;
      if (algo !== "chacha20" && !proUnlocked(get().license)) {
        rejectLocked("algo");
        return;
      }
      set({ algo });
    },
    setRuntime: (runtime) => {
      if (guardEdit()) return;
      if (runtime === "bytecode" && !proUnlocked(get().license)) {
        rejectLocked("runtime");
        return;
      }
      set({ runtime });
    },
    setIntegrity: (integrity) => {
      if (guardEdit()) return;
      if (integrity === "full" && !proUnlocked(get().license)) {
        rejectLocked("full");
        return;
      }
      set({ integrity });
    },
    setTerm: (term) => {
      if (guardEdit()) return;
      set({ term });
    },
    setTermDays: (days) => set({ termDays: days }),
    setTermDate: (date) => set({ termDate: date }),
    setCustomer: (value) => set({ customer: value }),
    toggleBind: () => set((state) => ({ bindDevice: !state.bindDevice })),
    toggleAdvanced: () => set((state) => ({ advancedOpen: !state.advancedOpen })),
    toggleSkip: () => set((state) => ({ skipCheck: !state.skipCheck })),
    toggleBackup: () => set((state) => ({ keepBackup: !state.keepBackup })),
    setExclude: (value) => set({ exclude: value }),
    toggleObfuscate: () => {
      if (!proUnlocked(get().license)) {
        rejectLocked("obfuscate");
        return;
      }
      set((state) => ({ obfuscate: !state.obfuscate }));
    },
    startBuild: () => {
      const state = get();
      if (state.phase === "running" || state.phase === "cancelling") return;
      if (state.license === "inactive") {
        set({ page: "license", surface: "desktop" });
        toast("请先申请试用或导入授权", "warn");
        return;
      }
      if (state.license === "expired" || (state.license === "trial" && state.buildsLeft <= 0)) {
        set({ license: "expired", buildsLeft: 0, daysLeft: 0, modal: "trial-ended" });
        return;
      }
      const project = projectById(state.projectId);
      if (!project) {
        toast("请先选择 Django 项目目录", "warn");
        return;
      }
      stopTimer();
      const target = estimateSec(project.modules, state.algo);
      set({
        phase: "running",
        progress: 0,
        stageLabel: "准备",
        logs: [`${stamp()}  开始加密 ${project.name}`],
        elapsed: 0,
        error: null,
        page: "build",
      });
      let index = 0;
      let cancelArmed = false;
      tick = window.setInterval(() => {
        const current = get();
        if (current.phase === "cancelling") {
          if (!cancelArmed) {
            cancelArmed = true;
            set({
              stageLabel: "正在取消",
              logs: [...current.logs, `${stamp()}  正在取消`],
            });
            return;
          }
          finishCancelled();
          return;
        }
        const stage = STAGES[index];
        if (!stage) {
          stopTimer();
          return;
        }
        const failed = current.failNext && stage.pct >= 46;
        if (failed) {
          stopTimer();
          const reason = `${project.name}.urls 导入了缺失的本地应用。`;
          const elapsed = Math.min(target, (target * stage.pct) / 100);
          const item: HistoryItem = {
            id: `f-${Date.now()}`,
            time: nowLabel(),
            project: project.name,
            path: current.outputDir,
            status: "failed",
            modules: project.modules,
            size: "—",
            duration: formatSec(elapsed),
            algo: current.algo,
            reason: `manage.py check 未通过：${reason}`,
          };
          set({
            phase: "failed",
            progress: stage.pct,
            stageLabel: "构建失败",
            elapsed,
            error: `manage.py check 未通过：${reason}`,
            logs: [...get().logs, `${stamp()}  失败  ${reason}`],
          });
          pushHistory(item);
          toast("构建失败", "danger");
          return;
        }
        const log =
          stage.pct === 46
            ? `正在加密 ${project.modules} 个模块（${algoLabel(current.algo)}）`
            : stage.pct === 84
              ? `发布到 ${current.outputDir || project.path + "-shielded"}`
              : stage.pct === 92 && current.skipCheck
                ? "已跳过 manage.py check"
                : stage.log;
        const elapsed = Math.min(target, (target * stage.pct) / 100);
        const logs = [...get().logs, `${stamp()}  ${log}`];
        if (stage.pct >= 100) {
          stopTimer();
          const duration = formatSec(target);
          const size = estimateMb(project.modules);
          const item: HistoryItem = {
            id: `s-${Date.now()}`,
            time: nowLabel(),
            project: project.name,
            path: current.outputDir,
            status: "success",
            modules: project.modules,
            size,
            duration,
            algo: current.algo,
          };
          const trialDrop = current.license === "trial" ? Math.max(0, current.buildsLeft - 1) : current.buildsLeft;
          const month = proUnlocked(current.license) && current.license !== "dev" ? current.monthUsed + 1 : current.monthUsed;
          set({
            phase: "success",
            progress: 100,
            stageLabel: "完成",
            elapsed: target,
            error: null,
            logs,
            buildsLeft: trialDrop,
            monthUsed: month,
          });
          pushHistory(item);
          toast("加密完成", "ok");
          return;
        }
        set({
          progress: stage.pct,
          stageLabel: stage.label,
          elapsed,
          logs,
        });
        index += 1;
      }, 340);
    },
    cancelBuild: () => {
      const state = get();
      if (state.phase !== "running") return;
      if (tick == null) {
        finishCancelled();
        return;
      }
      set({ phase: "cancelling", stageLabel: "正在取消" });
    },
    openOutput: (path, ok) => {
      if (!ok) {
        toast("这次构建没有可发布的目录", "warn");
        return;
      }
      toast(`已在资源管理器中打开 ${path}`, "ok");
    },
    openKey: (item) => {
      if (item) {
        if (item.status !== "success") {
          toast("这次构建没有生成密钥", "warn");
          return;
        }
        set({
          modal: "key",
          keyFor: { project: item.project, path: item.path, algo: item.algo, time: item.time },
        });
        return;
      }
      const state = get();
      const project = projectById(state.projectId);
      if (state.phase !== "success" || !project) {
        toast("完成本次构建后才能查看密钥", "warn");
        return;
      }
      set({
        modal: "key",
        keyFor: {
          project: project.name,
          path: state.outputDir,
          algo: state.algo,
          time: nowLabel(),
        },
      });
    },
    closeModal: () => set({ modal: null }),
    askClear: () => {
      if (get().history.length === 0) {
        toast("没有可清空的记录", "info");
        return;
      }
      set({ modal: "clear" });
    },
    clearHistory: () => {
      set({ history: [], modal: null });
      toast("构建记录已清空", "info");
    },
    setDefAlgo: (algo) => {
      if (algo !== "chacha20" && !proUnlocked(get().license)) {
        rejectLocked("algo");
        return;
      }
      set({ defAlgo: algo });
    },
    setDefRuntime: (runtime) => {
      if (runtime === "bytecode" && !proUnlocked(get().license)) {
        rejectLocked("runtime");
        return;
      }
      set({ defRuntime: runtime });
    },
    setDefIntegrity: (integrity) => {
      if (integrity === "full" && !proUnlocked(get().license)) {
        rejectLocked("full");
        return;
      }
      set({ defIntegrity: integrity });
    },
    setSuffix: (value) => set({ suffix: value }),
    toggleDefAuto: () => set((state) => ({ autoDetect: !state.autoDetect })),
    toggleDefSkip: () => set((state) => ({ defSkip: !state.defSkip })),
    saveDefaults: () => {
      const state = get();
      if (
        !proUnlocked(state.license) &&
        (state.defAlgo !== "chacha20" || state.defRuntime !== "source" || state.defIntegrity === "full")
      ) {
        toast("试用版无法把专业版选项存为默认值", "warn");
        return;
      }
      const project = projectById(state.projectId);
      set({
        algo: state.defAlgo,
        runtime: state.defRuntime,
        integrity: state.defIntegrity,
        skipCheck: state.defSkip,
        interpreter: state.autoDetect ? "auto" : state.interpreter,
        outputDir: project ? `${project.path}${state.suffix}` : state.outputDir,
      });
      toast("设置已保存", "ok");
    },
    openImport: () => set({ modal: "import" }),
    importLicense: (which) => {
      if (which === "pro") {
        set({ license: "pro", daysLeft: 364, modal: null, devEnv: false });
        toast("已导入专业版授权", "ok");
        return;
      }
      set({ license: "enterprise", daysLeft: 364, modal: null, devEnv: false });
      toast("已导入企业版授权", "ok");
    },
    openOffline: () => {
      if (!proUnlocked(get().license)) {
        toast("当前授权不包含离线激活", "warn");
        return;
      }
      set({ modal: "offline" });
    },
    applyOffline: (text) => {
      if (!proUnlocked(get().license) && !/PRO|ENT|TRIAL/i.test(text)) {
        toast("当前授权不包含离线激活", "warn");
        return;
      }
      const upper = text.toUpperCase();
      if (upper.includes("ENT")) {
        set({ license: "enterprise", daysLeft: 364, modal: null });
        toast("已应用企业版离线响应", "ok");
        return;
      }
      if (upper.includes("PRO")) {
        set({ license: "pro", daysLeft: 364, modal: null });
        toast("已应用专业版离线响应", "ok");
        return;
      }
      if (upper.includes("TRIAL")) {
        set({ license: "trial", daysLeft: 14, buildsLeft: 3, modal: null });
        toast("已应用试用离线响应", "ok");
        return;
      }
      toast("离线响应无法验签", "danger");
    },
    heartbeat: () => {
      const kind = get().license;
      if (kind === "inactive") {
        toast("尚未激活，无法校验", "warn");
        return;
      }
      if (kind === "expired") {
        toast("授权异常：试用已结束", "danger");
        return;
      }
      toast("心跳通过，授权仍然有效", "ok");
    },
    syncRevoke: () => {
      if (!proUnlocked(get().license)) {
        toast("当前授权不包含撤销列表", "warn");
        return;
      }
      toast("撤销列表已是最新", "ok");
    },
    askRelease: () => {
      if (get().license === "inactive") {
        toast("本机还没有授权", "info");
        return;
      }
      set({ modal: "release" });
    },
    releaseSeat: () => {
      set({
        license: "inactive",
        daysLeft: 0,
        buildsLeft: 0,
        modal: null,
        page: "license",
        waiting: false,
        pendingGrant: false,
        devEnv: false,
      });
      toast("已解除本机授权", "warn");
    },
    goOnlineActivate: () => {
      set({ page: "license", activateOpen: true, surface: "desktop" });
      toast("粘贴激活码后点击激活", "info");
    },
    minimize: () => set({ minimized: true }),
    toggleMax: () => set((state) => ({ maximized: !state.maximized })),
    closeWindow: () => {
      set({ minimized: true });
      toast("窗口已关闭。点底部的 PyShield 可以重新打开。", "info");
    },
    restore: () => set({ minimized: false }),
    bannerAction: () => {
      const kind = get().license;
      if (kind === "trial") {
        get().openPlan("pro");
        return;
      }
      set({ page: "license", surface: "desktop" });
    },
  };
});

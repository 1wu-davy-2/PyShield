export type LicenseKind = "inactive" | "trial" | "expired" | "pro" | "enterprise" | "dev";
export type NavPage = "build" | "history" | "license" | "settings" | "about";
export type WebPage = "home" | "activate" | "success" | "contact";
export type Surface = "desktop" | "web";
export type Algo = "chacha20" | "chacha20p" | "aes";
export type RuntimeForm = "source" | "bytecode";
export type Integrity = "core" | "full" | "off";
export type Term = "forever" | "days" | "date";
export type BuildPhase = "idle" | "running" | "cancelling" | "success" | "failed" | "cancelled";
export type ToastTone = "info" | "ok" | "warn" | "danger";
export type ModalKind =
  | "trial-ended"
  | "key"
  | "picker"
  | "output"
  | "import"
  | "offline"
  | "clear"
  | "release";

export type Project = {
  id: string;
  name: string;
  path: string;
  modules: number;
  django: boolean;
};

export type HistoryStatus = "success" | "failed" | "cancelled";

export type HistoryItem = {
  id: string;
  time: string;
  project: string;
  path: string;
  status: HistoryStatus;
  modules: number;
  size: string;
  duration: string;
  algo: Algo;
  reason?: string;
};

export type KeyFor = {
  project: string;
  path: string;
  algo: Algo;
  time: string;
};

export type Toast = { id: number; text: string; tone: ToastTone };

export const SERIAL = "PS-4RD6-H29F-CJ5T";
export const MACHINE = "7deb975ff9da8cc8";
export const FINGERPRINT = "9f3c1a72e4b08d11";
export const TRIAL_UNTIL = "2026-11-27";
export const PRO_UNTIL = "2027-11-13";
export const PRO_CUSTOMER = "Acme";
export const PRO_ID = "PS-2026-0042";
export const ENT_CUSTOMER = "Northwind";
export const ENT_ID = "PS-2026-0198";
export const APP_VERSION = "v0.6.0";
export const CPYTHON = "CPython 3.12.10";

export const PROJECTS: Project[] = [
  { id: "erp", name: "erp-portal", path: "E:\\proj\\erp", modules: 48, django: true },
  { id: "crm", name: "crm-suite", path: "E:\\proj\\crm", modules: 71, django: true },
  { id: "bill", name: "billing-api", path: "D:\\work\\billing", modules: 33, django: true },
  { id: "notes", name: "notes", path: "E:\\misc\\notes", modules: 0, django: false },
];

export const SAMPLE_HISTORY: HistoryItem[] = [
  {
    id: "h1",
    time: "11-13 21:10",
    project: "erp-portal",
    path: "E:\\proj\\erp-shielded",
    status: "success",
    modules: 48,
    size: "2.0 MB",
    duration: "12.6 s",
    algo: "chacha20",
  },
  {
    id: "h2",
    time: "11-12 18:22",
    project: "crm-suite",
    path: "E:\\proj\\crm-shielded",
    status: "failed",
    modules: 71,
    size: "—",
    duration: "3.1 s",
    algo: "chacha20",
    reason: "manage.py check 未通过：crm.urls 导入了缺失的本地应用。",
  },
  {
    id: "h3",
    time: "11-10 09:04",
    project: "billing-api",
    path: "D:\\work\\billing-shielded",
    status: "success",
    modules: 33,
    size: "1.4 MB",
    duration: "8.7 s",
    algo: "chacha20",
  },
];

export const PRESETS = [
  { id: "lic-off", group: "桌面", label: "未激活" },
  { id: "lic-trial", group: "桌面", label: "试用中" },
  { id: "build", group: "桌面", label: "构建" },
  { id: "running", group: "桌面", label: "构建中" },
  { id: "success", group: "桌面", label: "构建成功" },
  { id: "history", group: "桌面", label: "记录" },
  { id: "empty", group: "桌面", label: "空记录" },
  { id: "settings", group: "桌面", label: "设置" },
  { id: "about", group: "桌面", label: "关于" },
  { id: "modal", group: "桌面", label: "试用结束" },
  { id: "web", group: "官网", label: "申请页" },
  { id: "web-ok", group: "官网", label: "开通成功" },
  { id: "web-pro", group: "官网", label: "联系专业版" },
  { id: "web-ent", group: "官网", label: "联系企业版" },
  { id: "web-home", group: "官网", label: "官网首页" },
] as const;

export type PresetId = (typeof PRESETS)[number]["id"];

export const ALGO_OPTIONS: { id: Algo; label: string; pro?: boolean }[] = [
  { id: "chacha20", label: "ChaCha20" },
  { id: "chacha20p", label: "ChaCha20-P", pro: true },
  { id: "aes", label: "AES-256", pro: true },
];

export const RUNTIME_OPTIONS: { id: RuntimeForm; label: string; pro?: boolean }[] = [
  { id: "source", label: "源码" },
  { id: "bytecode", label: "字节码", pro: true },
];

export const INTEGRITY_OPTIONS: { id: Integrity; label: string; pro?: boolean }[] = [
  { id: "core", label: "保护核心" },
  { id: "full", label: "完整", pro: true },
  { id: "off", label: "关闭" },
];

export const TERM_OPTIONS: { id: Term; label: string }[] = [
  { id: "forever", label: "永久" },
  { id: "days", label: "按天数" },
  { id: "date", label: "指定日期" },
];

export function proUnlocked(kind: LicenseKind) {
  return kind === "pro" || kind === "enterprise" || kind === "dev";
}

export function projectById(id: string | null) {
  if (!id) return null;
  return PROJECTS.find((item) => item.id === id) ?? null;
}

export function algoLabel(algo: Algo) {
  if (algo === "chacha20") return "ChaCha20";
  if (algo === "chacha20p") return "ChaCha20-Poly";
  return "AES-256";
}

export function runtimeLabel(runtime: RuntimeForm) {
  return runtime === "source" ? "源码" : "字节码";
}

export function integrityLabel(integrity: Integrity) {
  if (integrity === "core") return "保护核心";
  if (integrity === "full") return "完整";
  return "关闭";
}

export function termLabel(term: Term, days: number, date: string) {
  if (term === "forever") return "永久";
  if (term === "days") return `${days} 天`;
  return date;
}

export function estimateMb(modules: number) {
  if (!modules) return "—";
  return `${((modules * 42) / 1024).toFixed(1)} MB`;
}

export function estimateSec(modules: number, algo: Algo) {
  if (!modules) return 0;
  return modules * 0.2625 + (algo === "aes" ? 1.4 : algo === "chacha20p" ? 0.5 : 0);
}

export function formatSec(seconds: number) {
  if (!seconds) return "—";
  return `${seconds.toFixed(1)} s`;
}

export function lockMessage(kind: LicenseKind, feature: "algo" | "runtime" | "full" | "obfuscate") {
  if (kind === "inactive") return "请先申请试用或导入授权";
  if (feature === "algo") return "试用版只能使用 ChaCha20 纯 Python";
  if (feature === "runtime") return "试用版不支持原生运行时";
  if (feature === "full") return "试用版不支持完整校验清单";
  return "试用版不支持标识符混淆";
}

export function capsuleOf(kind: LicenseKind, days: number) {
  switch (kind) {
    case "inactive":
      return { label: "未激活", tone: "warn" as const };
    case "trial":
      return { label: `试用 · ${days} 天`, tone: "warn" as const };
    case "expired":
      return { label: "授权异常", tone: "danger" as const };
    case "pro":
      return { label: "专业版", tone: "ok" as const };
    case "enterprise":
      return { label: "企业版", tone: "ok" as const };
    case "dev":
      return { label: "开发模式", tone: "warn" as const };
  }
}

export function trialBanner(kind: LicenseKind, days: number, left: number) {
  if (kind === "inactive") {
    return {
      label: "未激活",
      text: "尚未激活，先申请 14 天免费试用再开始构建",
      action: "去申请",
      tone: "warn" as const,
    };
  }
  if (kind === "trial") {
    return {
      label: "试用版",
      text: `试用版 · 剩余 ${days} 天 · 剩余 ${left} 次构建`,
      action: "升级版本",
      tone: "warn" as const,
    };
  }
  if (kind === "expired") {
    return {
      label: "试用已结束",
      text: "试用额度已用完，购买专业版后可继续构建",
      action: "去申请",
      tone: "danger" as const,
    };
  }
  return null;
}

export function pageMeta(page: NavPage) {
  switch (page) {
    case "build":
      return {
        title: "加密构建",
        tag: "Django",
        sub: "选择项目目录，配置保护参数，一键生成可部署的受保护副本。",
      };
    case "history":
      return {
        title: "构建记录",
        tag: "",
        sub: "查看最近的加密构建。失败记录会保留，方便回看原因。",
      };
    case "license":
      return {
        title: "版本与试用",
        tag: "",
        sub: "管理构建器自身的授权。免费版可自助开通，无需付款。",
      };
    case "settings":
      return {
        title: "设置",
        tag: "",
        sub: "这里的授权属于构建器本身，与产物里的客户许可证不是一回事。",
      };
    case "about":
      return {
        title: "关于",
        tag: APP_VERSION,
        sub: "本地把 Django 源码做成不可读的受保护副本。",
      };
  }
}

export function licenseCopy(kind: LicenseKind, days: number, left: number) {
  switch (kind) {
    case "inactive":
      return {
        badge: "未激活",
        tone: "neutral" as const,
        title: "选择适合你的版本",
        body: "免费版可自助开通：14 天、3 次加密构建，无需付款。",
        freeLabel: "申请 14 天试用",
        freeDisabled: false,
      };
    case "trial":
      return {
        badge: "试用中",
        tone: "warn" as const,
        title: "试用版已激活",
        body: `剩余 ${days} 天、${left} 次构建。到期后加密构建会被拒绝，已生成的产物不受影响。`,
        freeLabel: "重新打开官网",
        freeDisabled: false,
      };
    case "expired":
      return {
        badge: "试用已结束",
        tone: "danger" as const,
        title: "试用已结束",
        body: "14 天试用期或 3 次构建额度已用完，购买专业版后可以继续构建。",
        freeLabel: "试用已结束",
        freeDisabled: true,
      };
    case "dev":
      return {
        badge: "开发模式",
        tone: "warn" as const,
        title: "当前为开发模式",
        body: "源码运行，不限制构建次数。正式授权页仅作对照。",
        freeLabel: "已具备正式授权",
        freeDisabled: true,
      };
    default:
      return {
        badge: "正式授权",
        tone: "ok" as const,
        title: "当前为正式授权",
        body: "已安装正式授权，可以继续使用全部已购买功能。",
        freeLabel: "已具备正式授权",
        freeDisabled: true,
      };
  }
}

export function fakeKey(project: string, algo: string) {
  const raw = `${project}|${algo}|pyshield-v0.6.0|${MACHINE}`;
  const hex = Array.from(raw)
    .map((char) => char.charCodeAt(0).toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
  const body = `${hex}${hex}`.slice(0, 128);
  return body.replace(/(.{32})/g, "$1\n").trim();
}

export const RUN_LOGS = [
  "21:10:03  复制项目文件到临时目录",
  "21:10:05  扫描 Python 模块并建立加密队列",
  "21:10:08  正在加密 48 个模块（ChaCha20）",
  "21:10:14  模块加密完成，写入 stub 入口",
  "21:10:16  安装受保护运行时",
];

export const OK_LOGS = [
  ...RUN_LOGS,
  "21:10:17  签发产物许可证",
  "21:10:18  生成完整性清单",
  "21:10:19  发布到 E:\\proj\\erp-shielded",
  "21:10:20  写入构建密钥 build.key",
  "21:10:20  运行 manage.py check",
  "21:10:21  清理临时文件",
  "21:10:21  构建完成",
];

export const STAGES: { pct: number; label: string; log: string }[] = [
  { pct: 10, label: "复制项目文件", log: "复制项目文件到临时目录" },
  { pct: 28, label: "加密模块", log: "扫描 Python 模块并建立加密队列" },
  { pct: 46, label: "加密模块", log: "正在加密模块" },
  { pct: 60, label: "加密模块", log: "模块加密完成，写入 stub 入口" },
  { pct: 66, label: "安装运行时", log: "安装受保护运行时" },
  { pct: 72, label: "写入许可证", log: "签发产物许可证" },
  { pct: 78, label: "写入完整性清单", log: "生成完整性清单" },
  { pct: 84, label: "发布输出目录", log: "发布到输出目录" },
  { pct: 88, label: "写入构建密钥", log: "写入构建密钥 build.key" },
  { pct: 92, label: "manage.py check", log: "运行 manage.py check" },
  { pct: 96, label: "清理临时文件", log: "清理临时文件" },
  { pct: 100, label: "完成", log: "构建完成" },
];

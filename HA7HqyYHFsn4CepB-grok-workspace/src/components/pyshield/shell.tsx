import type { ReactNode } from "react";
import {
  BadgeCheck,
  FileClock,
  Info,
  LockKeyhole,
  Minus,
  RefreshCw,
  Settings,
  Square,
  X,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { APP_VERSION, CPYTHON, capsuleOf, pageMeta } from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Capsule, Mark } from "@/components/pyshield/bits";

const NAV = [
  { id: "build" as const, label: "加密构建", icon: LockKeyhole, group: "工作区" },
  { id: "history" as const, label: "构建记录", icon: FileClock, group: "工作区" },
  { id: "license" as const, label: "版本与试用", icon: BadgeCheck, group: "应用" },
  { id: "settings" as const, label: "设置", icon: Settings, group: "应用" },
  { id: "about" as const, label: "关于", icon: Info, group: "应用" },
];

export function Sidebar() {
  const page = usePyshield((state) => state.page);
  const setPage = usePyshield((state) => state.setPage);
  const license = usePyshield((state) => state.license);
  const history = usePyshield((state) => state.history.length);

  return (
    <aside className="flex shrink-0 flex-col border-line bg-sidebar max-xl:border-b xl:w-sidebar xl:border-r">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <Mark />
        <div className="min-w-0">
          <p className="text-title font-semibold leading-tight text-ink">PyShield</p>
          <p className="text-aux text-ink-3">加密构建器</p>
        </div>
      </div>
      <nav className="flex gap-1 overflow-x-auto px-2 pb-2 xl:flex-1 xl:flex-col xl:overflow-visible xl:px-3">
        {NAV.map((item, index) => {
          const showGroup = index === 0 || NAV[index - 1]?.group !== item.group;
          const active = page === item.id;
          const Icon = item.icon;
          return (
            <div key={item.id} className="contents xl:block">
              {showGroup ? (
                <p className="hidden px-3 pb-1 pt-3 text-aux font-medium text-ink-4 xl:block">{item.group}</p>
              ) : null}
              <button
                type="button"
                onClick={() => setPage(item.id)}
                className={cn(
                  "relative flex min-h-11 shrink-0 items-center gap-2 rounded-ctrl px-3 text-body xl:w-full",
                  active ? "bg-primary-soft font-medium text-primary" : "text-ink-2 hover:bg-card",
                )}
              >
                {active ? <span className="absolute bottom-2 left-0 top-2 w-1 rounded-full bg-primary max-xl:hidden" /> : null}
                <Icon className="size-4 shrink-0" aria-hidden />
                <span>{item.label}</span>
                {item.id === "history" ? (
                  <span className="ml-auto rounded-full bg-card px-1.5 text-aux tabular-nums text-ink-3">{history}</span>
                ) : null}
                {item.id === "license" && license === "trial" ? (
                  <span className="ml-auto rounded-full bg-warn-soft px-1.5 text-aux font-medium text-warn-ink">试用</span>
                ) : null}
              </button>
            </div>
          );
        })}
      </nav>
      <p className="px-4 pb-3 text-aux text-ink-4 xl:hidden">
        {APP_VERSION} · 本地离线构建
      </p>
      <div className="hidden px-3 pb-4 xl:block">
        <div className="rounded-card bg-card px-3 py-3 shadow-card">
          <p className="text-body font-medium text-ink">本地离线构建</p>
          <p className="mt-1 text-aux text-ink-3">加密在本机完成，源码与密钥不出设备。</p>
        </div>
        <p className="mt-3 px-1 font-mono text-aux text-ink-4">
          {APP_VERSION} · {CPYTHON}
        </p>
      </div>
    </aside>
  );
}

export function Topbar() {
  const page = usePyshield((state) => state.page);
  const license = usePyshield((state) => state.license);
  const days = usePyshield((state) => state.daysLeft);
  const setPage = usePyshield((state) => state.setPage);
  const refresh = usePyshield((state) => state.refreshLicense);
  const minimized = usePyshield((state) => state.minimize);
  const toggleMax = usePyshield((state) => state.toggleMax);
  const closeWindow = usePyshield((state) => state.closeWindow);
  const maximized = usePyshield((state) => state.maximized);
  const meta = pageMeta(page);
  const capsule = capsuleOf(license, days);

  return (
    <header className="flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-card px-4 md:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-page font-semibold text-ink">{meta.title}</h1>
          {meta.tag ? (
            <span className="shrink-0 rounded-full bg-primary-soft px-2 py-0.5 text-aux font-medium text-primary">
              {meta.tag}
            </span>
          ) : null}
        </div>
        <p className="truncate text-aux text-ink-3">{meta.sub}</p>
      </div>
      <Capsule tone={capsule.tone} onClick={() => setPage("settings")}>
        {capsule.label}
      </Capsule>
      <button
        type="button"
        aria-label="刷新授权状态"
        onClick={refresh}
        className="flex size-11 items-center justify-center rounded-ctrl text-ink-2 hover:bg-canvas"
      >
        <RefreshCw className="size-4" />
      </button>
      <div className="hidden items-center sm:flex">
        <WinButton label="最小化" onClick={minimized}>
          <Minus className="size-4" />
        </WinButton>
        <WinButton label={maximized ? "还原" : "最大化"} onClick={toggleMax}>
          <Square className="size-3.5" />
        </WinButton>
        <WinButton label="关闭" onClick={closeWindow} danger>
          <X className="size-4" />
        </WinButton>
      </div>
    </header>
  );
}

function WinButton({
  label,
  onClick,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={cn(
        "flex h-10 w-11 items-center justify-center text-ink-2 hover:bg-canvas",
        danger && "hover:bg-danger hover:text-on-primary",
      )}
    >
      {children}
    </button>
  );
}

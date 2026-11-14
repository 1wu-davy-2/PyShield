import { useEffect } from "react";
import { X } from "lucide-react";
import { PRESETS, type LicenseKind } from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { cn } from "@/lib/cn";
import { Mark } from "@/components/pyshield/bits";
import { Sidebar, Topbar } from "@/components/pyshield/shell";
import { BuildPage } from "@/components/pyshield/build-page";
import { HistoryPage } from "@/components/pyshield/history-page";
import { LicensePage } from "@/components/pyshield/license-page";
import { SettingsPage } from "@/components/pyshield/settings-page";
import { AboutPage } from "@/components/pyshield/about-page";
import { BrowserWindow } from "@/components/pyshield/website";
import { Modals } from "@/components/pyshield/modals";

const LICENSES: { id: LicenseKind; label: string }[] = [
  { id: "inactive", label: "未激活" },
  { id: "trial", label: "试用中" },
  { id: "expired", label: "试用已结束" },
  { id: "pro", label: "专业版" },
  { id: "enterprise", label: "企业版" },
  { id: "dev", label: "开发模式" },
];

export function PrototypeApp() {
  useAutoReturn();
  const surface = usePyshield((state) => state.surface);
  const minimized = usePyshield((state) => state.minimized);
  const maximized = usePyshield((state) => state.maximized);

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden bg-desk text-ink">
      <PrototypeBar />
      <div className={cn("min-h-0 flex-1", maximized ? "p-0" : "p-3 md:p-5")}>
        {surface === "web" ? (
          <BrowserWindow />
        ) : minimized ? (
          <RestoreDock />
        ) : (
          <DesktopWindow />
        )}
      </div>
      <Toasts />
    </div>
  );
}

function useAutoReturn() {
  const autoReturn = usePyshield((state) => state.autoReturn);
  const surface = usePyshield((state) => state.surface);
  const web = usePyshield((state) => state.web);
  const waiting = usePyshield((state) => state.waiting);
  const pending = usePyshield((state) => state.pendingGrant);

  useEffect(() => {
    if (!autoReturn || !pending) return;
    const delay = surface === "web" && web === "success" ? 3400 : surface === "desktop" && waiting ? 2500 : 0;
    if (!delay) return;
    const timer = window.setTimeout(() => usePyshield.getState().activateFromSite(), delay);
    return () => window.clearTimeout(timer);
  }, [autoReturn, surface, web, waiting, pending]);
}

function PrototypeBar() {
  const presetId = usePyshield((state) => state.presetId);
  const loadPreset = usePyshield((state) => state.loadPreset);
  const license = usePyshield((state) => state.license);
  const setLicenseKind = usePyshield((state) => state.setLicenseKind);
  const serviceUp = usePyshield((state) => state.serviceUp);
  const toggleService = usePyshield((state) => state.toggleService);
  const failNext = usePyshield((state) => state.failNext);
  const toggleFailNext = usePyshield((state) => state.toggleFailNext);

  return (
    <div className="shrink-0 border-b border-line bg-card px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="shrink-0 text-aux font-medium text-ink-3">画面</span>
        <select
          aria-label="原型画面"
          className="h-11 min-w-0 flex-1 rounded-ctrl border border-line bg-card px-2 text-body text-ink md:hidden"
          value={presetId ?? ""}
          onChange={(event) => {
            if (event.target.value) loadPreset(event.target.value as (typeof PRESETS)[number]["id"]);
          }}
        >
          {presetId ? null : <option value="">当前画面</option>}
          {PRESETS.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.group} · {preset.label}
            </option>
          ))}
        </select>
        <div className="hidden min-w-0 flex-1 gap-1 overflow-x-auto md:flex">
          {PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              aria-pressed={presetId === preset.id}
              onClick={() => loadPreset(preset.id)}
              className={cn(
                "shrink-0 rounded-full px-3 py-2 text-aux font-medium",
                presetId === preset.id ? "bg-primary text-on-primary" : "bg-canvas text-ink-2 hover:bg-primary-soft",
              )}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-aux text-ink-3">
          授权
          <select
            className="h-10 rounded-ctrl border border-line bg-card px-2 text-body text-ink"
            value={license}
            onChange={(event) => setLicenseKind(event.target.value as LicenseKind)}
          >
            {LICENSES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={toggleService}
          className={cn(
            "min-h-10 rounded-full px-3 text-aux font-medium",
            serviceUp ? "bg-ok-soft text-ok-ink" : "bg-danger-soft text-danger-ink",
          )}
        >
          {serviceUp ? "服务已连接" : "服务未配置"}
        </button>
        <button
          type="button"
          onClick={toggleFailNext}
          className={cn(
            "min-h-10 rounded-full px-3 text-aux font-medium",
            failNext ? "bg-danger-soft text-danger-ink" : "bg-canvas text-ink-2",
          )}
        >
          {failNext ? "下次构建会失败" : "下次构建成功"}
        </button>
      </div>
    </div>
  );
}

function DesktopWindow() {
  const page = usePyshield((state) => state.page);
  const maximized = usePyshield((state) => state.maximized);
  return (
    <div
      className={cn(
        "relative mx-auto flex h-full min-h-0 w-full max-w-[1440px] flex-col overflow-hidden bg-canvas shadow-window",
        maximized ? "max-w-none rounded-none" : "rounded-2xl",
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
        <Sidebar />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Topbar />
          <main className="scroll-thin min-h-0 flex-1 overflow-auto p-4 md:p-6">
            {page === "build" ? <BuildPage /> : null}
            {page === "history" ? <HistoryPage /> : null}
            {page === "license" ? <LicensePage /> : null}
            {page === "settings" ? <SettingsPage /> : null}
            {page === "about" ? <AboutPage /> : null}
          </main>
        </div>
      </div>
      <Modals />
    </div>
  );
}

function RestoreDock() {
  const restore = usePyshield((state) => state.restore);
  return (
    <div className="flex h-full items-end justify-center pb-8">
      <button
        type="button"
        onClick={restore}
        className="flex min-h-11 items-center gap-3 rounded-card bg-card px-4 py-3 shadow-window"
      >
        <Mark />
        <span className="text-left">
          <span className="block text-body font-semibold text-ink">PyShield</span>
          <span className="block text-aux text-ink-3">点击恢复窗口</span>
        </span>
      </button>
    </div>
  );
}

function Toasts() {
  const toasts = usePyshield((state) => state.toasts);
  const dismiss = usePyshield((state) => state.dismissToast);
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex w-[min(24rem,calc(100%-2rem))] -translate-x-1/2 flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={cn(
            "pointer-events-auto flex items-start gap-3 rounded-card px-3 py-3 shadow-pop",
            toast.tone === "ok" && "bg-ok-soft text-ok-ink",
            toast.tone === "warn" && "bg-warn-soft text-warn-ink",
            toast.tone === "danger" && "bg-danger-soft text-danger-ink",
            toast.tone === "info" && "bg-card text-ink",
          )}
          role="status"
        >
          <p className="flex-1 text-body">{toast.text}</p>
          <button type="button" aria-label="关闭提示" onClick={() => dismiss(toast.id)} className="text-current">
            <X className="size-4" />
          </button>
        </div>
      ))}
    </div>
  );
}

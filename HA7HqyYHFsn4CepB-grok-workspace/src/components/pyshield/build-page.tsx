import { useEffect, useRef } from "react";
import { ChevronDown, FolderOpen } from "lucide-react";
import {
  ALGO_OPTIONS,
  INTEGRITY_OPTIONS,
  RUNTIME_OPTIONS,
  TERM_OPTIONS,
  algoLabel,
  estimateMb,
  estimateSec,
  formatSec,
  integrityLabel,
  projectById,
  proUnlocked,
  runtimeLabel,
  termLabel,
  trialBanner,
} from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Capsule, Card, CardTitle, Field, Segment, Switch, inputClass } from "@/components/pyshield/bits";

export function BuildPage() {
  const state = usePyshield();
  const logRef = useRef<HTMLDivElement>(null);
  const project = projectById(state.projectId);
  const locked = !proUnlocked(state.license);
  const banner = trialBanner(state.license, state.daysLeft, state.buildsLeft);
  const running = state.phase === "running" || state.phase === "cancelling";
  const size = project ? estimateMb(project.modules) : "—";
  const predicted = project ? estimateSec(project.modules, state.algo) : 0;
  const shownTime =
    state.phase === "idle" || state.phase === "cancelled" ? predicted : state.elapsed;

  useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [state.logs]);

  return (
    <div className="flex flex-col gap-4">
      {banner ? (
        <div
          className={
            banner.tone === "danger"
              ? "flex flex-wrap items-center gap-3 rounded-card bg-danger-soft px-4 py-3"
              : "flex flex-wrap items-center gap-3 rounded-card bg-warn-soft px-4 py-3"
          }
        >
          <Capsule tone={banner.tone}>{banner.label}</Capsule>
          <p className={banner.tone === "danger" ? "min-w-0 flex-1 text-body text-danger-ink" : "min-w-0 flex-1 text-body text-warn-ink"}>
            {banner.text}
          </p>
          <Button variant="secondary" onClick={state.bannerAction}>
            {banner.action}
          </Button>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="加密模块" value={project ? String(project.modules) : "—"} />
        <Stat label="输出体积" value={state.phase === "failed" ? "—" : size} hint={state.phase === "success" ? "最近一次" : "预计"} />
        <Stat label="构建耗时" value={formatSec(shownTime)} hint={running ? "进行中" : state.phase === "success" ? "最近一次" : "预计"} />
        <Stat label="许可证状态" value={termLabel(state.term, state.termDays, state.termDate)} />
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-5">
        <div className="flex flex-col gap-4 xl:col-span-3">
          <Card>
            <CardTitle
              title="项目与输出"
              extra={
                <span className="rounded-full bg-canvas px-2 py-1 text-aux text-ink-3">
                  {project ? project.name : "未选择"}
                </span>
              }
            />
            <div className="flex flex-col gap-4">
              <Field label="Django 项目目录">
                <div className="flex gap-2">
                  <input
                    className={inputClass}
                    readOnly
                    value={project?.path ?? ""}
                    placeholder="尚未选择"
                  />
                  <Button variant="secondary" onClick={state.openPicker}>
                    <FolderOpen className="size-4" />
                    浏览
                  </Button>
                </div>
              </Field>
              <Field label="输出目录">
                <div className="flex gap-2">
                  <input
                    className={inputClass}
                    value={state.outputDir}
                    placeholder="选择项目后自动建议"
                    onChange={(event) => state.setOutputDir(event.target.value)}
                  />
                  <Button variant="secondary" onClick={state.openOutputPicker}>
                    选择
                  </Button>
                </div>
              </Field>
              <Field label="校验解释器">
                <select
                  className={inputClass}
                  value={state.interpreter}
                  onChange={(event) => state.setInterpreter(event.target.value)}
                >
                  <option value="auto">自动检测</option>
                  <option value="3.12">CPython 3.12.10</option>
                  <option value="3.11">CPython 3.11.9</option>
                </select>
              </Field>
            </div>
          </Card>

          <Card>
            <CardTitle title="加密选项" />
            <div className="flex flex-col gap-4">
              <Field label="模块加密算法">
                <Segment value={state.algo} options={ALGO_OPTIONS} locked={locked} onChange={state.setAlgo} />
              </Field>
              <Field label="运行时形态">
                <Segment value={state.runtime} options={RUNTIME_OPTIONS} locked={locked} onChange={state.setRuntime} />
              </Field>
              <Field label="完整性清单">
                <Segment
                  value={state.integrity}
                  options={INTEGRITY_OPTIONS}
                  locked={locked}
                  onChange={state.setIntegrity}
                />
              </Field>
            </div>
          </Card>

          <Card>
            <CardTitle title="许可证" extra={<span className="text-aux text-ink-4">写入产物，不是构建器授权</span>} />
            <div className="flex flex-col gap-4">
              <Field label="有效期">
                <Segment value={state.term} options={TERM_OPTIONS} locked={false} onChange={state.setTerm} />
              </Field>
              {state.term === "days" ? (
                <Field label="天数">
                  <input
                    className={inputClass}
                    type="number"
                    min={1}
                    max={3650}
                    value={state.termDays}
                    onChange={(event) => state.setTermDays(Number(event.target.value) || 1)}
                  />
                </Field>
              ) : null}
              {state.term === "date" ? (
                <Field label="到期日">
                  <input
                    className={inputClass}
                    type="date"
                    value={state.termDate}
                    onChange={(event) => state.setTermDate(event.target.value)}
                  />
                </Field>
              ) : null}
              <Field label="客户名称">
                <input
                  className={inputClass}
                  value={state.customer}
                  placeholder="例如 Acme 中国"
                  onChange={(event) => state.setCustomer(event.target.value)}
                />
              </Field>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-body text-ink">设备绑定</p>
                  <p className="text-aux text-ink-4">打开后只允许本机指纹运行产物。</p>
                </div>
                <Switch on={state.bindDevice} label="设备绑定" onClick={state.toggleBind} />
              </div>
            </div>
          </Card>

          <Card>
            <button type="button" onClick={state.toggleAdvanced} className="flex min-h-11 w-full items-center justify-between">
              <span className="text-title font-semibold text-ink">高级选项</span>
              <ChevronDown className={state.advancedOpen ? "size-4 rotate-180 text-ink-3" : "size-4 text-ink-3"} />
            </button>
            {state.advancedOpen ? (
              <div className="mt-4 flex flex-col gap-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-body text-ink">跳过 manage.py check</span>
                  <Switch on={state.skipCheck} label="跳过 manage.py check" onClick={state.toggleSkip} />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-body text-ink">保留原始 .py 备份</span>
                  <Switch on={state.keepBackup} label="保留原始备份" onClick={state.toggleBackup} />
                </div>
                <Field label="排除目录">
                  <input className={inputClass} value={state.exclude} onChange={(event) => state.setExclude(event.target.value)} />
                </Field>
                <div className="flex items-center justify-between gap-3">
                  <span className="inline-flex items-center gap-2 text-body text-ink">
                    标识符混淆
                    {locked ? <span className="rounded-full bg-violet-soft px-1.5 text-aux font-semibold text-violet">Pro</span> : null}
                  </span>
                  <Switch on={state.obfuscate} label="标识符混淆" onClick={state.toggleObfuscate} />
                </div>
              </div>
            ) : null}
          </Card>
        </div>

        <div className="flex flex-col gap-4 xl:col-span-2">
          <Card>
            <CardTitle title="构建摘要" />
            <dl>
              <SummaryRow k="项目" v={project?.name ?? "未选择"} />
              <SummaryRow k="模块" v={project ? String(project.modules) : "—"} />
              <SummaryRow k="算法" v={algoLabel(state.algo)} />
              <SummaryRow k="运行时" v={runtimeLabel(state.runtime)} />
              <SummaryRow k="完整性" v={integrityLabel(state.integrity)} />
              <SummaryRow k="许可证" v={termLabel(state.term, state.termDays, state.termDate)} />
              <SummaryRow k="体积" v={size} />
              <SummaryRow k="耗时" v={formatSec(predicted)} />
            </dl>
            {state.phase === "success" ? (
              <div className="mt-4 rounded-ctrl bg-ok-soft px-3 py-3">
                <p className="text-body font-medium text-ok-ink">加密完成</p>
                <p className="mt-1 text-aux text-ok-ink">
                  {project?.modules ?? 0} 个模块 · {size} · {formatSec(state.elapsed)}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={() => state.openOutput(state.outputDir, true)}>
                    打开输出目录
                  </Button>
                  <Button variant="secondary" onClick={() => state.openKey()}>
                    查看构建密钥
                  </Button>
                </div>
              </div>
            ) : null}
            {state.phase === "failed" && state.error ? (
              <div className="mt-4 rounded-ctrl bg-danger-soft px-3 py-3">
                <p className="text-body font-medium text-danger-ink">构建失败</p>
                <p className="mt-1 text-aux text-danger-ink">{state.error}</p>
              </div>
            ) : null}
            {state.phase === "cancelled" ? (
              <div className="mt-4 rounded-ctrl bg-warn-soft px-3 py-3">
                <p className="text-body font-medium text-warn-ink">已取消</p>
                <p className="mt-1 text-aux text-warn-ink">构建已中止，输出目录未发布。</p>
              </div>
            ) : null}
            {running ? (
              <div className="mt-4">
                <div className="mb-2 flex items-center justify-between text-aux">
                  <span className="font-medium text-ink" aria-live="polite">
                    {state.stageLabel}
                  </span>
                  <span className="font-mono tabular-nums text-ink-3">{state.progress}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-canvas">
                  <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${state.progress}%` }} />
                </div>
              </div>
            ) : null}
            <Button
              variant="primary"
              className="mt-4 w-full"
              disabled={state.phase === "cancelling"}
              onClick={state.phase === "running" ? state.cancelBuild : state.startBuild}
            >
              {state.phase === "cancelling" ? "正在取消" : state.phase === "running" ? "取消构建" : state.phase === "success" ? "再次构建" : "开始加密"}
            </Button>
          </Card>

          <Card>
            <CardTitle title="构建日志" extra={<span className="font-mono text-aux text-ink-4">{state.logs.length}</span>} />
            <div ref={logRef} className="scroll-thin h-52 overflow-auto rounded-ctrl bg-canvas px-3 py-2 font-mono text-aux leading-6 text-ink-2">
              {state.logs.length === 0 ? (
                <p className="text-ink-4">构建开始后，日志会显示在这里。</p>
              ) : (
                state.logs.map((line, index) => (
                  <p key={`${line}-${index}`} className="break-words">
                    {line}
                  </p>
                ))
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-aux text-ink-3">{label}</p>
      <p className="mt-1 font-mono text-page font-semibold tabular-nums text-ink">{value}</p>
      {hint ? <p className="text-aux text-ink-4">{hint}</p> : null}
    </Card>
  );
}

function SummaryRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line py-2 last:border-b-0">
      <dt className="text-aux text-ink-3">{k}</dt>
      <dd className="truncate text-right text-body font-medium text-ink">{v}</dd>
    </div>
  );
}

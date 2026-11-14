import {
  ALGO_OPTIONS,
  APP_VERSION,
  CPYTHON,
  ENT_CUSTOMER,
  ENT_ID,
  FINGERPRINT,
  INTEGRITY_OPTIONS,
  PRO_CUSTOMER,
  PRO_ID,
  PRO_UNTIL,
  RUNTIME_OPTIONS,
  TRIAL_UNTIL,
  proUnlocked,
} from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Capsule, Card, CardTitle, Field, Segment, Switch, inputClass } from "@/components/pyshield/bits";

export function SettingsPage() {
  const state = usePyshield();
  const kind = state.license;
  const unlocked = proUnlocked(kind);

  const head =
    kind === "trial"
      ? `试用有效 · 剩余 ${state.buildsLeft} 次构建 · ${state.daysLeft} 天后到期`
      : kind === "pro"
        ? `授权有效 · 本月剩余 ${Math.max(0, 50 - state.monthUsed)} 次 · 客户 ${PRO_CUSTOMER}`
        : kind === "enterprise"
          ? `授权有效 · 多席位 · 客户 ${ENT_CUSTOMER}`
          : kind === "dev"
            ? "开发模式 · 不限制构建次数"
            : kind === "expired"
              ? "试用已结束 · 构建已被拒绝"
              : "尚未激活 · 导入授权或申请试用后可构建";

  const version =
    kind === "pro" ? "专业版" : kind === "enterprise" ? "企业版" : kind === "trial" ? "试用版" : kind === "dev" ? "开发模式" : kind === "expired" ? "试用已结束" : "未激活";

  const capsule =
    kind === "pro" || kind === "enterprise" ? "ok" : kind === "expired" ? "danger" : kind === "inactive" ? "neutral" : "warn";

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardTitle title="工具授权" extra={<Capsule tone={capsule}>{version}</Capsule>} />
        <p className="text-body text-ink-2">{head}</p>
        <p className="mt-1 text-aux text-ink-4">这是构建器本身的授权，不会写进客户拿到的产物。</p>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2">
          <Fact label="授权版本" value={version} />
          <Fact label="客户" value={kind === "pro" ? PRO_CUSTOMER : kind === "enterprise" ? ENT_CUSTOMER : "—"} />
          <Fact label="授权编号" value={kind === "pro" ? PRO_ID : kind === "enterprise" ? ENT_ID : kind === "trial" ? "试用序列号" : "—"} />
          <Fact
            label="到期时间"
            value={
              kind === "trial" || kind === "expired"
                ? `${TRIAL_UNTIL}${kind === "expired" ? "（已到期）" : ""}`
                : kind === "pro" || kind === "enterprise"
                  ? `${PRO_UNTIL}（宽限 14 天）`
                  : kind === "dev"
                    ? "不限"
                    : "—"
            }
          />
          <Fact
            label="构建配额"
            value={
              kind === "trial"
                ? `${state.buildsLeft}/3 次`
                : kind === "pro"
                  ? `${state.monthUsed}/50 次每月`
                  : kind === "enterprise" || kind === "dev"
                    ? "不限"
                    : "—"
            }
          />
          <Fact label="本机指纹" value={`${FINGERPRINT.slice(0, 8)}…`} />
        </dl>
        <div className="mt-4">
          <Field label="激活服务器">
            <input
              className={inputClass}
              readOnly={kind !== "dev"}
              value={kind === "dev" ? state.serviceUrl : "https://license.pyshield.dev"}
              onChange={(event) => state.setServiceUrl(event.target.value)}
            />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button onClick={state.openImport}>导入授权文件</Button>
          <Button onClick={state.goOnlineActivate}>在线激活</Button>
          <Button onClick={state.openOffline}>生成离线请求</Button>
          <Button onClick={state.openOffline}>应用离线响应</Button>
          <Button variant="ghost" onClick={state.heartbeat}>心跳校验</Button>
          <Button variant="ghost" onClick={state.syncRevoke}>同步撤销列表</Button>
          <Button variant="ghost" onClick={state.askRelease}>解除本机授权</Button>
        </div>
        {!unlocked && kind !== "inactive" ? (
          <p className="mt-3 text-aux text-ink-4">试用版可以看见全部操作。离线激活和撤销列表会提示当前授权不具备该能力。</p>
        ) : null}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardTitle title="构建默认值" />
          <div className="flex flex-col gap-4">
            <Field label="默认算法">
              <Segment value={state.defAlgo} options={ALGO_OPTIONS} locked={!unlocked} onChange={state.setDefAlgo} />
            </Field>
            <Field label="默认完整性">
              <Segment value={state.defIntegrity} options={INTEGRITY_OPTIONS} locked={!unlocked} onChange={state.setDefIntegrity} />
            </Field>
            <Field label="默认运行时">
              <Segment value={state.defRuntime} options={RUNTIME_OPTIONS} locked={!unlocked} onChange={state.setDefRuntime} />
            </Field>
            <Field label="输出目录后缀">
              <input className={inputClass} value={state.suffix} onChange={(event) => state.setSuffix(event.target.value)} />
            </Field>
            <div className="flex items-center justify-between gap-3">
              <span className="text-body text-ink">自动检测解释器</span>
              <Switch on={state.autoDetect} label="自动检测解释器" onClick={state.toggleDefAuto} />
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-body text-ink">默认跳过校验</span>
              <Switch on={state.defSkip} label="默认跳过校验" onClick={state.toggleDefSkip} />
            </div>
            <Button variant="primary" onClick={state.saveDefaults}>
              保存设置
            </Button>
          </div>
        </Card>
        <Card>
          <CardTitle title="本机环境" />
          <dl className="flex flex-col">
            <Env label="应用版本" value={APP_VERSION} />
            <Env label="内置 CPython" value={CPYTHON} />
            <Env label="部署要求" value="Windows 10 及以上，无需预装 Python" />
            <Env label="本机指纹" value={FINGERPRINT} />
            <Env label="配置目录" value="%APPDATA%\\PyShield" />
          </dl>
        </Card>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-aux text-ink-3">{label}</dt>
      <dd className="mt-0.5 text-body font-medium text-ink">{value}</dd>
    </div>
  );
}

function Env({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-2.5 last:border-b-0">
      <dt className="text-aux text-ink-3">{label}</dt>
      <dd className="text-right font-mono text-body text-ink">{value}</dd>
    </div>
  );
}

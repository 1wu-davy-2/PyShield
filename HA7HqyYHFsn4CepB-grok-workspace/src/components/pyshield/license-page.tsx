import { Check, Copy, Minus } from "lucide-react";
import {
  ENT_CUSTOMER,
  MACHINE,
  PRO_UNTIL,
  SERIAL,
  TRIAL_UNTIL,
  licenseCopy,
} from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Capsule, Card, inputClass } from "@/components/pyshield/bits";
import { cn } from "@/lib/cn";

export function LicensePage() {
  const state = usePyshield();
  const copy = licenseCopy(state.license, state.daysLeft, state.buildsLeft);
  const serviceDown = !state.serviceUp;
  const emphasize = state.license === "expired" || state.license === "trial" ? "pro" : state.license === "inactive" ? "free" : state.license;

  const facts = factsFor(state.license, state.daysLeft, state.buildsLeft, state.monthUsed);

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <Capsule tone={copy.tone}>{copy.badge}</Capsule>
            <h2 className="mt-3 text-page font-semibold text-ink">{copy.title}</h2>
            <p className="mt-1 max-w-2xl text-body text-ink-2">{copy.body}</p>
            {state.waiting ? (
              <p className="mt-3 flex items-center gap-2 text-body text-primary">
                <span className="size-2 animate-pulse rounded-full bg-primary" />
                等待授权回传 · 每 2.5 秒查询一次
              </p>
            ) : null}
          </div>
          <div className="min-w-0 rounded-ctrl bg-canvas px-4 py-3">
            <p className="text-aux text-ink-3">本机序列号</p>
            <p className="mt-1 font-mono text-title font-semibold tracking-wide text-ink">{SERIAL}</p>
            <Button
              variant="ghost"
              className="mt-2"
              onClick={() => state.copyText(SERIAL, "序列号已复制")}
            >
              <Copy className="size-4" />
              复制
            </Button>
          </div>
        </div>
        <dl className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {facts.map((fact) => (
            <div key={fact.label} className="rounded-ctrl bg-canvas px-3 py-2">
              <dt className="text-aux text-ink-3">{fact.label}</dt>
              <dd className="mt-0.5 truncate font-mono text-body font-medium text-ink">{fact.value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Plan
          name="免费版"
          badge="立即可用"
          badgeClass="bg-ok-soft text-ok-ink"
          line="14 天 · 3 次加密"
          points={["完整模块加密", "ChaCha20 运行时", "绑定本机"]}
          muted="不含原生运行时、产物签发、CI"
          action={copy.freeLabel}
          disabled={copy.freeDisabled || serviceDown}
          emphasize={emphasize === "free"}
          title={serviceDown ? "授权服务未配置，暂时无法在线申请" : undefined}
          onClick={state.openApply}
        />
        <Plan
          name="专业版"
          badge="按席位"
          badgeClass="bg-violet-soft text-violet"
          line="年订阅 · 月度配额"
          points={["原生运行时", "产物签发", "多项目协作"]}
          muted="需要商务开通"
          action="前往官网了解"
          disabled={serviceDown}
          emphasize={emphasize === "pro"}
          title={serviceDown ? "授权服务未配置，暂时无法在线申请" : undefined}
          onClick={() => state.openPlan("pro")}
        />
        <Plan
          name="企业版"
          badge="多席位"
          badgeClass="bg-primary-soft text-primary"
          line="私有部署 · 离线激活"
          points={["离线激活 / CI", "撤销 / 审计", "对接内部签发"]}
          muted="支持定制交付"
          action="联系企业版"
          disabled={serviceDown}
          emphasize={emphasize === "enterprise"}
          title={serviceDown ? "授权服务未配置，暂时无法在线申请" : undefined}
          onClick={() => state.openPlan("enterprise")}
        />
      </div>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-title font-semibold text-ink">授权服务</h2>
            <p className="mt-1 text-body text-ink-2">
              {state.serviceUp ? "已连接，可申请试用与刷新授权" : "未配置，请联系供应商获取授权"}
            </p>
          </div>
          <Capsule tone={state.serviceUp ? "ok" : "danger"}>{state.serviceUp ? "已连接" : "不可用"}</Capsule>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={state.refreshLicense}>
            刷新授权状态
          </Button>
          <Button variant="secondary" onClick={state.toggleActivate}>
            我已获得激活码
          </Button>
        </div>
        {state.activateOpen ? (
          <div className="mt-4">
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                className={cn(inputClass, "font-mono uppercase")}
                value={state.activateCode}
                placeholder="PS-XXXX-XXXX-XXXX"
                onChange={(event) => state.setActivateCode(event.target.value)}
              />
              <Button variant="primary" onClick={state.submitCode}>
                激活
              </Button>
            </div>
            <p className="mt-2 text-aux text-ink-4">
              演示：本机序列号开通试用，PS-PRO0-ACME-2604 开通专业版，PS-ENT0-NRTH-0091 开通企业版。
            </p>
          </div>
        ) : null}
        {state.license === "dev" ? (
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <input
              className={inputClass}
              value={state.serviceUrl}
              onChange={(event) => state.setServiceUrl(event.target.value)}
              aria-label="试用服务地址"
            />
            <Button variant="secondary" onClick={state.saveServiceUrl}>
              保存地址
            </Button>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

function factsFor(kind: ReturnType<typeof usePyshield.getState>["license"], days: number, left: number, monthUsed: number) {
  if (kind === "trial") {
    return [
      { label: "机器码", value: MACHINE },
      { label: "剩余天数", value: `${days} 天` },
      { label: "剩余构建", value: `${left}/3 次` },
      { label: "到期", value: TRIAL_UNTIL },
    ];
  }
  if (kind === "expired") {
    return [
      { label: "机器码", value: MACHINE },
      { label: "剩余天数", value: "已到期" },
      { label: "剩余构建", value: "0/3 次" },
      { label: "到期", value: TRIAL_UNTIL },
    ];
  }
  if (kind === "pro") {
    return [
      { label: "机器码", value: MACHINE },
      { label: "授权版本", value: "专业版" },
      { label: "本月配额", value: `${Math.max(0, 50 - monthUsed)}/50 剩余` },
      { label: "到期", value: PRO_UNTIL },
    ];
  }
  if (kind === "enterprise") {
    return [
      { label: "机器码", value: MACHINE },
      { label: "授权版本", value: "企业版" },
      { label: "席位", value: ENT_CUSTOMER },
      { label: "到期", value: PRO_UNTIL },
    ];
  }
  if (kind === "dev") {
    return [
      { label: "机器码", value: MACHINE },
      { label: "剩余天数", value: "不限" },
      { label: "剩余构建", value: "不限" },
      { label: "到期", value: "开发模式" },
    ];
  }
  return [
    { label: "机器码", value: MACHINE },
    { label: "剩余天数", value: "—" },
    { label: "剩余构建", value: "—" },
    { label: "到期", value: "—" },
  ];
}

function Plan({
  name,
  badge,
  badgeClass,
  line,
  points,
  muted,
  action,
  disabled,
  emphasize,
  title,
  onClick,
}: {
  name: string;
  badge: string;
  badgeClass: string;
  line: string;
  points: string[];
  muted: string;
  action: string;
  disabled: boolean;
  emphasize: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <Card className={cn("flex h-full flex-col", emphasize && "ring-2 ring-primary")}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-title font-semibold text-ink">{name}</h3>
        <span className={cn("rounded-full px-2 py-1 text-aux font-medium", badgeClass)}>{badge}</span>
      </div>
      <p className="mt-2 text-body text-ink-2">{line}</p>
      <ul className="mt-4 flex flex-col gap-2">
        {points.map((point) => (
          <li key={point} className="flex gap-2 text-body text-ink-2">
            <Check className="mt-0.5 size-4 shrink-0 text-ok" />
            {point}
          </li>
        ))}
        <li className="flex gap-2 text-body text-ink-4">
          <Minus className="mt-0.5 size-4 shrink-0" />
          {muted}
        </li>
      </ul>
      <Button variant={emphasize && !disabled ? "primary" : "secondary"} className="mt-5 w-full" disabled={disabled} title={title} onClick={onClick}>
        {action}
      </Button>
    </Card>
  );
}

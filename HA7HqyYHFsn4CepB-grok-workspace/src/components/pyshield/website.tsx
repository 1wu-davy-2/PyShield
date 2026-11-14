import { useState } from "react";
import { Check } from "lucide-react";
import { MACHINE, SERIAL, TRIAL_UNTIL, proUnlocked } from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Mark, inputClass } from "@/components/pyshield/bits";
import { cn } from "@/lib/cn";

export function BrowserWindow() {
  const web = usePyshield((state) => state.web);
  const plan = usePyshield((state) => state.contactPlan);
  const back = usePyshield((state) => state.backToDesktop);
  const url =
    web === "home"
      ? "https://license.pyshield.dev/"
      : web === "activate"
        ? `https://license.pyshield.dev/activate?serial=${SERIAL}`
        : web === "success"
          ? "https://license.pyshield.dev/activate/done"
          : `https://license.pyshield.dev/contact?plan=${plan}&serial=${SERIAL}`;

  return (
    <div className="mx-auto flex h-full min-h-[36rem] w-full max-w-[1440px] flex-col overflow-hidden rounded-2xl bg-card shadow-window">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-sidebar px-3">
        <span className="flex gap-1.5" aria-hidden>
          <Dot className="bg-danger" />
          <Dot className="bg-warn" />
          <Dot className="bg-ok" />
        </span>
        <p className="min-w-0 flex-1 truncate rounded-full bg-card px-3 py-1.5 font-mono text-aux text-ink-2">{url}</p>
        <button type="button" onClick={back} className="shrink-0 text-aux font-medium text-primary">
          返回桌面
        </button>
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8 md:py-12">
          {web === "home" ? <WebHome /> : null}
          {web === "activate" ? <WebActivate /> : null}
          {web === "success" ? <WebSuccess /> : null}
          {web === "contact" ? <WebContact /> : null}
        </div>
      </div>
    </div>
  );
}

function Dot({ className }: { className: string }) {
  return <span className={cn("size-2.5 rounded-full", className)} />;
}

function WebHead({ title, sub }: { title: string; sub: string }) {
  return (
    <header className="mb-8">
      <div className="flex items-center gap-2">
        <Mark />
        <span className="text-title font-semibold text-ink">PyShield</span>
      </div>
      <h1 className="mt-6 text-page font-semibold text-ink">{title}</h1>
      <p className="mt-2 max-w-2xl text-body text-ink-2">{sub}</p>
    </header>
  );
}

function WebHome() {
  const setWeb = usePyshield((state) => state.setWeb);
  const toast = usePyshield((state) => state.toast);
  const [serial, setSerial] = useState("");

  const submit = () => {
    const value = serial.trim().toUpperCase();
    if (value === SERIAL) {
      setWeb("activate");
      return;
    }
    if (!/^PS-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(value)) {
      toast("序列号格式应为 PS-XXXX-XXXX-XXXX", "warn");
      return;
    }
    toast("未找到这个序列号", "danger");
  };

  return (
    <>
      <WebHead title="PyShield 版本与试用" sub="还没有从桌面应用带入序列号。把它贴在下面，就可以继续申请试用。" />
      <div className="max-w-lg rounded-card bg-canvas p-5">
        <label className="text-aux font-medium text-ink-3" htmlFor="serial">
          本机序列号
        </label>
        <input
          id="serial"
          className={cn(inputClass, "mt-2 bg-card font-mono uppercase")}
          value={serial}
          placeholder={SERIAL}
          onChange={(event) => setSerial(event.target.value)}
        />
        <Button variant="primary" className="mt-4" onClick={submit}>
          继续
        </Button>
      </div>
    </>
  );
}

function WebActivate() {
  const license = usePyshield((state) => state.license);
  const serviceUp = usePyshield((state) => state.serviceUp);
  const submitTrial = usePyshield((state) => state.submitTrial);
  const openPlan = usePyshield((state) => state.openPlan);
  const back = usePyshield((state) => state.backToDesktop);
  const [busy, setBusy] = useState(false);
  const opened = license === "trial";
  const formal = proUnlocked(license);

  const apply = () => {
    setBusy(true);
    window.setTimeout(() => {
      setBusy(false);
      submitTrial();
    }, 700);
  };

  const status = formal ? "已是正式授权" : opened ? `已开通 · 到期 ${TRIAL_UNTIL}` : license === "expired" ? "试用已结束" : "待申请";

  return (
    <>
      <WebHead
        title="PyShield 版本与试用"
        sub="本页已绑定你的本机序列号。申请通过后，授权会自动回传到桌面应用。"
      />
      <div className="grid gap-3 rounded-card bg-canvas p-4 sm:grid-cols-3">
        <Fact k="序列号" v={SERIAL} />
        <Fact k="机器码" v={MACHINE} />
        <Fact k="状态" v={status} />
      </div>
      <h2 className="mb-3 mt-8 text-title font-semibold text-ink">选择版本</h2>
      <div className="grid gap-4 md:grid-cols-3">
        <SitePlan
          name="免费版"
          line="14 天 · 3 次"
          action={busy ? "正在签发…" : opened ? "返回 PyShield" : "申请试用"}
          disabled={!serviceUp || license === "expired" || formal || busy}
          onClick={opened ? back : apply}
        />
        <SitePlan name="专业版" line="按席位年订阅" action="了解专业版" disabled={!serviceUp} onClick={() => openPlan("pro")} />
        <SitePlan name="企业版" line="多席位私有部署" action="联系企业版" disabled={!serviceUp} onClick={() => openPlan("enterprise")} />
      </div>
      {!serviceUp ? <p className="mt-4 text-body text-danger-ink">授权服务未配置，暂时无法在线申请。</p> : null}
    </>
  );
}

function WebSuccess() {
  const activate = usePyshield((state) => state.activateFromSite);
  return (
    <>
      <WebHead title="试用已开通" sub="授权已经写入服务端，正在尝试唤起桌面应用完成激活。" />
      <div className="grid gap-3 rounded-card bg-ok-soft p-4 sm:grid-cols-2">
        <Fact k="序列号" v={SERIAL} />
        <Fact k="到期" v={TRIAL_UNTIL} />
        <Fact k="时长" v="14 天" />
        <Fact k="配额" v="3 次加密构建" />
        <Fact k="机器码" v={MACHINE} />
      </div>
      <div className="mt-4 max-w-xl rounded-card bg-canvas p-5">
        <p className="text-body text-ink">点击下面的按钮回到 PyShield。</p>
        <p className="mt-1 text-aux text-ink-3">浏览器若询问是否打开 PyShield，请允许。应用也会自动轮询，几秒内自己切到前台。</p>
        <Button variant="primary" className="mt-4" onClick={activate}>
          打开 PyShield 并激活
        </Button>
      </div>
    </>
  );
}

function WebContact() {
  const plan = usePyshield((state) => state.contactPlan);
  const setWeb = usePyshield((state) => state.setWeb);
  const title = plan === "pro" ? "专业版" : "企业版";
  return (
    <>
      <WebHead
        title={`联系${title}`}
        sub={
          plan === "pro"
            ? "专业版按席位年订阅，包含原生运行时、产物签发和多项目协作。留下序列号即可由商务开通。"
            : "企业版支持私有部署、离线激活、撤销审计，以及按你们的签发系统定制。"
        }
      />
      <div className="max-w-xl rounded-card bg-canvas p-5">
        <Fact k="所选版本" v={title} />
        <div className="mt-3">
          <Fact k="序列号" v={SERIAL} />
        </div>
        <p className="mt-4 text-body text-ink-2">我们会按这个序列号核对机器码。此页不收集付款信息。</p>
        <Button className="mt-4" onClick={() => setWeb("activate")}>
          返回版本选择
        </Button>
      </div>
    </>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <p className="text-aux text-ink-3">{k}</p>
      <p className="truncate font-mono text-body font-medium text-ink">{v}</p>
    </div>
  );
}

function SitePlan({
  name,
  line,
  action,
  disabled,
  onClick,
}: {
  name: string;
  line: string;
  action: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <div className="flex flex-col rounded-card bg-card p-4 shadow-card">
      <div className="flex items-center gap-2">
        <Check className="size-4 text-primary" />
        <h3 className="text-title font-semibold text-ink">{name}</h3>
      </div>
      <p className="mt-2 text-body text-ink-2">{line}</p>
      <Button variant="primary" className="mt-4 w-full" disabled={disabled} onClick={onClick}>
        {action}
      </Button>
    </div>
  );
}

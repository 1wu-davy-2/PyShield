import { useState } from "react";
import { AlertTriangle, Copy } from "lucide-react";
import { MACHINE, PROJECTS, SERIAL, algoLabel, fakeKey, projectById } from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Modal } from "@/components/pyshield/bits";
import { cn } from "@/lib/cn";

export function Modals() {
  const modal = usePyshield((state) => state.modal);
  if (modal === "trial-ended") return <TrialModal />;
  if (modal === "picker") return <PickerModal />;
  if (modal === "output") return <OutputModal />;
  if (modal === "key") return <KeyModal />;
  if (modal === "import") return <ImportModal />;
  if (modal === "offline") return <OfflineModal />;
  if (modal === "clear") return <ClearModal />;
  if (modal === "release") return <ReleaseModal />;
  return null;
}

function TrialModal() {
  const close = usePyshield((state) => state.closeModal);
  const openPlan = usePyshield((state) => state.openPlan);
  const left = usePyshield((state) => state.buildsLeft);
  const quota = left <= 0;
  return (
    <Modal onClose={close} className="max-w-sm text-center">
      <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-warn-soft text-warn-ink">
        <AlertTriangle className="size-6" />
      </span>
      <h2 className="mt-3 text-page font-semibold text-ink">试用已结束</h2>
      <p className="mt-2 text-body text-ink-2">
        {quota
          ? "3 次试用构建额度已用完。购买专业版后可以继续构建，并解锁原生运行时与产物许可证签发。"
          : "14 天试用期已结束。购买专业版后可以继续构建，并解锁原生运行时与产物许可证签发。"}
      </p>
      <div className="mt-5 flex gap-2">
        <Button
          variant="primary"
          className="flex-1"
          onClick={() => {
            close();
            openPlan("pro");
          }}
        >
          购买专业版
        </Button>
        <Button className="flex-1" onClick={close}>
          稍后再说
        </Button>
      </div>
    </Modal>
  );
}

function PickerModal() {
  const close = usePyshield((state) => state.closeModal);
  const select = usePyshield((state) => state.selectProject);
  const [id, setId] = useState(PROJECTS[0]?.id ?? "");
  return (
    <Modal title="选择 Django 项目目录" onClose={close}>
      <ul className="flex flex-col gap-1">
        {PROJECTS.map((project) => (
          <li key={project.id}>
            <button
              type="button"
              onClick={() => setId(project.id)}
              className={cn(
                "flex w-full flex-col items-start rounded-ctrl px-3 py-2 text-left",
                id === project.id ? "bg-primary-soft" : "hover:bg-canvas",
              )}
            >
              <span className="text-body font-medium text-ink">{project.name}</span>
              <span className="font-mono text-aux text-ink-3">{project.path}</span>
              <span className="text-aux text-ink-4">
                {project.django ? `${project.modules} 个模块` : "不是 Django 项目"}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={close}>取消</Button>
        <Button variant="primary" onClick={() => select(id)}>
          选择此文件夹
        </Button>
      </div>
    </Modal>
  );
}

function OutputModal() {
  const close = usePyshield((state) => state.closeModal);
  const choose = usePyshield((state) => state.chooseOutput);
  const project = projectById(usePyshield((state) => state.projectId));
  const suffix = usePyshield((state) => state.suffix);
  const current = usePyshield((state) => state.outputDir);
  if (!project) return null;
  const options = [`${project.path}${suffix}`, `E:\\dist\\${project.name}${suffix}`];
  return (
    <Modal title="选择输出目录" onClose={close}>
      <ul className="flex flex-col gap-1">
        {options.map((dir) => (
          <li key={dir}>
            <button
              type="button"
              onClick={() => choose(dir)}
              className={cn(
                "w-full rounded-ctrl px-3 py-3 text-left font-mono text-body",
                dir === current ? "bg-primary-soft text-primary" : "text-ink hover:bg-canvas",
              )}
            >
              {dir}
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end">
        <Button onClick={close}>取消</Button>
      </div>
    </Modal>
  );
}

function KeyModal() {
  const close = usePyshield((state) => state.closeModal);
  const keyFor = usePyshield((state) => state.keyFor);
  const copyText = usePyshield((state) => state.copyText);
  if (!keyFor) return null;
  const body = fakeKey(keyFor.project, algoLabel(keyFor.algo));
  const block = `-----BEGIN PYSHIELD KEY-----\n${body}\n-----END PYSHIELD KEY-----`;
  return (
    <Modal title="构建密钥" onClose={close} className="max-w-lg">
      <dl className="grid grid-cols-2 gap-2 text-body">
        <div>
          <dt className="text-aux text-ink-3">项目</dt>
          <dd className="font-medium text-ink">{keyFor.project}</dd>
        </div>
        <div>
          <dt className="text-aux text-ink-3">算法</dt>
          <dd className="font-medium text-ink">{algoLabel(keyFor.algo)}</dd>
        </div>
        <div>
          <dt className="text-aux text-ink-3">输出</dt>
          <dd className="truncate font-mono text-aux text-ink">{keyFor.path}</dd>
        </div>
        <div>
          <dt className="text-aux text-ink-3">时间</dt>
          <dd className="font-mono text-ink">{keyFor.time}</dd>
        </div>
      </dl>
      <pre className="mt-4 overflow-auto rounded-ctrl bg-canvas p-3 font-mono text-aux leading-5 text-ink-2">{block}</pre>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={close}>关闭</Button>
        <Button variant="primary" onClick={() => copyText(block, "构建密钥已复制")}>
          <Copy className="size-4" />
          复制
        </Button>
      </div>
    </Modal>
  );
}

function ImportModal() {
  const close = usePyshield((state) => state.closeModal);
  const importLicense = usePyshield((state) => state.importLicense);
  return (
    <Modal title="导入授权文件" onClose={close}>
      <ul className="flex flex-col gap-2">
        <li>
          <button type="button" className="w-full rounded-ctrl bg-canvas px-3 py-3 text-left" onClick={() => importLicense("pro")}>
            <span className="block text-body font-medium text-ink">PyShield-Pro-Acme.lic</span>
            <span className="text-aux text-ink-3">专业版 · {SERIAL}</span>
          </button>
        </li>
        <li>
          <button
            type="button"
            className="w-full rounded-ctrl bg-canvas px-3 py-3 text-left"
            onClick={() => importLicense("enterprise")}
          >
            <span className="block text-body font-medium text-ink">PyShield-Enterprise-Northwind.lic</span>
            <span className="text-aux text-ink-3">企业版 · 离线席位</span>
          </button>
        </li>
      </ul>
      <div className="mt-4 flex justify-end">
        <Button onClick={close}>取消</Button>
      </div>
    </Modal>
  );
}

function OfflineModal() {
  const close = usePyshield((state) => state.closeModal);
  const apply = usePyshield((state) => state.applyOffline);
  const copyText = usePyshield((state) => state.copyText);
  const [text, setText] = useState("");
  const request = JSON.stringify(
    { serial: SERIAL, machine: MACHINE, nonce: "8c21aa" },
    null,
    2,
  );
  return (
    <Modal title="离线请求 / 响应" onClose={close} className="max-w-lg">
      <p className="text-aux text-ink-3">把下面的请求交给签发方，再把响应粘回来。</p>
      <pre className="mt-2 overflow-auto rounded-ctrl bg-canvas p-3 font-mono text-aux text-ink-2">{request}</pre>
      <Button className="mt-2" onClick={() => copyText(request, "离线请求已复制")}>
        复制请求
      </Button>
      <textarea
        className="mt-4 h-24 w-full rounded-ctrl border border-line p-3 font-mono text-aux text-ink outline-none focus:border-primary"
        placeholder="粘贴含 PRO、ENT 或 TRIAL 的响应"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="mt-3 flex justify-end gap-2">
        <Button onClick={close}>关闭</Button>
        <Button variant="primary" onClick={() => apply(text)}>
          应用离线响应
        </Button>
      </div>
    </Modal>
  );
}

function ClearModal() {
  const close = usePyshield((state) => state.closeModal);
  const clear = usePyshield((state) => state.clearHistory);
  return (
    <Modal title="清空构建记录？" onClose={close}>
      <p className="text-body text-ink-2">本地列表会被清空。已经生成的输出目录不会删除。</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={close}>取消</Button>
        <Button variant="primary" onClick={clear}>
          清空
        </Button>
      </div>
    </Modal>
  );
}

function ReleaseModal() {
  const close = usePyshield((state) => state.closeModal);
  const release = usePyshield((state) => state.releaseSeat);
  return (
    <Modal title="解除本机授权？" onClose={close}>
      <p className="text-body text-ink-2">解除后需要重新申请试用或导入授权，才能再次构建。</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={close}>取消</Button>
        <Button variant="primary" onClick={release}>
          解除
        </Button>
      </div>
    </Modal>
  );
}

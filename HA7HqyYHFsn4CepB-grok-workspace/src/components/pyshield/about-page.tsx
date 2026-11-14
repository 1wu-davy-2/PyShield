import { APP_VERSION } from "@/lib/pyshield/model";
import { Card, Mark } from "@/components/pyshield/bits";

const STEPS = [
  { n: "01", title: "模块加密", body: "把 .py 换成加载 stub。原字节用 ChaCha20 或 AES 加密后存放，打开文件看不到业务源码。" },
  { n: "02", title: "运行时注入", body: "受保护的导入钩子在进程内解密。磁盘上不还原成可读的 Python 文件。" },
  { n: "03", title: "许可证签名", body: "产物绑定客户、设备指纹和有效期。签名使用构建器持有的根密钥。" },
  { n: "04", title: "完整性清单", body: "启动时核对核心文件哈希。文件被替换或裁剪时拒绝加载。" },
];

const BOUNDS = [
  {
    title: "可以做到",
    tone: "text-ok-ink",
    items: ["阻止直接打开阅读业务源码", "把运行限制在许可证声明的设备和期限", "发现核心文件被替换"],
  },
  {
    title: "无法绝对阻止",
    tone: "text-warn-ink",
    items: ["对运行中的进程做内存转储", "有调试器和足够时间的逆向", "已授权机器上的人为泄漏"],
  },
  {
    title: "必须守住",
    tone: "text-danger-ink",
    items: ["根公钥和许可证私钥", "构建机与离线响应文件", "客户侧的授权和吊销流程"],
  },
];

export function AboutPage() {
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex items-start gap-3">
          <Mark className="size-12" />
          <div>
            <h2 className="text-page font-semibold text-ink">PyShield</h2>
            <p className="mt-1 font-mono text-aux text-ink-3">{APP_VERSION} · 面向 Django 的本地加密构建器</p>
          </div>
        </div>
        <p className="mt-4 max-w-3xl text-body text-ink-2">
          PyShield 在你自己的机器上把 Python 模块换成不可读的 stub，并注入运行时、许可证和完整性清单，得到一份可以部署的受保护副本。源码不会上传。
        </p>
      </Card>
      <Card>
        <h2 className="text-title font-semibold text-ink">保护链路</h2>
        <ol className="mt-4 grid gap-4 md:grid-cols-2">
          {STEPS.map((step) => (
            <li key={step.n} className="rounded-ctrl bg-canvas p-4">
              <p className="font-mono text-aux font-semibold text-primary">{step.n}</p>
              <p className="mt-1 text-body font-semibold text-ink">{step.title}</p>
              <p className="mt-1 text-body text-ink-2">{step.body}</p>
            </li>
          ))}
        </ol>
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        {BOUNDS.map((bound) => (
          <Card key={bound.title}>
            <h2 className={`text-title font-semibold ${bound.tone}`}>{bound.title}</h2>
            <ul className="mt-3 flex flex-col gap-2">
              {bound.items.map((item) => (
                <li key={item} className="text-body text-ink-2">
                  {item}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}

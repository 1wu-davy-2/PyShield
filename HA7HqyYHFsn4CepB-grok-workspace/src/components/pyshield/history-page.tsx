import { FileClock } from "lucide-react";
import type { HistoryStatus } from "@/lib/pyshield/model";
import { usePyshield } from "@/lib/pyshield/store";
import { Button, Card } from "@/components/pyshield/bits";
import { cn } from "@/lib/cn";

const STATUS: Record<HistoryStatus, { label: string; className: string }> = {
  success: { label: "成功", className: "bg-ok-soft text-ok-ink" },
  failed: { label: "失败", className: "bg-danger-soft text-danger-ink" },
  cancelled: { label: "已取消", className: "bg-warn-soft text-warn-ink" },
};

export function HistoryPage() {
  const history = usePyshield((state) => state.history);
  const askClear = usePyshield((state) => state.askClear);
  const openOutput = usePyshield((state) => state.openOutput);
  const openKey = usePyshield((state) => state.openKey);
  const setPage = usePyshield((state) => state.setPage);

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-title font-semibold text-ink">构建记录</h2>
          <p className="text-aux text-ink-3">最多保留 100 条。失败记录同样留下，便于回看日志。</p>
        </div>
        <Button variant="ghost" onClick={askClear}>
          清空记录
        </Button>
      </div>
      {history.length === 0 ? (
        <div className="flex flex-col items-center px-4 py-16 text-center">
          <span className="flex size-14 items-center justify-center rounded-full bg-canvas text-ink-3">
            <FileClock className="size-6" />
          </span>
          <p className="mt-4 text-title font-medium text-ink">还没有构建记录</p>
          <p className="mt-1 max-w-sm text-body text-ink-3">完成一次加密构建后，时间、体积和结果会留在这里。</p>
          <Button variant="primary" className="mt-5" onClick={() => setPage("build")}>
            去构建
          </Button>
        </div>
      ) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left">
            <thead>
              <tr className="border-b border-line text-aux text-ink-3">
                <th className="px-2 py-2 font-medium">时间</th>
                <th className="px-2 py-2 font-medium">项目</th>
                <th className="px-2 py-2 font-medium">状态</th>
                <th className="px-2 py-2 font-medium">模块</th>
                <th className="px-2 py-2 font-medium">体积</th>
                <th className="px-2 py-2 font-medium">耗时</th>
                <th className="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {history.map((item) => {
                const status = STATUS[item.status];
                return (
                  <tr key={item.id} className="border-b border-line last:border-b-0">
                    <td className="whitespace-nowrap px-2 py-3 font-mono text-aux text-ink-2">{item.time}</td>
                    <td className="px-2 py-3 text-body font-medium text-ink">{item.project}</td>
                    <td className="px-2 py-3">
                      <span className={cn("rounded-full px-2 py-1 text-aux font-medium", status.className)}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-2 py-3 font-mono text-body tabular-nums text-ink-2">{item.modules}</td>
                    <td className="px-2 py-3 font-mono text-body tabular-nums text-ink-2">{item.size}</td>
                    <td className="px-2 py-3 font-mono text-body tabular-nums text-ink-2">{item.duration}</td>
                    <td className="px-2 py-3">
                      <div className="flex gap-1">
                        <Button variant="ghost" onClick={() => openOutput(item.path, item.status === "success")}>
                          打开
                        </Button>
                        <Button variant="ghost" onClick={() => openKey(item)}>
                          密钥
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

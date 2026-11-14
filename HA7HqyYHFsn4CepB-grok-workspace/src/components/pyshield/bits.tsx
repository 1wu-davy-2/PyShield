import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Shield } from "lucide-react";
import { cn } from "@/lib/cn";

export const inputClass =
  "h-11 w-full rounded-ctrl border border-line bg-card px-3 text-body text-ink outline-none transition-colors focus:border-primary disabled:cursor-not-allowed disabled:bg-canvas disabled:text-ink-4";

type Variant = "primary" | "secondary" | "ghost";

export function Button({
  variant = "secondary",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex min-h-11 items-center justify-center gap-1.5 rounded-ctrl px-3 text-body font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
        variant === "primary" && "bg-primary text-on-primary hover:bg-primary-deep",
        variant === "secondary" && "border border-line bg-card text-ink hover:bg-canvas",
        variant === "ghost" && "bg-canvas text-ink-2 hover:bg-line",
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-card bg-card p-4 shadow-card md:p-5", className)}>{children}</section>;
}

export function CardTitle({ title, extra }: { title: string; extra?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-title font-semibold text-ink">{title}</h2>
      {extra}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-aux font-medium text-ink-3">{label}</span>
      {children}
    </div>
  );
}

export function Mark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-ctrl bg-primary text-on-primary",
        className,
      )}
    >
      <Shield className="size-5" aria-hidden />
    </span>
  );
}

export function ProMark() {
  return (
    <span className="rounded-full bg-violet-soft px-1.5 py-0.5 text-aux font-semibold leading-none text-violet">
      Pro
    </span>
  );
}

export function Segment<T extends string>({
  value,
  options,
  onChange,
  locked,
}: {
  value: T;
  options: { id: T; label: string; pro?: boolean }[];
  onChange: (id: T) => void;
  locked: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1 rounded-ctrl bg-canvas p-1">
      {options.map((option) => {
        const disabled = Boolean(option.pro && locked);
        const on = value === option.id && !disabled;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(option.id)}
            className={cn(
              "inline-flex min-h-10 items-center gap-1.5 rounded-ctrl px-3 text-body",
              on && "bg-card font-medium text-primary shadow-card",
              !on && !disabled && "text-ink-2 hover:bg-card",
              disabled && "text-ink-4",
            )}
          >
            {option.label}
            {disabled ? <ProMark /> : null}
          </button>
        );
      })}
    </div>
  );
}

export function Switch({
  on,
  label,
  onClick,
}: {
  on: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onClick}
      className="flex h-11 w-14 items-center justify-center"
    >
      <span className={cn("flex h-6 w-11 items-center rounded-full p-0.5", on ? "bg-primary" : "bg-line-strong")}>
        <span
          className={cn(
            "size-5 rounded-full bg-card shadow-card transition-transform",
            on && "translate-x-5",
          )}
        />
      </span>
    </button>
  );
}

export function Capsule({
  tone,
  children,
  onClick,
}: {
  tone: "warn" | "ok" | "danger" | "neutral";
  children: ReactNode;
  onClick?: () => void;
}) {
  const className = cn(
    "inline-flex min-h-10 items-center gap-1.5 rounded-full px-3 text-aux font-medium",
    tone === "warn" && "bg-warn-soft text-warn-ink",
    tone === "ok" && "bg-ok-soft text-ok-ink",
    tone === "danger" && "bg-danger-soft text-danger-ink",
    tone === "neutral" && "bg-canvas text-ink-2",
  );
  const dot = cn(
    "size-1.5 rounded-full",
    tone === "warn" && "bg-warn",
    tone === "ok" && "bg-ok",
    tone === "danger" && "bg-danger",
    tone === "neutral" && "bg-ink-4",
  );
  const inner = (
    <>
      <span className={dot} aria-hidden />
      {children}
    </>
  );
  if (!onClick) return <span className={className}>{inner}</span>;
  return (
    <button type="button" onClick={onClick} className={className}>
      {inner}
    </button>
  );
}

export function Modal({
  title,
  onClose,
  children,
  className,
}: {
  title?: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center p-4">
      <button type="button" className="absolute inset-0 bg-ink/45 backdrop-blur-sm" aria-label="关闭对话框" onClick={onClose} />
      <div className={cn("relative z-10 max-h-full w-full max-w-md overflow-auto rounded-card bg-card p-5 shadow-pop", className)}>
        {title ? <h2 className="text-title font-semibold text-ink">{title}</h2> : null}
        <div className={title ? "mt-4" : undefined}>{children}</div>
      </div>
    </div>
  );
}

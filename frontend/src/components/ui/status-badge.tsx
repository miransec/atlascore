import { cn } from "@/lib/cn";

type Tone = "neutral" | "success" | "warning" | "danger" | "accent" | "info";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-overlay text-muted border-border",
  success: "bg-success-soft text-success border-success/30",
  warning: "bg-warning-soft text-warning border-warning/30",
  danger: "bg-danger-soft text-danger border-danger/30",
  accent: "bg-accent-soft text-accent-hover border-accent/30",
  info: "bg-accent-soft text-accent-hover border-accent/30",
};

export function StatusBadge({
  children,
  tone = "neutral",
  pulse,
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        tones[tone],
        className,
      )}
    >
      {pulse ? (
        <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-current" />
      ) : null}
      {children}
    </span>
  );
}

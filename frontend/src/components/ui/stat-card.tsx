import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  className,
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "group rounded-lg border border-border bg-surface-raised p-4 shadow-sm transition-transform hover:-translate-y-0.5 hover:shadow-md",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight text-foreground">{value}</p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </div>
        {Icon ? (
          <div className="rounded-md border border-border bg-surface p-2 text-accent-hover">
            <Icon className="h-4 w-4" />
          </div>
        ) : null}
      </div>
    </div>
  );
}

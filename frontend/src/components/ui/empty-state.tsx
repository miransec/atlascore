import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

export function EmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  onAction,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface/40 px-6 py-14 text-center animate-fade-in",
        className,
      )}
    >
      {Icon ? (
        <div className="mb-4 rounded-full border border-border bg-surface-raised p-3 text-muted">
          <Icon className="h-5 w-5" />
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description ? (
        <p className="mt-1.5 max-w-sm text-sm text-muted">{description}</p>
      ) : null}
      {actionLabel && onAction ? (
        <Button className="mt-5" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}

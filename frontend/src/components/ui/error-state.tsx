import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
  className,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-danger/30 bg-danger-soft/40 px-6 py-10 text-center",
        className,
      )}
      role="alert"
    >
      <AlertTriangle className="mb-3 h-5 w-5 text-danger" />
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      {onRetry ? (
        <Button variant="secondary" className="mt-4" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

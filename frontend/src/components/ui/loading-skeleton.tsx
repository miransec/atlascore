import { cn } from "@/lib/cn";

export function LoadingSkeleton({
  className,
  lines = 3,
}: {
  className?: string;
  lines?: number;
}) {
  return (
    <div className={cn("space-y-3", className)} aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded-md bg-[linear-gradient(90deg,var(--surface-raised),var(--surface-overlay),var(--surface-raised))] bg-[length:200%_100%] animate-skeleton"
          style={{ width: `${88 - i * 12}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "rounded-md bg-[linear-gradient(90deg,var(--surface-raised),var(--surface-overlay),var(--surface-raised))] bg-[length:200%_100%] animate-skeleton",
        className,
      )}
      aria-hidden
    />
  );
}

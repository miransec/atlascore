import { type ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface Column<T> {
  key: string;
  header: string;
  className?: string;
  cell: (row: T) => ReactNode;
}

export function DataTable<T extends { id: string }>({
  columns,
  rows,
  empty,
  className,
}: {
  columns: Column<T>[];
  rows: T[];
  empty?: ReactNode;
  className?: string;
}) {
  if (rows.length === 0 && empty) return <>{empty}</>;

  return (
    <div className={cn("overflow-hidden rounded-lg border border-border", className)}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left text-sm">
          <thead className="bg-surface border-b border-border">
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={cn(
                    "px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-muted",
                    c.className,
                  )}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-border-subtle last:border-0 hover:bg-surface-raised/60"
              >
                {columns.map((c) => (
                  <td key={c.key} className={cn("px-4 py-3 text-foreground", c.className)}>
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

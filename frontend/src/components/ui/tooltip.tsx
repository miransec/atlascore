"use client";

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Tooltip({
  content,
  children,
  side = "right",
}: {
  content: string;
  children: ReactNode;
  side?: "right" | "top" | "bottom";
}) {
  const [show, setShow] = useState(false);
  const pos =
    side === "right"
      ? "left-full top-1/2 ml-2 -translate-y-1/2"
      : side === "top"
        ? "bottom-full left-1/2 mb-2 -translate-x-1/2"
        : "top-full left-1/2 mt-2 -translate-x-1/2";

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      onFocus={() => setShow(true)}
      onBlur={() => setShow(false)}
    >
      {children}
      {show ? (
        <span
          role="tooltip"
          className={cn(
            "pointer-events-none absolute z-50 whitespace-nowrap rounded-md border border-border bg-surface-overlay px-2 py-1 text-xs text-foreground shadow-md animate-fade-in",
            pos,
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}

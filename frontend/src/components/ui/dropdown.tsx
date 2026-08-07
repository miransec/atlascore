"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

interface DropdownProps {
  trigger: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}

export function Dropdown({
  trigger,
  children,
  align = "left",
  className,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className={cn("relative inline-block", className)}>
      <div onClick={() => setOpen((v) => !v)}>{trigger}</div>
      {open ? (
        <div
          role="menu"
          className={cn(
            "absolute z-40 mt-2 min-w-[12rem] overflow-hidden rounded-md border border-border bg-surface-raised p-1 shadow-md animate-slide-up",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          <div onClick={() => setOpen(false)}>{children}</div>
        </div>
      ) : null}
    </div>
  );
}

export function DropdownItem({
  children,
  onClick,
  danger,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 rounded-sm px-2.5 py-1.5 text-left text-sm transition-colors",
        danger ? "text-danger hover:bg-danger-soft" : "text-foreground hover:bg-surface-overlay",
        disabled && "pointer-events-none opacity-50",
      )}
    >
      {children}
    </button>
  );
}

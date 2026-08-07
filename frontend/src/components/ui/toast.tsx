"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

type ToastTone = "default" | "success" | "danger" | "warning";

interface ToastItem {
  id: string;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastApi {
  push: (toast: Omit<ToastItem, "id" | "tone"> & { tone?: ToastTone }) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const toneClass: Record<ToastTone, string> = {
  default: "border-border",
  success: "border-success/40 bg-success-soft",
  danger: "border-danger/40 bg-danger-soft",
  warning: "border-warning/40 bg-warning-soft",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast: Omit<ToastItem, "id" | "tone"> & { tone?: ToastTone }) => {
      const id = crypto.randomUUID();
      setItems((prev) => [
        ...prev,
        { id, title: toast.title, description: toast.description, tone: toast.tone ?? "default" },
      ]);
      window.setTimeout(() => dismiss(id), 4200);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, description) => push({ title, description, tone: "success" }),
      error: (title, description) => push({ title, description, tone: "danger" }),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2"
        aria-live="polite"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto animate-slide-up rounded-md border bg-surface-raised px-4 py-3 shadow-md",
              toneClass[t.tone],
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-foreground">{t.title}</p>
                {t.description ? (
                  <p className="mt-0.5 text-xs text-muted">{t.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                className="text-muted hover:text-foreground"
                onClick={() => dismiss(t.id)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

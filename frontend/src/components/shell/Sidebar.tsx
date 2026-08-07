"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  Building2,
  ChevronLeft,
  ChevronRight,
  FileText,
  FolderOpen,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MessageSquareText,
  Search,
  Settings2,
  Shield,
  Users,
  UsersRound,
  Boxes,
  ScrollText,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { Tooltip } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth-context";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV: NavGroup[] = [
  {
    title: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/dashboard/activity", label: "Activity", icon: Activity },
    ],
  },
  {
    title: "Knowledge",
    items: [
      { href: "/dashboard/sources", label: "Sources", icon: FolderOpen },
      { href: "/dashboard/documents", label: "Documents", icon: FileText },
      { href: "/dashboard/search", label: "Search", icon: Search },
      { href: "/dashboard/answer", label: "Ask AI", icon: MessageSquareText },
    ],
  },
  {
    title: "Workspace",
    items: [
      { href: "/dashboard/workspaces", label: "Workspaces", icon: Boxes },
      { href: "/dashboard/members", label: "Members", icon: Users },
      { href: "/dashboard/teams", label: "Teams", icon: UsersRound },
    ],
  },
  {
    title: "Security",
    items: [
      { href: "/dashboard/api-keys", label: "API Keys", icon: KeyRound },
      { href: "/dashboard/service-accounts", label: "Service Accounts", icon: Shield },
      { href: "/dashboard/audit", label: "Audit Logs", icon: ScrollText },
    ],
  },
  {
    title: "Settings",
    items: [
      { href: "/dashboard/settings/organisation", label: "Organisation", icon: Building2 },
      { href: "/dashboard/settings/providers", label: "AI Providers", icon: Bot },
      { href: "/dashboard/settings/preferences", label: "Preferences", icon: SlidersHorizontal },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({
  collapsed,
  onToggle,
  onLogout,
}: {
  collapsed: boolean;
  onToggle: () => void;
  onLogout: () => void;
}) {
  const pathname = usePathname();
  const { user } = useAuth();

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col border-r border-border bg-surface/95 backdrop-blur-md transition-[width] duration-200",
        collapsed ? "w-[var(--sidebar-collapsed)]" : "w-[var(--sidebar-width)]",
      )}
    >
      <div className="flex h-14 items-center justify-between border-b border-border px-3">
        {!collapsed ? (
          <Link href="/dashboard" className="flex items-center gap-2 px-1">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent-soft text-accent-hover">
              <Settings2 className="h-3.5 w-3.5" />
            </span>
            <span className="text-sm font-semibold tracking-tight">AtlasCore</span>
          </Link>
        ) : (
          <Link
            href="/dashboard"
            className="mx-auto flex h-7 w-7 items-center justify-center rounded-md bg-accent-soft text-accent-hover"
            aria-label="AtlasCore"
          >
            <Settings2 className="h-3.5 w-3.5" />
          </Link>
        )}
        <button
          type="button"
          onClick={onToggle}
          className={cn(
            "rounded-md p-1.5 text-muted hover:bg-surface-raised hover:text-foreground",
            collapsed && "absolute -right-3 top-4 z-40 border border-border bg-surface-raised",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {NAV.map((group) => (
          <div key={group.title} className="mb-4">
            {!collapsed ? (
              <p className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                {group.title}
              </p>
            ) : null}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                const link = (
                  <Link
                    href={item.href}
                    className={cn(
                      "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                      active
                        ? "bg-accent-soft text-accent-hover"
                        : "text-muted hover:bg-surface-raised hover:text-foreground",
                      collapsed && "justify-center px-0",
                    )}
                    aria-current={active ? "page" : undefined}
                  >
                    <item.icon className="h-4 w-4 shrink-0" />
                    {!collapsed ? <span className="truncate">{item.label}</span> : null}
                  </Link>
                );
                return (
                  <li key={item.href}>
                    {collapsed ? <Tooltip content={item.label}>{link}</Tooltip> : link}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-border p-2">
        {!collapsed && user ? (
          <div className="mb-2 rounded-md border border-border bg-surface-raised px-2.5 py-2">
            <p className="truncate text-xs font-medium text-foreground">{user.full_name}</p>
            <p className="truncate text-[11px] text-muted">{user.email}</p>
          </div>
        ) : null}
        <button
          type="button"
          onClick={onLogout}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm text-muted hover:bg-danger-soft hover:text-danger",
            collapsed && "justify-center",
          )}
        >
          <LogOut className="h-4 w-4" />
          {!collapsed ? "Sign out" : null}
        </button>
      </div>
    </aside>
  );
}

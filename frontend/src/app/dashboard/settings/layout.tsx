"use client";

/**
 * Settings layout — shared sidebar nav for all /dashboard/settings/* pages.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

const NAV_ITEMS = [
  { href: "/dashboard/settings/members", label: "Members" },
  { href: "/dashboard/settings/invitations", label: "Invitations" },
  { href: "/dashboard/settings/teams", label: "Teams" },
  { href: "/dashboard/settings/service-accounts", label: "Service Accounts" },
  { href: "/dashboard/settings/knowledge", label: "Knowledge" },
];

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const { user } = useAuth();

  const adminRoles = ["owner", "administrator"];
  const isAdmin = user && adminRoles.includes(user.org_role ?? "");

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="w-56 border-r bg-gray-50 p-4 shrink-0">
        <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-gray-400">
          Settings
        </p>
        <nav className="flex flex-col gap-1">
          {NAV_ITEMS.map(({ href, label }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`rounded px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-indigo-50 font-medium text-indigo-700"
                    : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 p-8">
        {!isAdmin && (
          <div className="mb-6 rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            You have read-only access to this section.
          </div>
        )}
        {children}
      </main>
    </div>
  );
}

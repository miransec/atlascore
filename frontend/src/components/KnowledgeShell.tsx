"use client";

/**
 * KnowledgeShell — shared layout for knowledge-related pages.
 *
 * Renders:
 *   - A top navigation bar with AtlasCore branding, org badge, and sign-out.
 *   - A secondary nav strip linking between Knowledge sections:
 *       Sources · Documents · Search · Ask a Question
 *   - A page-level back-link to the dashboard.
 *   - A content wrapper for the page body.
 *
 * Used by: /dashboard/search, /dashboard/answer, /dashboard/settings/knowledge
 *
 * SECURITY:
 *   - No API keys, storage_keys, or vectors are ever surfaced here.
 *   - All data passed to children is plain text or server-controlled UUIDs.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { auth, ApiError } from "@/lib/api";
import { useRouter } from "next/navigation";

const NAV_LINKS = [
  { label: "Sources", href: "/dashboard/settings/knowledge" },
  { label: "Search", href: "/dashboard/search" },
  { label: "Ask a Question", href: "/dashboard/answer" },
] as const;

interface KnowledgeShellProps {
  children: React.ReactNode;
  /** Optional heading displayed above the content area. */
  heading?: string;
  /** Optional description shown below the heading. */
  description?: string;
}

export function KnowledgeShell({
  children,
  heading,
  description,
}: KnowledgeShellProps) {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await auth.logout();
    } catch (err) {
      if (!(err instanceof ApiError)) {
        console.error("Logout error:", err);
      }
    } finally {
      signOut();
      router.replace("/login");
    }
  }

  if (!user) return null;

  const roleLabel = user.org_role
    ? user.org_role.replace(/_/g, " ")
    : "Member";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Top bar ── */}
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Link
            href="/dashboard"
            className="text-base font-bold tracking-tight text-gray-900 hover:text-indigo-600 transition-colors"
          >
            AtlasCore
          </Link>

          <div className="flex items-center gap-4">
            <span className="hidden rounded-full bg-blue-50 px-3 py-0.5 text-xs font-medium text-blue-700 sm:inline-block">
              {user.organisation_slug}
            </span>
            <div className="flex items-center gap-3">
              <div className="hidden text-right sm:block">
                <p className="text-sm font-medium text-gray-800">{user.full_name}</p>
                <p className="text-xs capitalize text-gray-500">{roleLabel}</p>
              </div>
              <button
                onClick={handleLogout}
                disabled={loggingOut}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:border-gray-400 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loggingOut ? "Signing out…" : "Sign out"}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* ── Knowledge section nav strip ── */}
      <div className="border-b border-gray-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <nav className="flex items-center gap-1 overflow-x-auto py-0">
            {NAV_LINKS.map((link) => {
              const isActive = pathname === link.href || pathname.startsWith(link.href + "/");
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={
                    "whitespace-nowrap border-b-2 px-4 py-3 text-sm font-medium transition-colors " +
                    (isActive
                      ? "border-indigo-600 text-indigo-600"
                      : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700")
                  }
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </div>

      {/* ── Page content ── */}
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {/* Back link */}
        <div className="mb-6">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
          >
            <span aria-hidden="true">←</span> Dashboard
          </Link>
        </div>

        {/* Optional page heading */}
        {heading && (
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-gray-900">{heading}</h1>
            {description && (
              <p className="mt-1 text-sm text-gray-500">{description}</p>
            )}
          </div>
        )}

        {children}
      </main>
    </div>
  );
}

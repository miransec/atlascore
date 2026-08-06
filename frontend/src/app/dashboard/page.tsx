"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { auth, ApiError } from "@/lib/api";

export default function DashboardPage() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await auth.logout();
    } catch (err) {
      // On network error or 401 (already expired) we still clear local state.
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
      {/* Top navigation bar */}
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <span className="text-base font-bold tracking-tight text-gray-900">
            AtlasCore
          </span>

          <div className="flex items-center gap-4">
            {/* Organisation badge */}
            <span className="hidden rounded-full bg-blue-50 px-3 py-0.5 text-xs font-medium text-blue-700 sm:inline-block">
              {user.organisation_slug}
            </span>

            {/* User info + logout */}
            <div className="flex items-center gap-3">
              <div className="hidden text-right sm:block">
                <p className="text-sm font-medium text-gray-800">
                  {user.full_name}
                </p>
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

      {/* Main content area */}
      <main className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        {/* Welcome card */}
        <div className="mb-8 rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
          <h1 className="text-2xl font-semibold text-gray-900">
            Welcome back, {user.full_name.split(" ")[0]}
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Signed in to{" "}
            <span className="font-medium text-gray-700">
              {user.organisation_slug}
            </span>{" "}
            as{" "}
            <span className="capitalize text-gray-700">{roleLabel}</span>.
          </p>
        </div>

        {/* Platform admin indicator */}
        {user.is_platform_admin && (
          <div className="mb-8 rounded-xl border border-amber-200 bg-amber-50 px-6 py-4">
            <p className="text-sm font-medium text-amber-800">
              Platform administrator
            </p>
            <p className="mt-0.5 text-xs text-amber-700">
              You have elevated privileges across all organisations.
            </p>
          </div>
        )}

        {/* Phase 1B admin cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[
            {
              title: "Members",
              description: "Manage organisation members and roles.",
              href: "/dashboard/settings/members",
            },
            {
              title: "Invitations",
              description: "Send and revoke organisation invitations.",
              href: "/dashboard/settings/invitations",
            },
            {
              title: "Teams",
              description: "Group members into teams for shared access.",
              href: "/dashboard/settings/teams",
            },
            {
              title: "Service Accounts",
              description: "Non-human identities for API automation.",
              href: "/dashboard/settings/service-accounts",
            },
            {
              title: "Knowledge",
              description: "Manage sources and ingested documents.",
              href: "/dashboard/settings/knowledge",
            },
            {
              title: "Knowledge Search",
              description: "Search across ingested workspace knowledge.",
              href: "/dashboard/search",
            },
            {
              title: "Ask a Question",
              description:
                "Grounded Q&A — answers cited from workspace knowledge only.",
              href: "/dashboard/answer",
            },
            {
              title: "Workspaces",
              description: "Manage AI workspaces and their configurations.",
              href: null,
            },
            {
              title: "Audit log",
              description: "Review organisation activity and security events.",
              href: null,
            },
          ].map((card) => (
            <div
              key={card.title}
              className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
            >
              <h3 className="text-sm font-semibold text-gray-900">{card.title}</h3>
              <p className="mt-1 text-xs text-gray-500">{card.description}</p>
              {card.href ? (
                <Link
                  href={card.href}
                  className="mt-4 inline-block text-xs font-medium text-indigo-600 hover:underline"
                >
                  Open →
                </Link>
              ) : (
                <p className="mt-4 text-xs font-medium text-gray-400">Coming soon →</p>
              )}
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

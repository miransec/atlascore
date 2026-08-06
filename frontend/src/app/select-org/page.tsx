"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { auth, Organisation, ApiError, setAccessToken } from "@/lib/api";

export default function SelectOrgPage() {
  const router = useRouter();

  const [orgs, setOrgs] = useState<Organisation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<string | null>(null);

  useEffect(() => {
    // Recover org list written by login step 1.
    const raw = sessionStorage.getItem("pending_orgs");
    if (!raw) {
      // No pending orgs — send back to login.
      router.replace("/login");
      return;
    }
    try {
      const parsed: Organisation[] = JSON.parse(raw);
      if (!Array.isArray(parsed) || parsed.length === 0) {
        router.replace("/login");
        return;
      }
      setOrgs(parsed);
    } catch {
      router.replace("/login");
    }
  }, [router]);

  async function handleSelect(organisationId: string) {
    if (selecting) return;
    setSelecting(organisationId);
    setError(null);

    try {
      const token = await auth.selectOrg({ organisation_id: organisationId });
      // Store the JWT access token in memory (never in storage).
      setAccessToken(token.access_token);
      // Clean up the temporary org list.
      sessionStorage.removeItem("pending_orgs");
      router.replace("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          // Pre-auth session expired or already consumed — restart login.
          sessionStorage.removeItem("pending_orgs");
          router.replace("/login");
          return;
        }
        setError(err.detail);
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
      setSelecting(null);
    }
  }

  if (orgs.length === 0) {
    return null; // waiting for redirect or hydration
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-gray-900">
            AtlasCore
          </h1>
          <p className="mt-2 text-sm text-gray-500">
            Select your organisation to continue
          </p>
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
          <h2 className="mb-5 text-xl font-semibold text-gray-800">
            Choose organisation
          </h2>

          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <ul className="space-y-2">
            {orgs.map((org) => {
              const isSelecting = selecting === org.id;
              return (
                <li key={org.id}>
                  <button
                    onClick={() => handleSelect(org.id)}
                    disabled={!!selecting}
                    className="group flex w-full items-center justify-between rounded-lg border border-gray-200 px-4 py-3.5 text-left transition-colors hover:border-blue-400 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-900 group-hover:text-blue-700">
                        {org.display_name}
                      </p>
                      {org.org_role && (
                        <p className="mt-0.5 text-xs capitalize text-gray-500">
                          {org.org_role.replace(/_/g, " ")}
                        </p>
                      )}
                    </div>
                    {isSelecting ? (
                      <span className="text-xs text-blue-600">Loading…</span>
                    ) : (
                      <svg
                        className="h-4 w-4 text-gray-400 group-hover:text-blue-500"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                        aria-hidden="true"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M9 5l7 7-7 7"
                        />
                      </svg>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <p className="mt-4 text-center text-sm text-gray-500">
          <button
            onClick={() => {
              sessionStorage.removeItem("pending_orgs");
              router.push("/login");
            }}
            className="font-medium text-blue-600 hover:text-blue-700"
          >
            ← Back to sign in
          </button>
        </p>
      </div>
    </div>
  );
}

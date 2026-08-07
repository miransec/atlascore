"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { auth, Organisation, ApiError, setAccessToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function SelectOrgPage() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [orgs, setOrgs] = useState<Organisation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState<string | null>(null);

  useEffect(() => {
    const raw = sessionStorage.getItem("pending_orgs");
    if (!raw) {
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
      setAccessToken(token.access_token);
      const me = await auth.me();
      setUser(me);
      sessionStorage.removeItem("pending_orgs");
      router.replace("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
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

  if (orgs.length === 0) return null;

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md animate-slide-up">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">AtlasCore</h1>
          <p className="mt-2 text-sm text-muted">Select your organisation to continue</p>
        </div>
        <div className="surface-card p-8">
          <h2 className="mb-5 text-lg font-semibold">Choose organisation</h2>
          {error ? (
            <div className="mb-4 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </div>
          ) : null}
          <ul className="space-y-2">
            {orgs.map((org) => (
              <li key={org.id}>
                <button
                  type="button"
                  onClick={() => handleSelect(org.id)}
                  disabled={!!selecting}
                  className="group flex w-full items-center justify-between rounded-md border border-border bg-surface px-4 py-3.5 text-left transition hover:border-accent/40 hover:bg-accent-soft disabled:opacity-60"
                >
                  <div>
                    <p className="text-sm font-medium">{org.display_name}</p>
                    {org.org_role ? (
                      <p className="mt-0.5 text-xs capitalize text-muted">
                        {org.org_role.replace(/_/g, " ")}
                      </p>
                    ) : null}
                  </div>
                  {selecting === org.id ? (
                    <span className="text-xs text-accent-hover">Loading…</span>
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted group-hover:text-accent-hover" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <p className="mt-4 text-center text-sm text-muted">
          <button
            type="button"
            onClick={() => {
              sessionStorage.removeItem("pending_orgs");
              router.push("/login");
            }}
            className="text-accent-hover hover:underline"
          >
            Back to sign in
          </button>
        </p>
      </div>
    </div>
  );
}

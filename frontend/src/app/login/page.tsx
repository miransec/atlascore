"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { auth, ApiError } from "@/lib/api";
import { Button, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await auth.login({ email, password });
      sessionStorage.setItem("pending_orgs", JSON.stringify(result.organisations));
      router.push("/select-org");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "An unexpected error occurred. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md animate-slide-up">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">AtlasCore</h1>
          <p className="mt-2 text-sm text-muted">
            Secure enterprise AI for knowledge and grounded workflows
          </p>
        </div>
        <div className="surface-card p-8">
          <h2 className="mb-6 text-lg font-semibold">Sign in</h2>
          {error ? (
            <div className="mb-4 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </div>
          ) : null}
          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div>
              <label htmlFor="email" className="mb-1 block text-xs text-muted">
                Email
              </label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-1 block text-xs text-muted">
                Password
              </label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <Button type="submit" className="w-full" loading={loading}>
              Continue
            </Button>
          </form>
          <p className="mt-6 text-center text-sm text-muted">
            No account?{" "}
            <Link href="/register" className="text-accent-hover hover:underline">
              Register
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { auth, ApiError } from "@/lib/api";
import { Button, Input } from "@/components/ui";

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    full_name: "",
    email: "",
    password: "",
    organisation_name: "",
    organisation_slug: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const { name, value } = e.target;
    setForm((prev) => {
      const next = { ...prev, [name]: value };
      if (
        name === "organisation_name" &&
        prev.organisation_slug === slugify(prev.organisation_name)
      ) {
        next.organisation_slug = slugify(value);
      }
      return next;
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await auth.register(form);
      router.push("/login?registered=1");
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
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-md animate-slide-up">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">AtlasCore</h1>
          <p className="mt-2 text-sm text-muted">Create your account and organisation</p>
        </div>
        <div className="surface-card p-8">
          <h2 className="mb-6 text-lg font-semibold">Register</h2>
          {error ? (
            <div className="mb-4 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </div>
          ) : null}
          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            {(
              [
                ["full_name", "Full name", "text", "Jane Smith"],
                ["email", "Email", "email", "jane@company.com"],
                ["password", "Password", "password", "12+ characters"],
                ["organisation_name", "Organisation name", "text", "Acme Corp"],
                ["organisation_slug", "Organisation slug", "text", "acme-corp"],
              ] as const
            ).map(([name, label, type, placeholder]) => (
              <div key={name}>
                <label htmlFor={name} className="mb-1 block text-xs text-muted">
                  {label}
                </label>
                <Input
                  id={name}
                  name={name}
                  type={type}
                  required
                  minLength={name === "password" ? 12 : undefined}
                  value={form[name]}
                  onChange={handleChange}
                  placeholder={placeholder}
                  className={name === "organisation_slug" ? "font-mono" : undefined}
                />
              </div>
            ))}
            <Button type="submit" className="w-full" loading={loading}>
              Create account
            </Button>
          </form>
        </div>
        <p className="mt-4 text-center text-sm text-muted">
          Already have an account?{" "}
          <Link href="/login" className="text-accent-hover hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

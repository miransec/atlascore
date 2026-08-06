"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { auth, ApiError } from "@/lib/api";

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
      // Auto-derive slug from org name when the user hasn't touched slug yet.
      if (name === "organisation_name" && prev.organisation_slug === slugify(prev.organisation_name)) {
        next.organisation_slug = slugify(value);
      }
      return next;
    });
  }

  function slugify(s: string): string {
    return s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await auth.register(form);
      // Registration succeeded — go to login.
      router.push("/login?registered=1");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-gray-900">
            AtlasCore
          </h1>
          <p className="mt-2 text-sm text-gray-500">
            Create your account and organisation
          </p>
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-8 shadow-sm">
          <h2 className="mb-6 text-xl font-semibold text-gray-800">
            Register
          </h2>

          {error && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate>
            <div className="mb-4">
              <label
                htmlFor="full_name"
                className="mb-1.5 block text-sm font-medium text-gray-700"
              >
                Full name
              </label>
              <input
                id="full_name"
                name="full_name"
                type="text"
                autoComplete="name"
                required
                value={form.full_name}
                onChange={handleChange}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="Jane Smith"
              />
            </div>

            <div className="mb-4">
              <label
                htmlFor="email"
                className="mb-1.5 block text-sm font-medium text-gray-700"
              >
                Email address
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                value={form.email}
                onChange={handleChange}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="jane@company.com"
              />
            </div>

            <div className="mb-4">
              <label
                htmlFor="password"
                className="mb-1.5 block text-sm font-medium text-gray-700"
              >
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                value={form.password}
                onChange={handleChange}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="12+ characters"
              />
              <p className="mt-1 text-xs text-gray-400">
                Minimum 12 characters.
              </p>
            </div>

            <hr className="my-5 border-gray-200" />

            <div className="mb-4">
              <label
                htmlFor="organisation_name"
                className="mb-1.5 block text-sm font-medium text-gray-700"
              >
                Organisation name
              </label>
              <input
                id="organisation_name"
                name="organisation_name"
                type="text"
                required
                value={form.organisation_name}
                onChange={handleChange}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="Acme Corp"
              />
            </div>

            <div className="mb-6">
              <label
                htmlFor="organisation_slug"
                className="mb-1.5 block text-sm font-medium text-gray-700"
              >
                Organisation slug
              </label>
              <input
                id="organisation_slug"
                name="organisation_slug"
                type="text"
                required
                pattern="[a-z0-9][a-z0-9\-]{1,46}[a-z0-9]"
                value={form.organisation_slug}
                onChange={handleChange}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="acme-corp"
              />
              <p className="mt-1 text-xs text-gray-400">
                Lowercase letters, numbers, and hyphens only.
              </p>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Creating account…" : "Create account"}
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-sm text-gray-500">
          Already have an account?{" "}
          <Link
            href="/login"
            className="font-medium text-blue-600 hover:text-blue-700"
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

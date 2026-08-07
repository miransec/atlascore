"use client";

import { useEffect, useState } from "react";
import { PageHeader, Button, Input, LoadingSkeleton, ErrorState } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { ApiError, organisations, type OrganisationDetail } from "@/lib/api";

export default function OrganisationSettingsPage() {
  const toast = useToast();
  const [org, setOrg] = useState<OrganisationDetail | null>(null);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    organisations
      .getCurrent()
      .then((o) => {
        setOrg(o);
        setName(o.display_name);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.detail : "Failed to load organisation"),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Organisation"
        description="Organisation profile for the current tenancy context."
      />
      {loading ? (
        <LoadingSkeleton lines={4} />
      ) : error ? (
        <ErrorState description={error} />
      ) : (
        <div className="surface-card max-w-xl space-y-4 p-5">
          <div>
            <label className="mb-1 block text-xs text-muted">Slug</label>
            <Input value={org?.slug ?? ""} disabled />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">Display name</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button
            loading={saving}
            onClick={async () => {
              setSaving(true);
              try {
                const updated = await organisations.updateCurrent({
                  display_name: name.trim(),
                });
                setOrg(updated);
                toast.success("Organisation updated");
              } catch (err) {
                toast.error(
                  "Update failed",
                  err instanceof ApiError ? err.detail : "Error",
                );
              } finally {
                setSaving(false);
              }
            }}
          >
            Save
          </Button>
        </div>
      )}
    </div>
  );
}

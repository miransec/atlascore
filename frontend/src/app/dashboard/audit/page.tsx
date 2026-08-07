"use client";

import { ScrollText } from "lucide-react";
import { PageHeader, EmptyState } from "@/components/ui";

export default function AuditLogsPage() {
  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Audit Logs"
        description="Security-relevant events are recorded server-side. A query/export API is not available in this release."
      />
      <EmptyState
        icon={ScrollText}
        title="Audit log API unavailable"
        description="AtlasCore emits append-oriented audit events with restricted privileges, but listing them over HTTP is planned for a later phase. No synthetic audit data is shown."
      />
    </div>
  );
}

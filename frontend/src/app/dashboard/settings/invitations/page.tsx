import { redirect } from "next/navigation";

export default function LegacyInvitationsPage() {
  redirect("/dashboard/members");
}

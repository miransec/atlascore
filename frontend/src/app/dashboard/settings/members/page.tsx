import { redirect } from "next/navigation";

export default function LegacyMembersSettingsPage() {
  redirect("/dashboard/members");
}

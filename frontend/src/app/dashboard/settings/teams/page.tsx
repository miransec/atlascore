import { redirect } from "next/navigation";

export default function LegacyTeamsSettingsPage() {
  redirect("/dashboard/teams");
}

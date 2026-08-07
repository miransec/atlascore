import { redirect } from "next/navigation";

export default function LegacyServiceAccountsSettingsPage() {
  redirect("/dashboard/service-accounts");
}

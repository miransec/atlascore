import { redirect } from "next/navigation";

export default function LegacyKnowledgePage() {
  redirect("/dashboard/sources");
}

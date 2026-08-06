import { redirect } from "next/navigation";

export default function Home() {
  // Root always redirects to login
  redirect("/login");
}

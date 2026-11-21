import { redirect } from "next/navigation";

/** The app entry redirects to the authenticated dashboard (or /login). */
export default function Home() {
  redirect("/dashboard");
}

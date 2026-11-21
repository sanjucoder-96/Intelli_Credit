import type { Metadata } from "next";

import { Providers } from "./providers";

import "./globals.css";

/**
 * Root layout (server component).
 *
 * `suppressHydrationWarning` on <html> is required by next-themes: the theme
 * class is applied on the client before hydration, so the server-rendered
 * attribute intentionally differs. All client-side context (TanStack Query,
 * next-themes) is mounted via <Providers/>. The authenticated app shell
 * (sidebar, nav, breadcrumbs) arrives with the case workspace in Phase 5.
 */
export const metadata: Metadata = {
  title: "IntelliCredit",
  description:
    "Agentic credit-appraisal platform - explainable, evidence-linked workflows.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

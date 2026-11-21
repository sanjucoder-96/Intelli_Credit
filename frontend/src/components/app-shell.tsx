"use client";

import { LayoutDashboard, FolderKanban, LogOut, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/cases", label: "Cases", icon: FolderKanban },
];

function NavItem({ href, label, icon: Icon }: (typeof NAV)[number]) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-secondary text-secondary-foreground"
          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
      )}
    >
      <Icon className="size-4" />
      {label}
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);

  return (
    <div className="flex min-h-screen bg-muted/20">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-background md:flex">
        <div className="flex items-center gap-2 px-5 py-4">
          <ShieldCheck className="size-5 text-primary" />
          <div className="leading-tight">
            <p className="text-sm font-semibold tracking-tight">IntelliCredit</p>
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
              Credit Intelligence
            </p>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3 py-2">
          {NAV.map((item) => (
            <NavItem key={item.href} {...item} />
          ))}
        </nav>
        <div className="border-t p-3">
          <div className="mb-2 px-2">
            <p className="truncate text-sm font-medium">{user?.full_name}</p>
            <p className="truncate font-mono text-[11px] capitalize text-muted-foreground">
              {user?.role?.replace("_", " ")}
            </p>
          </div>
          <Button variant="ghost" size="sm" className="w-full justify-start gap-2" onClick={logout}>
            <LogOut className="size-4" />
            Sign out
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b bg-background/95 px-4 backdrop-blur md:px-6">
          <div className="flex items-center gap-2 md:hidden">
            <ShieldCheck className="size-5 text-primary" />
            <span className="text-sm font-semibold">IntelliCredit</span>
          </div>
          <div className="flex flex-1 items-center justify-end gap-1">
            <ThemeToggle />
          </div>
        </header>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}

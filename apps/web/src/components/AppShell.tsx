"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { clsx } from "@/lib/format";
import { clearSession, getSession, type Session } from "@/lib/session";

interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
}

const NAV: NavItem[] = [
  { href: "/", label: "首页", icon: <HomeIcon /> },
  { href: "/home", label: "我的家", icon: <HouseIcon /> },
  { href: "/items", label: "物品", icon: <BoxIcon /> },
  { href: "/assistant", label: "AI 助手", icon: <SparkleIcon /> },
];

const SECONDARY: NavItem[] = [
  { href: "/home/rooms", label: "房间", icon: <DoorIcon /> },
  { href: "/home/storage", label: "收纳空间", icon: <ShelfIcon /> },
  { href: "/home/setup", label: "搭建我的家", icon: <CompassIcon /> },
  { href: "/items/place", label: "批量归位", icon: <ShelfIcon /> },
  { href: "/items/new", label: "添加物品", icon: <PlusIcon /> },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

// Login / signup are their own screens — showing the app's navigation beside
// a sign-in form invites clicking into pages that will only redirect back.
const BARE_PATHS = ["/login", "/signup"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  // Read after mount: `getSession()` reads a cookie, which does not exist
  // during the server pass. Rendering a name here that we then have to correct
  // would be a hydration mismatch.
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    setSession(getSession());
  }, [pathname]);

  if (BARE_PATHS.includes(pathname)) {
    return <div className="min-h-screen bg-ink-50">{children}</div>;
  }

  function signOut() {
    clearSession();
    window.location.href = "/login";
  }

  return (
    <div className="min-h-screen bg-ink-50">
      {/* Top bar (mobile + desktop) */}
      <header className="sticky top-0 z-30 border-b border-ink-100 bg-white/80 backdrop-blur">
        <div className="container-page flex h-14 items-center justify-between">
          <Link href="/" className="flex items-center gap-2 font-semibold text-ink-900">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500 text-white">
              <SparkleIcon className="h-4 w-4" />
            </span>
            <span className="hidden sm:inline">AI 家庭收纳管家</span>
            <span className="sm:hidden">收纳</span>
          </Link>
          <nav className="hidden md:flex items-center gap-1">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive(pathname, item.href)
                    ? "bg-brand-50 text-brand-700"
                    : "text-ink-600 hover:bg-ink-100",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <Link href="/items/new" className="btn-primary hidden sm:inline-flex">
              <PlusIcon className="h-4 w-4" />
              添加物品
            </Link>
            {session && (
              <button
                type="button"
                onClick={() => {
                  if (window.confirm("退出登录？")) signOut();
                }}
                title="退出登录"
                className="hidden items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink-600 hover:bg-ink-100 sm:flex"
              >
                <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-100 text-xs font-medium text-brand-700">
                  {session.displayName.slice(0, 1)}
                </span>
                {session.displayName}
              </button>
            )}
          </div>
        </div>
      </header>

      <div className="container-page flex gap-6 py-6">
        {/* Side nav (desktop) */}
        <aside className="hidden md:block w-48 shrink-0">
          <div className="card p-3">
            <p className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-ink-400">
              主要
            </p>
            <nav className="flex flex-col">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={clsx(
                    "flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                    isActive(pathname, item.href)
                      ? "bg-brand-50 text-brand-700"
                      : "text-ink-700 hover:bg-ink-100",
                  )}
                >
                  {item.icon}
                  {item.label}
                </Link>
              ))}
            </nav>
            <p className="mt-3 px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-ink-400">
              空间
            </p>
            <nav className="flex flex-col">
              {SECONDARY.filter((i) => i.href !== "/items/new").map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={clsx(
                    "flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors",
                    isActive(pathname, item.href)
                      ? "bg-brand-50 text-brand-700"
                      : "text-ink-700 hover:bg-ink-100",
                  )}
                >
                  {item.icon}
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </aside>

        <main className="min-w-0 flex-1">{children}</main>
      </div>

      {/* Bottom nav (mobile) */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 border-t border-ink-100 bg-white">
        <div className="grid grid-cols-5">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex flex-col items-center gap-0.5 py-2 text-xs",
                isActive(pathname, item.href) ? "text-brand-600" : "text-ink-500",
              )}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
          <Link
            href="/items/new"
            className="flex flex-col items-center gap-0.5 py-2 text-xs text-brand-600"
          >
            <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-500 text-white">
              <PlusIcon className="h-4 w-4" />
            </span>
            添加
          </Link>
        </div>
      </nav>
      <div className="md:hidden h-16" />
    </div>
  );
}

// ------------------------------------------------------------- icons

function HomeIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5 10.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9.5" />
    </svg>
  );
}
function HouseIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 11 12 3l9 8" />
      <path d="M5 10v10h14V10" />
      <path d="M10 20v-6h4v6" />
    </svg>
  );
}
function BoxIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" />
      <path d="M3 7.5 12 12l9-4.5" />
      <path d="M12 12v9" />
    </svg>
  );
}
function SparkleIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2 13.5 8.5 20 10l-6.5 1.5L12 18l-1.5-6.5L4 10l6.5-1.5L12 2Z" />
      <path d="M19 14l.7 3.3L23 18l-3.3.7L19 22l-.7-3.3L15 18l3.3-.7L19 14Z" />
    </svg>
  );
}
function DoorIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <circle cx="15" cy="12" r="0.8" fill="currentColor" />
    </svg>
  );
}
function ShelfIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M3 10h18" />
      <path d="M3 16h18" />
    </svg>
  );
}
function CompassIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5-5 2 2-5 5-2Z" />
    </svg>
  );
}
function PlusIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

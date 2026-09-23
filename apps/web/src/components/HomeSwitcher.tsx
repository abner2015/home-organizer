"use client";

// 「切回家」dropdown — P0.A.
//
// Multi-home 已经有了 (P0.9: POST /homes), 但 homeId cookie 写死后 UI 没切入口。
// AppShell 顶部右侧加一个 dropdown, 点击展开用户全部家; 切换 →
// `setSession({...current, homeId: newId})` 重写 cookie + `router.refresh()`
// 让 server component 用新 X-Home-Id 重渲染.
//
// 设计要点见 plan:
//   - 单家: 显示家名 + 「+ 新家」入口
//   - 多家: 当前 active 高亮 + 其他家可点击
//   - 点击外部 / ESC 关闭
//   - 移动端也可见 (与 user button 不同)

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { APIError, api } from "@/lib/api";
import { clsx } from "@/lib/format";
import { getSession, setSession, type Session } from "@/lib/session";
import type { Home } from "@/lib/types";

export function HomeSwitcher({ session }: { session: Session }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [homes, setHomes] = useState<Home[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Load user's homes once on mount. Re-fetch is unnecessary — a fresh home
  // shows up after `CreateHomeForm` returns to /home/setup; the next visit to
  // a page that mounts this component will see the updated list. (Switching
  // away from a tab and back is enough.)
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .listHomes({ token: session.token })
      .then((list) => {
        if (!cancelled) setHomes(list);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof APIError ? e.message : "加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [session.token]);

  // Close on click outside.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Close on ESC.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  function reload() {
    setError(null);
    setHomes(null);
    api
      .listHomes({ token: session.token })
      .then(setHomes)
      .catch((e) => {
        setError(e instanceof APIError ? e.message : "加载失败");
      });
  }

  function switchTo(homeId: string) {
    setOpen(false);
    if (homeId === session.homeId) return;
    // Re-read getSession() (not `session` from props) because the AppShell's
    // local state may have drifted if the cookie was updated since mount; the
    // cookie is the source of truth.
    const current = getSession();
    if (current) {
      setSession({ ...current, homeId });
    } else {
      // Should not happen — AppShell only renders this with a session — but
      // if it does, write just the home cookie so the next request still has
      // a valid X-Home-Id.
      document.cookie = `ho_home=${encodeURIComponent(homeId)}; Path=/; SameSite=Lax`;
    }
    // Force server components to re-fetch with the new X-Home-Id. The cookie
    // change alone does not invalidate the current render in Next.js.
    router.refresh();
  }

  const active = homes?.find((h) => h.id === session.homeId);
  const label = active?.name ?? "加载中…";

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="切换当前家"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-ink-700 hover:bg-ink-100"
      >
        <HouseIcon className="h-4 w-4 text-ink-500" />
        <span className="max-w-[8rem] truncate">{label}</span>
        <ChevronDown className="h-3.5 w-3.5 text-ink-400" />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="选择一个家"
          className="absolute right-0 z-40 mt-1 w-64 overflow-hidden rounded-lg border border-ink-100 bg-white shadow-lg"
        >
          {renderList({ homes, error, session, switchTo, reload, close: () => setOpen(false) })}
        </div>
      ) : null}
    </div>
  );
}

// Lift the type narrowing out of the JSX ternary chain: TS cannot track
// `homes !== null` across nested conditionals.
function renderList({
  homes,
  error,
  session,
  switchTo,
  reload,
  close,
}: {
  homes: Home[] | null;
  error: string | null;
  session: Session;
  switchTo: (homeId: string) => void;
  reload: () => void;
  close: () => void;
}) {
  if (homes === null && !error) {
    return <p className="p-3 text-sm text-ink-500">加载中…</p>;
  }
  if (error) {
    return (
      <div className="p-3 text-sm">
        <p className="text-red-600">{error}</p>
        <button
          type="button"
          onClick={reload}
          className="mt-2 text-xs text-brand-700 hover:underline"
        >
          重试
        </button>
      </div>
    );
  }
  // homes is non-null here: the two early returns above rule out both
  // `homes === null && !error` and `error` (which only fires after a failed
  // fetch, when homes has been reset to null by reload()).
  const loaded = homes as Home[];
  if (loaded.length === 0) {
    return <p className="p-3 text-sm text-ink-500">还没有家</p>;
  }
  return (
    <>
      <ul className="max-h-72 overflow-auto py-1">
        {loaded.map((h) => {
          const isActive = h.id === session.homeId;
          return (
            <li key={h.id}>
              <button
                type="button"
                role="option"
                aria-selected={isActive}
                aria-current={isActive ? "true" : undefined}
                onClick={() => switchTo(h.id)}
                disabled={isActive}
                className={clsx(
                  "flex w-full items-center justify-between px-3 py-2 text-left text-sm",
                  isActive
                    ? "bg-brand-50 font-medium text-brand-700"
                    : "text-ink-700 hover:bg-ink-50",
                )}
              >
                <span className="truncate">{h.name}</span>
                {isActive ? (
                  <span className="text-xs text-brand-600">当前</span>
                ) : (
                  <span className="text-xs text-ink-400">
                    {(h.member_count ?? 1) > 1 ? `${h.member_count} 人` : "个人"}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <Link
        href="/home/new"
        onClick={close}
        className="block border-t border-ink-100 bg-ink-50 px-3 py-2 text-sm text-brand-700 hover:bg-ink-100"
      >
        + 新家
      </Link>
    </>
  );
}

// ---------------------------------------------------------------- icons

function HouseIcon({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 11 12 3l9 8" />
      <path d="M5 10v10h14V10" />
      <path d="M10 20v-6h4v6" />
    </svg>
  );
}

function ChevronDown({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

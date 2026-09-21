"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { setSession } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const tokens = await api.login({ email, password });
      // Which home to act in is discovered, not hardcoded: one account may
      // belong to several, and a fresh signup has exactly one.
      const [me, homes] = await Promise.all([
        api.me(tokens.access_token),
        api.listHomes({ token: tokens.access_token }),
      ]);
      const home = homes[0];
      if (!home) {
        setError("这个账号下还没有家庭空间，请联系管理员或重新注册。");
        return;
      }
      setSession(
        {
          token: tokens.access_token,
          homeId: home.id,
          displayName: me.display_name || "我",
        },
        tokens.expires_in,
      );
      router.replace("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-10">
      <h1 className="text-2xl font-semibold text-ink-900">登录</h1>
      <p className="mt-1 text-sm text-ink-500">登录后即可打开你的家庭收纳空间。</p>

      <form onSubmit={onSubmit} className="card mt-6 space-y-4 p-5">
        <label className="block">
          <span className="text-sm font-medium text-ink-700">邮箱</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-brand-400"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink-700">密码</span>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-brand-400"
          />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary w-full">
          {busy ? "登录中…" : "登录"}
        </button>
      </form>

      <p className="mt-4 text-sm text-ink-500">
        还没有账号？
        <Link href="/signup" className="ml-1 text-brand-600 hover:underline">
          注册一个
        </Link>
      </p>
    </div>
  );
}

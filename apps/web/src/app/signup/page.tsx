"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.signup({ email, password, display_name: displayName });
      // Signup does not hand back tokens (see `app/api/v1/auth.py`), so the
      // next step is a normal login rather than an implicit session.
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-10">
        <h1 className="text-2xl font-semibold text-ink-900">注册成功</h1>
        <p className="mt-2 text-sm text-ink-600">
          系统已经为你建好了一个家庭空间。现在登录就能开始使用。
        </p>
        <Link href="/login" className="btn-primary mt-6 w-full justify-center">
          去登录
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center px-4 py-10">
      <h1 className="text-2xl font-semibold text-ink-900">注册</h1>
      <p className="mt-1 text-sm text-ink-500">
        注册后会自动创建一个属于你的家庭空间。
      </p>

      <form onSubmit={onSubmit} className="card mt-6 space-y-4 p-5">
        <label className="block">
          <span className="text-sm font-medium text-ink-700">昵称</span>
          <input
            type="text"
            required
            maxLength={100}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-brand-400"
          />
        </label>
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
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none focus:border-brand-400"
          />
          <span className="mt-1 block text-xs text-ink-400">至少 8 位</span>
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={busy} className="btn-primary w-full">
          {busy ? "注册中…" : "注册"}
        </button>
      </form>

      <p className="mt-4 text-sm text-ink-500">
        已经有账号？
        <Link href="/login" className="ml-1 text-brand-600 hover:underline">
          去登录
        </Link>
      </p>
    </div>
  );
}

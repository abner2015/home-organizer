// Browser-side session store.
//
// The session lives in *cookies*, not localStorage. Half this app is
// server-rendered — `/`, `/items`, `/home`, … are async Server Components that
// call the API during render — and `localStorage` does not exist there. The old
// stub papered over that by falling back to two hardcoded UUIDs on the server,
// so the very same page could render with one identity and then hydrate into a
// different one. A cookie is visible to both halves, so there is now exactly
// one identity.
//
// The server counterpart lives in `session.server.ts` (it needs
// `next/headers`, which cannot enter a client bundle — hence the split).

import { HOME_COOKIE, NAME_COOKIE, TOKEN_COOKIE } from "./cookies";
import type { UUID } from "./types";

export interface Session {
  token: string;
  homeId: UUID;
  displayName: string;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookie(name: string, value: string, maxAgeSeconds: number): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${name}=${encodeURIComponent(value)}; Path=/; ` +
    `Max-Age=${maxAgeSeconds}; SameSite=Lax${secure}`;
}

function deleteCookie(name: string): void {
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/** The stored session, or `null` when signed out. Browser only. */
export function getSession(): Session | null {
  const token = readCookie(TOKEN_COOKIE);
  const homeId = readCookie(HOME_COOKIE);
  if (!token || !homeId) return null;
  return { token, homeId, displayName: readCookie(NAME_COOKIE) ?? "我" };
}

/**
 * Persist a session. `maxAgeSeconds` should be the token's own `expires_in`
 * so the cookie cannot outlive the credential inside it — an expired cookie is
 * worse than no cookie, because the app would keep rendering a shell that only
 * ever 401s. Rotating the access token with `/auth/refresh` is not wired yet;
 * when it is, this is the function that needs to extend the deadline.
 */
export function setSession(session: Session, maxAgeSeconds: number): void {
  writeCookie(TOKEN_COOKIE, session.token, maxAgeSeconds);
  writeCookie(HOME_COOKIE, session.homeId, maxAgeSeconds);
  writeCookie(NAME_COOKIE, session.displayName, maxAgeSeconds);
}

export function clearSession(): void {
  deleteCookie(TOKEN_COOKIE);
  deleteCookie(HOME_COOKIE);
  deleteCookie(NAME_COOKIE);
}

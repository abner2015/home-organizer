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

import { HOME_COOKIE, NAME_COOKIE, REFRESH_COOKIE, TOKEN_COOKIE } from "./cookies";
import { secondsUntilExpiry } from "./jwt";
import type { TokenResponse, UUID } from "./types";

/**
 * How long the cookies live when the refresh token's own lifetime cannot be
 * read. Only reachable for a session stored without one — it exists so that an
 * unreadable token cannot produce a decade-long cookie.
 */
const FALLBACK_MAX_AGE_SECONDS = 3600;

export interface Session {
  token: string;
  /** Mints a fresh `token` once it expires; `null` for a session that cannot renew. */
  refreshToken: string | null;
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

/**
 * The stored session, or `null` when signed out. Browser only.
 *
 * A non-null session does not imply a *valid* access token: it may be one
 * silent refresh away from working again. Use `isExpired(session.token)` from
 * `./jwt` when that distinction matters.
 */
export function getSession(): Session | null {
  const token = readCookie(TOKEN_COOKIE);
  const homeId = readCookie(HOME_COOKIE);
  if (!token || !homeId) return null;
  return {
    token,
    refreshToken: readCookie(REFRESH_COOKIE),
    homeId,
    displayName: readCookie(NAME_COOKIE) ?? "我",
  };
}

function persist(session: Session): void {
  // Every cookie gets the *refresh* token's lifetime, not the access token's.
  // That is the point of having one: the access token is renewable, so expiring
  // the cookies along with it would discard a session that could have carried
  // on silently — which is exactly the bug this replaced. Once the refresh
  // token is gone, nothing can renew anything and the cookies legitimately die
  // with it.
  const maxAge = session.refreshToken
    ? (secondsUntilExpiry(session.refreshToken) ?? FALLBACK_MAX_AGE_SECONDS)
    : FALLBACK_MAX_AGE_SECONDS;
  writeCookie(TOKEN_COOKIE, session.token, maxAge);
  writeCookie(HOME_COOKIE, session.homeId, maxAge);
  writeCookie(NAME_COOKIE, session.displayName, maxAge);
  if (session.refreshToken) {
    writeCookie(REFRESH_COOKIE, session.refreshToken, maxAge);
  }
}

/** Persist a freshly signed-in session. */
export function setSession(session: Session): void {
  persist(session);
}

/**
 * Swap in a rotated token pair, keeping the home and the display name.
 *
 * `homeId` is deliberately untouched: which home you are acting in is a user
 * choice, not part of the credential.
 */
export function updateSessionTokens(tokens: TokenResponse): void {
  const current = getSession();
  if (!current) return;
  persist({
    ...current,
    token: tokens.access_token,
    refreshToken: tokens.refresh_token,
  });
}

export function clearSession(): void {
  deleteCookie(TOKEN_COOKIE);
  deleteCookie(REFRESH_COOKIE);
  deleteCookie(HOME_COOKIE);
  deleteCookie(NAME_COOKIE);
}

// Server-side session reader, for the async Server Components that render the
// app. Separate module from `session.ts` because it imports `next/headers`,
// which cannot be bundled for the browser.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { HOME_COOKIE, NAME_COOKIE, TOKEN_COOKIE } from "./cookies";
import type { Session } from "./session";

/** The session from the request's cookies, or `null` when signed out. */
export async function getServerSession(): Promise<Session | null> {
  const store = await cookies();
  const token = store.get(TOKEN_COOKIE)?.value;
  const homeId = store.get(HOME_COOKIE)?.value;
  if (!token || !homeId) return null;
  return { token, homeId, displayName: store.get(NAME_COOKIE)?.value ?? "我" };
}

/**
 * The session, or a redirect to `/login`.
 *
 * `middleware.ts` already bounces unauthenticated navigations, so this is
 * belt-and-braces for the race where the cookie expires between the middleware
 * check and the render. Callers get a non-nullable `Session`.
 */
export async function requireSession(): Promise<Session> {
  const session = await getServerSession();
  if (!session) redirect("/login");
  return session;
}

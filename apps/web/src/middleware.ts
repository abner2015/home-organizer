// Route guard, and the only place that can renew a session *before* a render.
//
// Server Components cannot write cookies, so if an expired token reaches one
// the page can do nothing but render an error — the user looks signed in and
// every screen fails. Renewing here, on the way in, is what keeps an expired
// access token invisible.
//
// `/api/*` is deliberately NOT matched: those requests are proxied to the
// backend by the rewrite in `next.config.mjs` and must be allowed to return a
// real 401 JSON envelope rather than an HTML redirect. The browser side of
// renewal lives in `lib/api.ts` instead.

import { NextResponse, type NextRequest } from "next/server";

import { HOME_COOKIE, NAME_COOKIE, REFRESH_COOKIE, TOKEN_COOKIE } from "@/lib/cookies";
import { isExpired, secondsUntilExpiry } from "@/lib/jwt";

const PUBLIC_PATHS = ["/login", "/signup"];

// Same default as the rewrite in `next.config.mjs`. Middleware runs on the
// server, so it talks to the API directly rather than through the rewrite.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

function redirectToLogin(request: NextRequest): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

function setSessionCookie(
  response: NextResponse,
  name: string,
  value: string,
  maxAgeSeconds: number,
): void {
  response.cookies.set(name, value, {
    path: "/",
    maxAge: maxAgeSeconds,
    sameSite: "lax",
    httpOnly: false, // the browser session store reads these client-side
    // The `secure` flag only matters in production, where TLS terminates
    // upstream. Deriving it from the build-time env avoids threading the
    // request through every call.
    secure: process.env.NODE_ENV === "production",
  });
}

/**
 * Ask the API for a new token pair. Three outcomes, and they are not the same:
 * renewed, refused (the refresh token is dead — sign out), or unreachable
 * (the API is down — leave the session alone and let the page report the
 * outage; logging the user out over a blip would be worse than the outage).
 */
async function renew(
  refreshToken: string | undefined,
): Promise<
  | { status: "renewed"; tokens: TokenResponse }
  | { status: "refused" }
  | { status: "unreachable" }
> {
  if (!refreshToken) return { status: "refused" };
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!res.ok) return { status: "refused" };
    return { status: "renewed", tokens: (await res.json()) as TokenResponse };
  } catch {
    return { status: "unreachable" };
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  if (!token) return redirectToLogin(request);
  if (!isExpired(token)) return NextResponse.next();

  const result = await renew(request.cookies.get(REFRESH_COOKIE)?.value);

  if (result.status === "unreachable") return NextResponse.next();
  if (result.status === "refused") {
    const response = redirectToLogin(request);
    for (const name of [TOKEN_COOKIE, REFRESH_COOKIE, HOME_COOKIE, NAME_COOKIE]) {
      response.cookies.delete(name);
    }
    return response;
  }

  // Hand the *new* token to this very render by mutating the request cookies.
  // Setting only the response cookies would be too late: the render below reads
  // the request, so it would 401 once more before the next navigation picked
  // the new token up.
  request.cookies.set(TOKEN_COOKIE, result.tokens.access_token);
  request.cookies.set(REFRESH_COOKIE, result.tokens.refresh_token);
  const response = NextResponse.next({ request });
  const maxAge =
    secondsUntilExpiry(result.tokens.refresh_token) ?? result.tokens.expires_in;
  setSessionCookie(response, TOKEN_COOKIE, result.tokens.access_token, maxAge);
  setSessionCookie(response, REFRESH_COOKIE, result.tokens.refresh_token, maxAge);
  // Re-stamp the non-credential cookies too. They were written with the same
  // deadline as the pair the first time, so leaving them alone would let them
  // lapse on the original schedule while the token keeps renewing — the
  // session would go null with a perfectly good token still in hand.
  const home = request.cookies.get(HOME_COOKIE)?.value;
  const name = request.cookies.get(NAME_COOKIE)?.value;
  if (home) setSessionCookie(response, HOME_COOKIE, home, maxAge);
  if (name) setSessionCookie(response, NAME_COOKIE, name, maxAge);
  return response;
}

export const config = {
  // Everything but the API proxy, Next's own asset routes and the favicon.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};

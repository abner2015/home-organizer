// Route guard. Unauthenticated navigations land on /login instead of a page
// that would render an error state because every API call it makes 401s.
//
// `/api/*` is deliberately NOT matched: those requests are proxied to the
// backend by the rewrite in `next.config.mjs` and must be allowed to return a
// real 401 JSON envelope rather than an HTML redirect.

import { NextResponse, type NextRequest } from "next/server";
import { TOKEN_COOKIE } from "@/lib/cookies";

const PUBLIC_PATHS = ["/login", "/signup"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }
  if (request.cookies.get(TOKEN_COOKIE)?.value) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  // Everything but the API proxy, Next's own asset routes and the favicon.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};

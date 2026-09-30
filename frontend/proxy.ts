import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const COOKIE_NAME = "scanner_token";

/**
 * Route protection for the SecOps dashboard.
 *
 * Next.js 16 renamed the middleware convention to proxy.ts — this file is
 * the replacement for the deprecated middleware.ts (see frontend/AGENTS.md).
 *
 * Auth model (MVP):
 *  - httpOnly cookie `scanner_token` is set by /api/auth/login
 *  - proxy checks cookie *presence* only; JWT signature/expiry is enforced
 *    server-side by the scanner backend on every API call
 */
export function proxy(request: NextRequest) {
  const token = request.cookies.get(COOKIE_NAME)?.value;
  const { pathname } = request.nextUrl;

  const isProtected = pathname.startsWith("/dashboard");
  const isLogin = pathname === "/login" || pathname === "/";

  if (isProtected && !token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (isLogin && token) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/login", "/"],
};

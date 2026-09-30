import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const COOKIE_NAME = "scanner_token";

/**
 * UI auth entry point: exchange a scanner API key for a JWT.
 * Sets the JWT as an httpOnly cookie (used by proxy.ts for route protection)
 * and returns the token in the JSON body (used by the browser for Authorization
 * headers, since httpOnly cookies are not readable from JS).
 */
export async function POST(request: Request) {
  let body: { api_key?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 422 });
  }

  if (!body.api_key || typeof body.api_key !== "string") {
    return NextResponse.json({ detail: "api_key is required" }, { status: 422 });
  }

  let backendRes: Response;
  try {
    backendRes = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: body.api_key }),
    });
  } catch {
    return NextResponse.json({ detail: "Scanner backend unreachable" }, { status: 502 });
  }

  const data = await backendRes.json().catch(() => ({ detail: backendRes.statusText }));

  if (!backendRes.ok) {
    return NextResponse.json(data, { status: backendRes.status });
  }

  const response = NextResponse.json({
    token: data.token,
    token_type: data.token_type ?? "bearer",
    expires_in: data.expires_in,
    tenant_id: data.tenant_id,
  });

  const maxAge = typeof data.expires_in === "number" ? data.expires_in : 86400;
  response.cookies.set(COOKIE_NAME, data.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });

  return response;
}

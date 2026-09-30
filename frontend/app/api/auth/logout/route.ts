import { NextResponse } from "next/server";

const COOKIE_NAME = "scanner_token";

/** Clear the httpOnly auth cookie on logout. */
export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return response;
}

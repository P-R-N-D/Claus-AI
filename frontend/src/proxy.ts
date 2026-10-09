import { NextResponse, type NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isBackendPath = pathname.startsWith("/core/") || pathname.startsWith("/agent/");

  if (pathname !== "/" && pathname.endsWith("/") && !isBackendPath) {
    // A native URL avoids NextURL reapplying the incoming trailing-slash flag.
    const url = new URL(request.url);
    url.pathname = pathname.slice(0, -1);
    return NextResponse.redirect(url, 308);
  }

  return NextResponse.next();
}

export const config = {
  matcher: "/((?!_next(?:/|$)).*)",
};

import { NextResponse, type NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isBackendPath = pathname.startsWith("/core/") || pathname.startsWith("/agent/");

  if (pathname !== "/" && pathname.endsWith("/") && !isBackendPath) {
    // A native URL avoids NextURL reapplying the incoming trailing-slash flag.
    const url = new URL(request.url);
    // Collapse leading slashes so the Location can never become scheme-relative (//host).
    url.pathname = pathname.slice(0, -1).replace(/^\/{2,}/, "/");
    return NextResponse.redirect(url, 308);
  }

  return NextResponse.next();
}

export const config = {
  // Backend prefixes go straight to the rewrites; the isBackendPath check above stays as a guard.
  matcher: "/((?!_next(?:/|$)|core/|agent/).*)",
};

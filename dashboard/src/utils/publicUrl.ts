/**
 * Resolve an app-owned path under Vite's configured public base.
 * Accepts paths with or without a leading slash; output always has exactly one
 * base/path separator. This is for browser-facing routes and static assets,
 * not API paths (use getApiUrl for those).
 */
export function publicUrlForBase(path: string, base: string): string {
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(path)) return path;
  const cleaned = path.replace(/^\/+/, "");
  if (base === "/") return `/${cleaned}`;
  const prefix = base.endsWith("/") ? base : `${base}/`;
  const basePath = prefix.replace(/^\/+|\/+$/g, "");
  if (cleaned === basePath || cleaned.startsWith(`${basePath}/`)) {
    return `/${cleaned}`;
  }
  return `${prefix}${cleaned}`;
}

export function publicUrl(path: string): string {
  return publicUrlForBase(path, import.meta.env.BASE_URL || "/");
}

export function routerBasenameForBase(base: string): string | undefined {
  if (base === "/") return undefined;
  return base.replace(/\/+$/, "") || undefined;
}

/** Router basename has no trailing slash; undefined preserves root behavior. */
export function routerBasename(): string | undefined {
  return routerBasenameForBase(import.meta.env.BASE_URL || "/");
}

export function appPathnameForBase(pathname: string, base: string): string {
  const normalizedBase = routerBasenameForBase(base);
  if (!normalizedBase) return pathname;
  if (pathname === normalizedBase) return "/";
  return pathname.startsWith(`${normalizedBase}/`)
    ? pathname.slice(normalizedBase.length)
    : pathname;
}

export function appPathname(pathname: string): string {
  return appPathnameForBase(pathname, import.meta.env.BASE_URL || "/");
}

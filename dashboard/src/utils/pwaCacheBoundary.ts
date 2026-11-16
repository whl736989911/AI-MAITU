export const PRIVATE_API_RUNTIME_CACHE_NAMES = [
  "html-shell",
  "static-chunks",
  "3d-assets",
  "fonts",
  "api-readonly",
] as const;

export interface RuntimeCacheEntry {
  url: string;
}

export interface RuntimeCacheLike<
  Entry extends RuntimeCacheEntry = RuntimeCacheEntry,
> {
  keys(): Promise<readonly Entry[]>;
  delete(entry: Entry): Promise<boolean>;
}

export interface RuntimeCacheStorageLike<
  Entry extends RuntimeCacheEntry = RuntimeCacheEntry,
> {
  keys(): Promise<readonly string[]>;
  open(name: string): Promise<RuntimeCacheLike<Entry>>;
}

/** Self-contained so Workbox can serialize this callback into its worker. */
export function isScopedApiRequest(
  { url }: { url: URL },
  scope: string = (
    globalThis as typeof globalThis & { registration: { scope: string } }
  ).registration.scope,
): boolean {
  const scopeUrl = new URL(scope);
  if (url.origin !== scopeUrl.origin) return false;

  const decodePath = (path: string): string | null => {
    try {
      let decoded = path;
      for (;;) {
        const next = decodeURIComponent(decoded);
        if (next === decoded) return decoded;
        decoded = next;
      }
    } catch {
      return null;
    }
  };

  const decodedScopePath = decodePath(scopeUrl.pathname);
  const decodedRequestPath = decodePath(url.pathname);
  // Malformed same-origin paths fail closed to NetworkOnly.
  if (decodedScopePath === null || decodedRequestPath === null) return true;

  const scopePath = new URL(decodedScopePath, scopeUrl.origin).pathname;
  const requestPath = new URL(decodedRequestPath, scopeUrl.origin).pathname;
  const scopePrefix = scopePath.endsWith("/") ? scopePath : `${scopePath}/`;
  if (!requestPath.startsWith(scopePrefix)) return false;
  const scopedPath = requestPath.slice(scopePrefix.length);
  return scopedPath === "api" || scopedPath.startsWith("api/");
}

/** Remove only API requests accidentally cached by the dashboard's prior rules. */
export async function clearScopedApiRuntimeCacheEntries<
  Entry extends RuntimeCacheEntry,
>(cacheStorage: RuntimeCacheStorageLike<Entry>, scope: string): Promise<void> {
  const existingCaches = new Set(await cacheStorage.keys());
  for (const cacheName of PRIVATE_API_RUNTIME_CACHE_NAMES) {
    if (!existingCaches.has(cacheName)) continue;
    const cache = await cacheStorage.open(cacheName);
    for (const entry of await cache.keys()) {
      if (isScopedApiRequest({ url: new URL(entry.url) }, scope)) {
        await cache.delete(entry);
      }
    }
  }
}

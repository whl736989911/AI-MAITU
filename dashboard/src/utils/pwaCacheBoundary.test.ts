import { describe, expect, it } from "vitest";
import {
  clearScopedApiRuntimeCacheEntries,
  isScopedApiRequest,
  PRIVATE_API_RUNTIME_CACHE_NAMES,
  type RuntimeCacheEntry,
  type RuntimeCacheLike,
  type RuntimeCacheStorageLike,
} from "./pwaCacheBoundary";

const SCOPE = "https://dashboard.example/console/";

class FakeCache implements RuntimeCacheLike {
  readonly entries = new Map<string, RuntimeCacheEntry>();

  async keys(): Promise<RuntimeCacheEntry[]> {
    return [...this.entries.values()];
  }

  async delete(entry: RuntimeCacheEntry): Promise<boolean> {
    return this.entries.delete(entry.url);
  }

  add(url: string): void {
    this.entries.set(url, { url });
  }
}

class FakeCacheStorage implements RuntimeCacheStorageLike {
  readonly caches = new Map<string, FakeCache>();

  async keys(): Promise<string[]> {
    return [...this.caches.keys()];
  }

  async open(name: string): Promise<FakeCache> {
    let cache = this.caches.get(name);
    if (!cache) {
      cache = new FakeCache();
      this.caches.set(name, cache);
    }
    return cache;
  }
}

describe("isScopedApiRequest", () => {
  it("matches same-origin API paths under the worker scope, not query text", () => {
    expect(
      isScopedApiRequest(
        {
          url: new URL(
            "https://dashboard.example/console/api/agents/a/workspace/download?path=private.gif",
          ),
        },
        SCOPE,
      ),
    ).toBe(true);
    expect(
      isScopedApiRequest(
        {
          url: new URL(
            "https://dashboard.example/console/api/agents/a/workspace/download?path=assets/secret.js",
          ),
        },
        SCOPE,
      ),
    ).toBe(true);
    expect(
      isScopedApiRequest(
        {
          url: new URL("https://dashboard.example/console/api/usage/summary"),
        },
        SCOPE,
      ),
    ).toBe(true);
  });

  it("recognizes encoded API path components and rejects other origins/scopes", () => {
    expect(
      isScopedApiRequest(
        {
          url: new URL("https://dashboard.example/%63onsole/%61pi%2Fagents/a"),
        },
        SCOPE,
      ),
    ).toBe(true);
    expect(
      isScopedApiRequest(
        {
          url: new URL("https://dashboard.example/console/apiary/private.gif"),
        },
        SCOPE,
      ),
    ).toBe(false);
    expect(
      isScopedApiRequest(
        { url: new URL("https://dashboard.example/other/api/private.gif") },
        SCOPE,
      ),
    ).toBe(false);
    expect(
      isScopedApiRequest(
        { url: new URL("https://cdn.example/console/api/private.gif") },
        SCOPE,
      ),
    ).toBe(false);
  });
});

describe("clearScopedApiRuntimeCacheEntries", () => {
  it("removes only scoped private API entries from known runtime caches", async () => {
    const storage = new FakeCacheStorage();
    const staticChunks = await storage.open("static-chunks");
    const models = await storage.open("3d-assets");
    const fonts = await storage.open("fonts");
    const html = await storage.open("html-shell");
    const oldApiCache = await storage.open("api-readonly");
    const otherApp = await storage.open("other-app-cache");

    staticChunks.add(
      "https://dashboard.example/console/api/agents/a/workspace/download?path=assets/secret.js",
    );
    staticChunks.add("https://dashboard.example/console/assets/index.a1.js");
    staticChunks.add("https://dashboard.example/other/api/private.js");
    models.add(
      "https://dashboard.example/console/api/agents/a/workspace/download?path=private.gif",
    );
    fonts.add(
      "https://dashboard.example/console/api/agents/a/workspace/download?path=private.woff2",
    );
    html.add("https://dashboard.example/console/api/auth/me");
    oldApiCache.add("https://dashboard.example/console/api/auth/me");
    otherApp.add("https://dashboard.example/console/api/auth/me");

    await clearScopedApiRuntimeCacheEntries(storage, SCOPE);

    expect([...staticChunks.entries.keys()]).toEqual([
      "https://dashboard.example/console/assets/index.a1.js",
      "https://dashboard.example/other/api/private.js",
    ]);
    expect(models.entries.size).toBe(0);
    expect(fonts.entries.size).toBe(0);
    expect(html.entries.size).toBe(0);
    expect(oldApiCache.entries.size).toBe(0);
    expect(otherApp.entries.size).toBe(1);
  });
});

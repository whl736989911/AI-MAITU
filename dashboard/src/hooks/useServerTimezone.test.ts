import { describe, expect, it } from "vitest";

import {
  applyTimezoneFetchResult,
  DEFAULT_SERVER_TIMEZONE,
} from "./useServerTimezone";

describe("applyTimezoneFetchResult", () => {
  it("does not cache a failed fetch so the next mount can retry", () => {
    const result = applyTimezoneFetchResult({ ok: false });
    expect(result.value).toBe(DEFAULT_SERVER_TIMEZONE);
    expect(result.cache).toBeNull();
  });

  it("caches a trimmed server timezone", () => {
    const result = applyTimezoneFetchResult({
      ok: true,
      timezone: " Asia/Tokyo ",
    });
    expect(result.value).toBe("Asia/Tokyo");
    expect(result.cache).toBe("Asia/Tokyo");
  });

  it("uses and caches UTC for an empty successful response", () => {
    const result = applyTimezoneFetchResult({ ok: true, timezone: "  " });
    expect(result.value).toBe(DEFAULT_SERVER_TIMEZONE);
    expect(result.cache).toBe(DEFAULT_SERVER_TIMEZONE);
  });
});

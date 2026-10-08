import { useEffect, useState } from "react";
import { octopSettingsApi } from "../api/modules/settings";

export const DEFAULT_SERVER_TIMEZONE = "UTC";

let cachedTimezone: string | null = null;
let inflight: Promise<string> | null = null;

export function applyTimezoneFetchResult(
  result: { ok: true; timezone: unknown } | { ok: false },
): { cache: string | null; value: string } {
  if (!result.ok) {
    return { cache: null, value: DEFAULT_SERVER_TIMEZONE };
  }
  const timezone =
    typeof result.timezone === "string" ? result.timezone.trim() : "";
  const value = timezone || DEFAULT_SERVER_TIMEZONE;
  return { cache: value, value };
}

async function fetchServerTimezone(): Promise<string> {
  if (cachedTimezone) return cachedTimezone;
  if (!inflight) {
    inflight = octopSettingsApi
      .timezone()
      .then((settings) => {
        const next = applyTimezoneFetchResult({
          ok: true,
          timezone: settings.timezone,
        });
        cachedTimezone = next.cache;
        return next.value;
      })
      .catch(() => {
        const next = applyTimezoneFetchResult({ ok: false });
        cachedTimezone = next.cache;
        return next.value;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Server timezone from config.json `default_timezone` (via GET /api/settings/timezone). */
export function useServerTimezone(): string {
  const [timezone, setTimezone] = useState(
    cachedTimezone ?? DEFAULT_SERVER_TIMEZONE,
  );

  useEffect(() => {
    void fetchServerTimezone().then(setTimezone);
  }, []);

  return timezone;
}

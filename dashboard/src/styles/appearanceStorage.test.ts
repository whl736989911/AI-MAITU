import { afterEach, describe, expect, it } from "vitest";
import {
  loadAppearanceOnBoot,
  readStoredAppearance,
  writeStoredAppearance,
} from "./appearanceStorage";
import {
  DEFAULT_CUSTOM_COLOR,
  LEGACY_PALETTE_STORAGE_KEY,
  THEME_STORAGE_KEY,
} from "./themePalettes";

const AUTH_PROBE_KEY = "auth-token-preservation-probe";

afterEach(() => {
  localStorage.removeItem(THEME_STORAGE_KEY);
  localStorage.removeItem(LEGACY_PALETTE_STORAGE_KEY);
  localStorage.removeItem(AUTH_PROBE_KEY);
});

describe("appearanceStorage", () => {
  it("migrates legacy plain theme string + palette key", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    localStorage.setItem(LEGACY_PALETTE_STORAGE_KEY, "tech");

    const appearance = readStoredAppearance();
    expect(appearance.preference).toBe("dark");
    expect(appearance.palette).toBe("tech");
  });

  it("reads unified JSON under the theme key", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ preference: "light", palette: "indigo" }),
    );

    const appearance = readStoredAppearance();
    expect(appearance.preference).toBe("light");
    expect(appearance.palette).toBe("indigo");
  });
  it("migrates only the unversioned historic red default while preserving mode", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        preference: "dark",
        palette: "custom",
        customColor: "#e5484d",
      }),
    );
    localStorage.setItem(AUTH_PROBE_KEY, "must-remain");

    const appearance = loadAppearanceOnBoot();
    expect(appearance).toMatchObject({ preference: "dark", palette: "brand" });
    expect(
      JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) ?? "{}"),
    ).toMatchObject({ version: 2, preference: "dark", palette: "brand" });
    expect(localStorage.getItem(AUTH_PROBE_KEY)).toBe("must-remain");
  });

  it("preserves unversioned custom colors other than the historic default", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        preference: "light",
        palette: "custom",
        customColor: "#123456",
      }),
    );
    expect(loadAppearanceOnBoot()).toMatchObject({
      preference: "light",
      palette: "custom",
      customColor: "#123456",
    });
  });

  it("preserves an explicitly selected red custom color in the current format", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        preference: "system",
        palette: "custom",
        customColor: "#e5484d",
      }),
    );
    expect(loadAppearanceOnBoot()).toMatchObject({
      palette: "custom",
      customColor: "#e5484d",
    });
  });

  it("reads the custom palette with its brand hex", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        preference: "light",
        palette: "custom",
        customColor: "#AB5E50",
      }),
    );

    const appearance = readStoredAppearance();
    expect(appearance.palette).toBe("custom");
    expect(appearance.customColor).toBe("#ab5e50");
  });

  it("falls back to the default custom color on invalid hex", () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        preference: "light",
        palette: "custom",
        customColor: "not-a-color",
      }),
    );

    expect(readStoredAppearance().customColor).toBe(DEFAULT_CUSTOM_COLOR);
  });

  it("writes preference and palette as different fields in the same key", () => {
    writeStoredAppearance({ preference: "system", palette: "teal" });
    localStorage.setItem(LEGACY_PALETTE_STORAGE_KEY, "should-be-removed");

    writeStoredAppearance({
      preference: "light",
      palette: "custom",
      customColor: "#00AA55",
    });

    expect(localStorage.getItem(LEGACY_PALETTE_STORAGE_KEY)).toBeNull();
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!)).toEqual({
      version: 2,
      preference: "light",
      palette: "custom",
      customColor: "#00aa55",
    });
  });

  it("loadAppearanceOnBoot migrates legacy keys into unified JSON", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    localStorage.setItem(LEGACY_PALETTE_STORAGE_KEY, "amber");

    const appearance = loadAppearanceOnBoot();
    expect(appearance.preference).toBe("dark");
    expect(appearance.palette).toBe("amber");
    expect(localStorage.getItem(LEGACY_PALETTE_STORAGE_KEY)).toBeNull();
    expect(JSON.parse(localStorage.getItem(THEME_STORAGE_KEY)!)).toEqual({
      version: 2,
      preference: "dark",
      palette: "amber",
      customColor: DEFAULT_CUSTOM_COLOR.toLowerCase(),
    });
  });

  it("falls back safely on invalid JSON or unknown values", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "{not-json");
    expect(readStoredAppearance().preference).toBe("system");
    expect(readStoredAppearance().palette).toBe("brand");

    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ preference: "neon", palette: "pink" }),
    );
    expect(readStoredAppearance().preference).toBe("system");
    expect(readStoredAppearance().palette).toBe("brand");
  });
});

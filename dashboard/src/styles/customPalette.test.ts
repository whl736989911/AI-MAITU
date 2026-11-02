import { describe, expect, it } from "vitest";

import {
  brandPrimary,
  brandTokensFor,
  contrastRatio,
  customBrandTokens,
  deriveCustomBrandColors,
  mixHex,
  normalizeHexColor,
} from "./themePalettes";

describe("normalizeHexColor", () => {
  it("normalizes 3/6-digit hex with or without #", () => {
    expect(normalizeHexColor("#4B74FA")).toBe("#4b74fa");
    expect(normalizeHexColor("4b74fa")).toBe("#4b74fa");
    expect(normalizeHexColor("#ABC")).toBe("#aabbcc");
    expect(normalizeHexColor(" abc ")).toBe("#aabbcc");
  });

  it("rejects invalid input with null", () => {
    expect(normalizeHexColor("")).toBeNull();
    expect(normalizeHexColor(null)).toBeNull();
    expect(normalizeHexColor("#12345")).toBeNull();
    expect(normalizeHexColor("#1234567")).toBeNull();
    expect(normalizeHexColor("red")).toBeNull();
    expect(normalizeHexColor("rgb(1,2,3)")).toBeNull();
  });
});

describe("mixHex", () => {
  it("interpolates between colors", () => {
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mixHex("#000000", "#ffffff", 0)).toBe("#000000");
    expect(mixHex("#000000", "#ffffff", 1)).toBe("#ffffff");
  });
});

describe("deriveCustomBrandColors", () => {
  it("darkens light colors until white text is readable", () => {
    const { solid } = deriveCustomBrandColors("#FFFF00"); // pure yellow
    expect(contrastRatio(solid, "#FFFFFF")).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps already-dark colors mostly unchanged", () => {
    const { solid } = deriveCustomBrandColors("#1E3A8A");
    expect(contrastRatio(solid, "#FFFFFF")).toBeGreaterThanOrEqual(4.5);
    // Stays in the same hue family (blue channel dominant).
    const r = Number.parseInt(solid.slice(1, 3), 16);
    const b = Number.parseInt(solid.slice(5, 7), 16);
    expect(b).toBeGreaterThan(r);
  });

  it("brightens dark colors for dark-mode text", () => {
    const { onDark } = deriveCustomBrandColors("#000000");
    expect(contrastRatio(onDark, "#0f1117")).toBeGreaterThanOrEqual(3);
  });
});

describe("customBrandTokens", () => {
  it("produces WCAG-AA solid states for light mode", () => {
    const tokens = customBrandTokens("#F87171").light;
    for (const c of [
      tokens.colorPrimary,
      tokens.colorPrimaryHover,
      tokens.colorPrimaryActive,
    ]) {
      expect(contrastRatio(c, "#FFFFFF")).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("produces readable link colors for dark mode", () => {
    const tokens = customBrandTokens("#7C2D12").dark;
    expect(contrastRatio(tokens.colorLink, "#0f1117")).toBeGreaterThanOrEqual(
      3,
    );
  });
});

describe("brandTokensFor / brandPrimary with custom palette", () => {
  it("derives tokens from the custom hex", () => {
    const light = brandTokensFor("custom", false, "#FF0000");
    expect(light.colorPrimary).not.toBe("#FF0000"); // darkened for AA
    expect(contrastRatio(light.colorPrimary, "#FFFFFF")).toBeGreaterThanOrEqual(
      4.5,
    );
  });

  it("curated palettes ignore customColor", () => {
    expect(brandPrimary("rose", false, "#00FF00")).toBe(
      brandPrimary("rose", false),
    );
  });
  it("tracks runtime brand changes while custom colors take precedence", () => {
    const initialBrand = brandTokensFor("brand", false, undefined, "#4F46E5");
    const updatedBrand = brandTokensFor("brand", false, undefined, "#0891B2");
    expect(updatedBrand.colorPrimary).not.toBe(initialBrand.colorPrimary);
    expect(brandPrimary("brand", false, undefined, "#0891B2")).toBe(
      updatedBrand.colorPrimary,
    );

    const selectedCustom = brandTokensFor(
      "custom",
      false,
      "#D946EF",
      "#0891B2",
    );
    expect(selectedCustom).toEqual(brandTokensFor("custom", false, "#D946EF"));
  });
});

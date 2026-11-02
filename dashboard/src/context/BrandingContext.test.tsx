import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../api/request";
import { THEME_STORAGE_KEY } from "../styles/themePalettes";
import { BrandingProvider, type Branding } from "./BrandingContext";
import { ThemeProvider } from "./ThemeContext";

vi.mock("../api/request", () => ({
  request: vi.fn(),
  requestUpload: vi.fn(),
}));

function makeBrand(accent: string): Branding {
  return {
    name: { zh: "测试品牌", en: "Test Brand" },
    full_name: { zh: "测试品牌", en: "Test Brand" },
    short_name: { zh: "测试", en: "Test" },
    description: { zh: "测试品牌", en: "Test Brand" },
    colors: { brand: accent, accent },
    pwa: { theme_color: accent, background_color: "#ffffff" },
    logos: {
      mark: "/brand/mark.png",
      wordmark_light: "/brand/light.png",
      wordmark_dark: "/brand/dark.png",
      favicon: "/brand/favicon.ico",
      apple_touch_icon: "/brand/apple-touch-icon.png",
      pwa_192: "/brand/pwa-192.png",
      pwa_512: "/brand/pwa-512.png",
    },
    version: "test",
    is_custom: true,
  };
}

function color(variable: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(variable)
    .trim()
    .toLowerCase();
}

function renderBranding() {
  render(
    <ThemeProvider>
      <BrandingProvider>
        <div />
      </BrandingProvider>
    </ThemeProvider>,
  );
}

describe("BrandingProvider palette", () => {
  beforeEach(() => {
    localStorage.removeItem(THEME_STORAGE_KEY);
    localStorage.removeItem("octop:ui-palette");
    document.documentElement.style.cssText = "";
    vi.mocked(request).mockResolvedValue(makeBrand("#123456"));
  });

  afterEach(() => {
    cleanup();
    localStorage.removeItem(THEME_STORAGE_KEY);
    document.getElementById("octop-brand-palette")?.remove();
    document.getElementById("octop-custom-palette")?.remove();
    document.documentElement.style.cssText = "";
    document.documentElement.removeAttribute("data-palette");
    document.documentElement.removeAttribute("data-theme");
  });

  it("updates primary, focus and selected backgrounds together after a brand change", async () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({ version: 2, preference: "light", palette: "brand" }),
    );
    renderBranding();

    await waitFor(() => {
      expect(color("--fn-color-brand")).toBe("#123456");
      expect(color("--fn-border-focus")).toBe("#123456");
      expect(color("--fn-bg-selected")).toBe("rgba(18, 52, 86, 0.06)");
    });

    vi.mocked(request).mockResolvedValue(makeBrand("#0369a1"));
    await act(async () => {
      window.dispatchEvent(new Event("octop:branding-updated"));
    });

    await waitFor(() => {
      expect(color("--fn-color-brand")).toBe("#0369a1");
      expect(color("--fn-border-focus")).toBe("#0369a1");
      expect(color("--fn-bg-selected")).toBe("rgba(3, 105, 161, 0.06)");
      expect(color("--fn-sidebar-item-active-text")).toBe("#0369a1");
    });
  });

  it("does not replace an explicitly selected custom palette when branding changes", async () => {
    localStorage.setItem(
      THEME_STORAGE_KEY,
      JSON.stringify({
        version: 2,
        preference: "light",
        palette: "custom",
        customColor: "#123456",
      }),
    );
    vi.mocked(request).mockResolvedValue(makeBrand("#4f46e5"));
    renderBranding();

    await waitFor(() => {
      expect(color("--fn-color-brand")).toBe("#123456");
      expect(color("--fn-border-focus")).toBe("#123456");
    });

    vi.mocked(request).mockResolvedValue(makeBrand("#0369a1"));
    await act(async () => {
      window.dispatchEvent(new Event("octop:branding-updated"));
    });

    await waitFor(() => {
      expect(color("--octop-accent-color")).toBe("#0369a1");
      expect(color("--fn-color-brand")).toBe("#123456");
      expect(color("--fn-border-focus")).toBe("#123456");
      expect(color("--fn-bg-selected")).toBe("rgba(18, 52, 86, 0.06)");
    });
  });
});

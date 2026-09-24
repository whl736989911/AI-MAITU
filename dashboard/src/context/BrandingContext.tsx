import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { BRAND } from "../brand.generated";
import { request, requestUpload } from "../api/request";
import { DEFAULT_PALETTE } from "../styles/themePalettes";
import { useTheme } from "./ThemeContext";

export interface Branding {
  name: { zh: string; en: string };
  full_name: { zh: string; en: string };
  short_name: { zh: string; en: string };
  description: { zh: string; en: string };
  colors: { brand: string; accent: string };
  pwa: { theme_color: string; background_color: string };
  logos: Record<"mark" | "wordmark_light" | "wordmark_dark" | "favicon" | "apple_touch_icon" | "pwa_192" | "pwa_512", string>;
  version: string;
  is_custom: boolean;
}

const FALLBACK: Branding = {
  name: BRAND.name,
  full_name: BRAND.fullName,
  short_name: BRAND.shortName,
  description: BRAND.description,
  colors: BRAND.color,
  pwa: { theme_color: "#ffffff", background_color: "#0f1117" },
  logos: {
    mark: BRAND.logo.mark,
    wordmark_light: BRAND.logo.wordmarkLight,
    wordmark_dark: BRAND.logo.wordmarkDark,
    favicon: BRAND.logo.favicon,
    apple_touch_icon: BRAND.logo.appleTouchIcon,
    pwa_192: BRAND.logo.pwa192,
    pwa_512: BRAND.logo.pwa512,
  },
  version: "default",
  is_custom: false,
};

const BrandingContext = createContext<Branding>(FALLBACK);

function applyBranding(brand: Branding, applyAccent: boolean) {
  document.title = brand.name[document.documentElement.lang.startsWith("zh") ? "zh" : "en"];
  const favicon = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  if (favicon) favicon.href = brand.logos.favicon;
  let apple = document.querySelector<HTMLLinkElement>("link[rel='apple-touch-icon']");
  if (!apple) {
    apple = document.createElement("link");
    apple.rel = "apple-touch-icon";
    document.head.appendChild(apple);
  }
  apple.href = brand.logos.apple_touch_icon;
  const appleTitle = document.querySelector<HTMLMetaElement>("meta[name='apple-mobile-web-app-title']");
  if (appleTitle) appleTitle.content = brand.name[document.documentElement.lang.startsWith("zh") ? "zh" : "en"];
  const root = document.documentElement;
  document.querySelectorAll<HTMLMetaElement>("meta[name='theme-color']").forEach((meta) => {
    meta.content = brand.pwa.theme_color;
  });
  root.style.setProperty("--octop-theme-color", brand.pwa.theme_color);
  root.style.setProperty("--octop-brand-color", brand.colors.brand);
  root.style.setProperty("--octop-accent-color", brand.colors.accent);
  if (applyAccent) {
    root.style.setProperty("--fn-color-brand", brand.colors.accent);
    root.style.setProperty("--fn-text-brand", brand.colors.accent);
    root.style.setProperty("--fn-logo-color", brand.colors.brand);
    root.style.setProperty("--octop-accent-hover", brand.colors.accent);
  } else {
    root.style.removeProperty("--fn-color-brand");
    root.style.removeProperty("--fn-text-brand");
    root.style.removeProperty("--fn-logo-color");
    root.style.removeProperty("--octop-accent-hover");
  }
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [brand, setBrand] = useState<Branding>(FALLBACK);
  const { palette } = useTheme();
  useEffect(() => {
    let live = true;
    const load = () => {
      void request<Branding>("/branding", { cache: "no-store" })
        .then((value) => { if (live) setBrand(value); })
        .catch(() => undefined);
    };
    load();
    window.addEventListener("octop:branding-updated", load);
    return () => {
      live = false;
      window.removeEventListener("octop:branding-updated", load);
    };
  }, []);
  useEffect(() => {
    applyBranding(brand, palette === DEFAULT_PALETTE);
  }, [brand, palette]);
  return <BrandingContext.Provider value={brand}>{children}</BrandingContext.Provider>;
}


export function useBranding(): Branding {
  return useContext(BrandingContext);
}

export const brandingApi = {
  get: () => request<Branding>("/branding", { cache: "no-store" }),
  put: (body: Partial<Branding>) => request<Branding>("/branding", { method: "PUT", body: JSON.stringify(body) }),
  reset: () => request<Branding>("/branding", { method: "DELETE" }),
  uploadLogo: async (slot: keyof Branding["logos"], file: File) => {
    const body = new FormData();
    body.append("file", file);
    return requestUpload<{ url: string }>(`/branding/logo/${slot}`, body);
  },
};

import { Layout } from "antd";
import { Menu as MenuIcon } from "lucide-react";
import PwaInstallPrompt from "../components/PwaInstallPrompt";
import CurrentVersionBadge from "../components/CurrentVersionBadge";
import { useTheme } from "../context/ThemeContext";
import { useBranding } from "../context/BrandingContext";
import { typeSize } from "../utils/mobileTypeScale";

const { Header: AntHeader } = Layout;

interface HeaderProps {
  selectedKey?: string;
  collapsed?: boolean;
  onToggle?: () => void;
  isMobile?: boolean;
}

/**
 * Mobile-only top chrome: brand + nav toggle + install.
 * Desktop GitHub / theme controls moved into the account popover.
 */
export default function Header({ onToggle, isMobile }: HeaderProps) {
  const { isDark } = useTheme();
  const brand = useBranding();
  const mobileLogoSrc = isDark
    ? brand.logos.wordmark_dark
    : brand.logos.wordmark_light;

  if (!isMobile) return null;
  const safeTop = "env(safe-area-inset-top, 0px)";

  return (
    <AntHeader
      style={{
        height: `calc(var(--fn-header-height) + ${safeTop})`,
        padding: `${safeTop} 12px 0`,
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "var(--fn-header-bg)",
        backdropFilter: "blur(var(--fn-header-blur))",
        WebkitBackdropFilter: "blur(var(--fn-header-blur))",
        borderBottom: "1px solid var(--fn-border-primary)",
        transition: "background var(--fn-transition)",
        flexShrink: 0,
        zIndex: 20,
      }}
    >
      <div
        style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}
      >
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-label="Open navigation"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: typeSize(34, true),
              height: typeSize(34, true),
              border: "none",
              borderRadius: "var(--fn-radius-md)",
              background: "transparent",
              color: "var(--fn-text-tertiary)",
              cursor: "pointer",
              transition: "all var(--fn-transition-fast)",
              flexShrink: 0,
            }}
          >
            <MenuIcon size={20} strokeWidth={1.8} />
          </button>
        )}
        <img
          src={mobileLogoSrc}
          alt={brand.name.en}
          style={{
            height: 36,
            width: "auto",
            maxWidth: 160,
            objectFit: "contain",
            flexShrink: 0,
            display: "block",
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            minWidth: 0,
            flexShrink: 1,
          }}
        >
          <CurrentVersionBadge isMobile />
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          flexShrink: 0,
        }}
      >
        <PwaInstallPrompt compact />
      </div>
    </AntHeader>
  );
}

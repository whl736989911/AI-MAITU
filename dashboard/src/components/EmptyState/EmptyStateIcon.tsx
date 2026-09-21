import type { LucideIcon } from "lucide-react";

interface EmptyStateIconProps {
  /** Lucide glyph drawn above the placeholder copy. */
  icon: LucideIcon;
  /** Glyph edge in px; default 72 for page-level placeholders. */
  size?: number;
}

/**
 * Muted glyph for empty / setup-guide placeholders that need a larger mark than
 * ``EmptyState``'s default icon. Keeps every placeholder on the same lucide
 * weight and muted token.
 */
export function EmptyStateIcon({ icon: Icon, size = 72 }: EmptyStateIconProps) {
  return (
    <Icon
      size={size}
      strokeWidth={1.2}
      aria-hidden
      style={{ color: "var(--fn-text-quaternary, #bfbfbf)" }}
    />
  );
}

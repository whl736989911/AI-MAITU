// dashboard/src/pages/Experts/components/ExpertCard.tsx
import { memo, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle } from "lucide-react";
import { pickLocale } from "../../../utils/localizedText";
import { ExpertIcon, resolveExpertAvatarUrl } from "./iconForName";
import styles from "../index.module.less";

export interface ExpertSummary {
  id: string;
  label: { zh?: string; en?: string };
  description: { zh?: string; en?: string };
  welcome_message?: { zh?: string; en?: string };
  icon_name?: string | null;
  icon_url?: string | null;
  color?: string | null;
  files?: string[];
  task_examples?: { zh?: string[]; en?: string[] } | null;
}

/**
 * The accent hook the card's own styles read (border, hover wash, footer
 * hint). A caller that names its own tint differently — the feature catalog's
 * ``--feature-tint`` — passes it as ``accentVar``; the hook is then chained to
 * that token so a single value still drives both names.
 */
const ACCENT_HOOK = "--expert-accent";

export interface TemplateCardProps {
  /** Localized card title. */
  title: string;
  /** Localized description; an empty one keeps the card's two-line slot. */
  description: string;
  /** Colour behind the icon chip, the hover border and the footer hint. */
  accent: string;
  /**
   * Draws the chip's glyph. The card hands it the box it has: 44px when the
   * chip is an avatar, 20px for the square one.
   */
  renderIcon: (size: number) => ReactNode;
  /** Draw the chip as a 44px avatar rather than the tinted square. */
  portrait?: boolean;
  /** Custom property to publish ``accent`` under; see ``ACCENT_HOOK``. */
  accentVar?: string;
  /** Row under the description — a template's call to action, a badge. */
  footer?: ReactNode;
  onClick: () => void;
}

/**
 * The template card. Expert templates and feature-catalog entries are the same
 * card; they differ only in the icon they draw, the name they publish their
 * accent under, and whether they have a footer.
 *
 * It is a ``button`` — the whole card is one click target, so it has to be
 * reachable and operable from the keyboard.
 */
export function TemplateCard({
  title,
  description,
  accent,
  renderIcon,
  portrait = false,
  accentVar = ACCENT_HOOK,
  footer,
  onClick,
}: TemplateCardProps) {
  return (
    <button
      type="button"
      className={styles.expertTemplateCard}
      style={accentStyle(accentVar, accent)}
      onClick={onClick}
    >
      {/* Icon + title */}
      <span className={styles.expertTemplateHeader}>
        <span
          className={
            portrait
              ? `${styles.agentCardIcon} ${styles.agentCardPortrait}`
              : styles.agentCardIcon
          }
          style={
            portrait
              ? undefined
              : {
                  color: accent,
                  background: `${accent}18`,
                }
          }
        >
          {renderIcon(portrait ? 44 : 20)}
        </span>
        <span className={styles.agentCardTitleBlock}>
          <span className={styles.agentCardName}>{title}</span>
        </span>
      </span>

      {/* Description */}
      <span className={styles.agentCardDesc}>{description || "\u00a0"}</span>

      {/* Footer */}
      {footer ? (
        <span className={styles.expertCardFooter}>{footer}</span>
      ) : null}
    </button>
  );
}

/**
 * ``accent`` published under the caller's token. The card's styles read
 * ``ACCENT_HOOK``, so when the caller names its token something else the hook
 * is pointed at it rather than being set twice.
 */
function accentStyle(token: string, accent: string): CSSProperties {
  if (token === ACCENT_HOOK) return { [ACCENT_HOOK]: accent } as CSSProperties;
  return {
    [token]: accent,
    [ACCENT_HOOK]: `var(${token})`,
  } as CSSProperties;
}

interface ExpertCardProps {
  expert: ExpertSummary;
  lang: "zh" | "en";
  isInstalled: boolean;
  onCreate: (expert: ExpertSummary) => void;
}

/** A built-in expert template, rendered as the shared template card. */
export const ExpertCard = memo(function ExpertCard({
  expert,
  lang,
  isInstalled,
  onCreate,
}: ExpertCardProps) {
  const { t } = useTranslation();
  const portraitUrl = resolveExpertAvatarUrl(expert.icon_url);

  return (
    <TemplateCard
      title={pickLocale(expert.label, lang) || expert.id}
      description={pickLocale(expert.description, lang)}
      accent={expert.color || "var(--fn-color-brand)"}
      portrait={Boolean(portraitUrl)}
      renderIcon={(size) => (
        <ExpertIcon
          iconUrl={portraitUrl}
          iconName={expert.icon_name}
          size={size}
        />
      )}
      footer={
        <>
          <span className={styles.expertCardHint}>
            {t("experts.createFromTemplate")}
          </span>
          {isInstalled && (
            <span className={styles.expertInstalledLabel}>
              <CheckCircle size={12} />
              {t("experts.installedBadge")}
            </span>
          )}
        </>
      }
      onClick={() => onCreate(expert)}
    />
  );
});

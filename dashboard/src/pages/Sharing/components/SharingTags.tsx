import { Tag, Tooltip } from "antd";
import {
  Building2,
  CheckCircle2,
  Clock,
  Eye,
  Globe2,
  Lock,
  Pencil,
  Undo2,
  UserRound,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BRAND } from "../../../brand.generated";
import type {
  SharingChangeStatus,
  SharingImpactScope,
  SharingPermission,
  SharingVisibility,
} from "../../../api/modules/sharing";
import {
  IMPACT_LABEL_KEYS,
  IMPACT_SHORT_KEYS,
  PERMISSION_LABEL_KEYS,
  STATUS_LABEL_KEYS,
  VISIBILITY_LABEL_KEYS,
} from "../labels";

const ICON_SIZE = 12;

/**
 * Who can reach a resource. ``public`` is painted with the brand accent because
 * it is the one state that crosses the whole organization.
 */
export function VisibilityTag({
  visibility,
}: {
  visibility: SharingVisibility;
}) {
  const { t } = useTranslation();
  const publicReach = visibility === "public";
  return (
    <Tag
      color={
        publicReach
          ? BRAND.color.accent
          : visibility === "unit"
          ? "blue"
          : undefined
      }
      icon={
        visibility === "public" ? (
          <Globe2 size={ICON_SIZE} />
        ) : visibility === "unit" ? (
          <Building2 size={ICON_SIZE} />
        ) : (
          <Lock size={ICON_SIZE} />
        )
      }
      style={{ marginInlineEnd: 0 }}
    >
      {t(VISIBILITY_LABEL_KEYS[visibility])}
    </Tag>
  );
}

/** What a share lets a matching viewer do: reach the resource, or also maintain it. */
export function PermissionTag({
  permission,
}: {
  permission: SharingPermission;
}) {
  const { t } = useTranslation();
  const writable = permission === "write";
  return (
    <Tag
      color={writable ? "gold" : undefined}
      icon={writable ? <Pencil size={ICON_SIZE} /> : <Eye size={ICON_SIZE} />}
      style={{ marginInlineEnd: 0 }}
    >
      {t(PERMISSION_LABEL_KEYS[permission])}
    </Tag>
  );
}

/** How far a change reaches; ``org`` is the scope the approval gate exists for. */
export function ImpactScopeTag({ scope }: { scope: SharingImpactScope }) {
  const { t } = useTranslation();
  return (
    <Tooltip title={t(IMPACT_LABEL_KEYS[scope])}>
      <Tag
        color={
          scope === "org"
            ? BRAND.color.accent
            : scope === "unit"
            ? "blue"
            : undefined
        }
        icon={
          scope === "org" ? (
            <Globe2 size={ICON_SIZE} />
          ) : scope === "unit" ? (
            <Building2 size={ICON_SIZE} />
          ) : (
            <UserRound size={ICON_SIZE} />
          )
        }
        style={{ marginInlineEnd: 0 }}
      >
        {t(IMPACT_SHORT_KEYS[scope])}
      </Tag>
    </Tooltip>
  );
}

const STATUS_ICONS: Record<SharingChangeStatus, LucideIcon> = {
  pending_approval: Clock,
  applied: CheckCircle2,
  rejected: XCircle,
  rolled_back: Undo2,
};

const STATUS_COLORS: Record<SharingChangeStatus, string | undefined> = {
  pending_approval: "gold",
  applied: "green",
  rejected: "red",
  rolled_back: undefined,
};

/** Lifecycle chip for one change-log row. */
export function StatusTag({ status }: { status: SharingChangeStatus }) {
  const { t } = useTranslation();
  const Icon = STATUS_ICONS[status];
  return (
    <Tag
      color={STATUS_COLORS[status]}
      icon={<Icon size={ICON_SIZE} />}
      style={{ marginInlineEnd: 0 }}
    >
      {t(STATUS_LABEL_KEYS[status])}
    </Tag>
  );
}

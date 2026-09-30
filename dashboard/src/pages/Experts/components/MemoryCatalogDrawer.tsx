// dashboard/src/pages/Experts/components/MemoryCatalogDrawer.tsx
import { useTranslation } from "react-i18next";
import { useIsMobile } from "../../../hooks/useIsMobile";
import MemoryPanel from "../../Agent/Memory/MemoryPanel";
import CatalogDrawer from "./CatalogDrawer";

interface MemoryCatalogDrawerProps {
  agentId: string;
  open: boolean;
  onClose: () => void;
  /** Feature callers use the unified overlay plus stage-scoped memory editor. */
  featureMemory?: boolean;
  /** Show only read-only memory. Defaults to false, which is every expert. */
  readOnly?: boolean;
}

/** Experts modal embedding the full Memory surface. */
export default function MemoryCatalogDrawer({
  agentId,
  open,
  onClose,
  readOnly = false,
  featureMemory = false,
}: MemoryCatalogDrawerProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <CatalogDrawer
      title={t(
        featureMemory
          ? "personalization.myPersonalization"
          : "pageShell.memory.title",
      )}
      open={open}
      onClose={onClose}
    >
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <MemoryPanel
          key={featureMemory ? `${agentId}:${open}` : agentId}
          agentId={agentId || null}
          fill={!isMobile}
          readOnly={readOnly}
          featureMemory={featureMemory}
        />
      </div>
    </CatalogDrawer>
  );
}

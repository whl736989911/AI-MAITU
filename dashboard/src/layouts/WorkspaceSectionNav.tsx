import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { prefetchRoute } from "../routes/prefetch";
import type { NavItem } from "./sidebarNav";

/** Secondary destinations are the same authorized, preference-filtered items as the sidebar. */
export default function WorkspaceSectionNav({
  workspace,
  moduleKey,
}: {
  workspace: NavItem | undefined;
  moduleKey: string;
}) {
  const { t } = useTranslation();
  if (!workspace?.children?.length) return null;
  const activeKey =
    moduleKey === "admin-users" && workspace.key === "system"
      ? "identity"
      : moduleKey;
  return (
    <nav className="workspace-modules" aria-label={t(workspace.labelKey)}>
      <div className="workspace-modules-title">{t(workspace.labelKey)}</div>
      <div className="workspace-module-links">
        {workspace.children.map((item) => (
          <Link
            key={item.key}
            to={item.path}
            aria-current={item.key === activeKey ? "page" : undefined}
            onMouseEnter={() => prefetchRoute(item.path)}
          >
            <span className="workspace-module-icon">{item.icon}</span>
            <span>{t(item.labelKey)}</span>
          </Link>
        ))}
      </div>
    </nav>
  );
}

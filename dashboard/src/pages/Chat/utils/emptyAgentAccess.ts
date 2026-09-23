import type { OctopUser } from "../../../api/modules/auth";
import { userCan } from "../../../utils/permissions";

export type EmptyAgentAccess = "experts" | "features" | "both" | "none";

export function emptyAgentAccessFor(
  user: OctopUser | null | undefined,
): EmptyAgentAccess {
  const experts = userCan(user, "experts");
  const features = userCan(user, "features");
  if (experts && features) return "both";
  if (features) return "features";
  return experts ? "experts" : "none";
}

export function emptyAgentAccessKey(access: EmptyAgentAccess): string {
  return access[0].toUpperCase() + access.slice(1);
}

export function emptyAgentAccessPath(access: EmptyAgentAccess): string {
  return access === "features" ? "/features" : "/experts";
}

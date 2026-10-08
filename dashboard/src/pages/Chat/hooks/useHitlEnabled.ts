import { useEffect, useState } from "react";
import { agentChatApi } from "../../../api/modules/agentChat";

/** Global approval availability exposed by the authenticated chat welcome API. */
export function useHitlEnabled(
  agentId: string | null | undefined,
): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    if (!agentId) {
      setEnabled(null);
      return () => {
        active = false;
      };
    }

    setEnabled(null);
    void agentChatApi
      .welcome(agentId)
      .then((welcome) => {
        if (active) setEnabled(welcome.hitl_enabled ?? null);
      })
      .catch(() => {
        if (active) setEnabled(null);
      });

    return () => {
      active = false;
    };
  }, [agentId]);

  return enabled;
}

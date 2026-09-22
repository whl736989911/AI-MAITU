/**
 * The capability choices of a feature's own agent — the models, tools, skills
 * and subagents a definition may name — fetched only when a block that needs
 * them is opened.
 *
 * Loading them means starting the caller's agent, so this is not something the
 * settings drawer may do on open: renaming a feature must not pay for an agent
 * start. Each block that needs the choices carries its own loading state, its own
 * refusal and its own retry, and a refused load is *never* answered with empty
 * lists — "could not load" and "you have none" are different facts, and an empty
 * list would let an editor declare a scope no run could honour.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureCapabilities,
} from "../../../api/modules/features";
import { apiErrorMessage } from "../../../utils/apiError";

export interface FeatureChoicesState {
  /** ``null`` until the call has answered — never an empty stand-in. */
  choices: FeatureCapabilities | null;
  loading: boolean;
  /** Refusal text from the server, kept until the next attempt. */
  error: string | null;
  load: () => Promise<void>;
}

/**
 * ``open`` is what triggers the first fetch: put it on the disclosure that owns
 * the fields, so the choices cost an agent start only once somebody looks.
 *
 * ``featureId`` is the definition the block belongs to, and it is what makes the
 * lists describe the agent a *run* of it would use: ``resolve_capability``
 * intersects a declared scope with the run agent's own skills and subagents, so
 * an editor that read them off the caller's agent would offer entries the run
 * withholds. Omitted (a definition that does not exist yet) the server answers
 * for the caller's agent, which is exactly what such a run uses.
 */
export function useFeatureCapabilities(
  open: boolean,
  featureId?: string,
): FeatureChoicesState {
  const { t } = useTranslation();
  const [choices, setChoices] = useState<FeatureCapabilities | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setChoices(await featuresApi.getFeatureCapabilities(featureId));
    } catch (err) {
      setChoices(null);
      setError(
        apiErrorMessage(err, t("features.settingsCapabilityLoadFailed"), t),
      );
    } finally {
      setLoading(false);
    }
  }, [featureId, t]);

  useEffect(() => {
    if (open && choices === null && !loading && error === null) {
      void load();
    }
  }, [open, choices, loading, error, load]);

  return { choices, loading, error, load };
}

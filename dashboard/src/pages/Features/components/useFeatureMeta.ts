/**
 * Definition-format metadata for the settings editor: the unit keys, icon names
 * and output kinds a definition may use, plus the ids the app itself ships.
 *
 * Only a writer needs it, so the call is skipped for everyone else — every
 * surface that would act on it is hidden from them anyway.
 *
 * ``ready`` is the load-bearing half: everything that offers to write a
 * definition reads it first, because the stand-in carries an empty
 * ``bundled_ids`` and an unanswered list would offer exactly the definitions
 * this instance ships and the server refuses.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { featuresApi, type FeatureMeta } from "../../../api/modules/features";
import { useAsyncResource } from "../../../hooks/useAsyncResource";

/**
 * Stand-in until the call answers: the output kinds are fixed by the definition
 * format itself, while the rest is simply unknown — the pickers then offer
 * nothing beyond what the definition already holds.
 */
const EMPTY_FEATURE_META: FeatureMeta = {
  units: [],
  icons: [],
  output_kinds: ["markdown", "json", "text"],
  bundled_ids: [],
};

export interface FeatureMetaState {
  meta: FeatureMeta;
  /**
   * True once the server has answered. Until then a writer's entry points stay
   * hidden: ``bundled_ids`` is what says whether a feature may be edited at all,
   * and an unanswered list would offer to edit the ones the app ships.
   */
  ready: boolean;
}

export function useFeatureMeta(enabled: boolean): FeatureMetaState {
  const { t } = useTranslation();
  const [ready, setReady] = useState(false);
  const { data, loading } = useAsyncResource(
    EMPTY_FEATURE_META,
    async () => featuresApi.getFeatureMeta(),
    [],
    {
      enabled,
      errorFallback: t("features.settingsMetaFailed"),
      t,
      logLabel: "features.meta",
    },
  );

  /**
   * Raised once the *value* below is the server's, not when the call resolved:
   * a caller reads ``bundled_ids`` off this and decides whether to offer to write
   * a definition, and the stand-in holds an empty list — the one answer that
   * would offer exactly the definitions the app ships.
   */
  useEffect(() => {
    if (!enabled || loading || data === EMPTY_FEATURE_META) return;
    setReady(true);
  }, [data, enabled, loading]);

  // A caller who may not write never learns the format at all, and is not told a
  // set of shipped ids either: ``ready`` is what says the answer may be acted on.
  return { meta: data, ready: enabled && ready };
}

/**
 * Definition-format metadata for the settings editor: the unit keys, icon names
 * and output kinds a definition may use, plus the ids the app itself ships.
 *
 * Only a writer needs it, so the call is skipped for everyone else — the settings
 * entry points are hidden for them anyway.
 */

import { useState } from "react";
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
  const { data } = useAsyncResource(
    EMPTY_FEATURE_META,
    async () => {
      const meta = await featuresApi.getFeatureMeta();
      setReady(true);
      return meta;
    },
    [],
    {
      enabled,
      errorFallback: t("features.settingsMetaFailed"),
      t,
      logLabel: "features.meta",
    },
  );
  return { meta: data, ready };
}

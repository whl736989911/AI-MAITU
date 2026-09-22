/**
 * The write half of the definition editor: the form instance, its seeding, the
 * two gates the server would refuse anyway (a half-filled bilingual pair, a
 * ``validate`` gate no artifact could satisfy), the create/update call and the
 * delete.
 *
 * It is a hook rather than a component because the two surfaces that write a
 * definition are wrapped differently on purpose — the create drawer scrolls
 * inside a drawer, the settings page fills a route — while the write itself has
 * to stay byte-identical between them. Only the container differs.
 */

import { useCallback, useEffect, useState } from "react";
import { Form } from "antd";
import type { FormInstance } from "antd/es/form";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type Feature,
  type FeatureMeta,
} from "../../../api/modules/features";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "@/utils/antdMessage";
import {
  emptyFormValues,
  featureToFormValues,
  formValuesToDefinition,
  incompleteCopyFields,
  validateGateProblems,
  type FeatureFormValues,
} from "./featureSettings";

export interface FeatureSettingsFormOptions {
  /** Definition being edited; ``null`` creates a new one. */
  feature: Feature | null;
  meta: FeatureMeta;
  /**
   * Re-seed whenever this becomes true: a cancelled edit must not leak into the
   * next open. A route-mounted surface leaves it at its default.
   */
  open?: boolean;
  /** Called with the written id once a create/update succeeds. */
  onSaved: (featureId: string, created: boolean) => void;
  /** Called with the id once a delete succeeds. */
  onDeleted?: (featureId: string) => void;
}

export interface FeatureSettingsFormState {
  form: FormInstance<FeatureFormValues>;
  saving: boolean;
  deleting: boolean;
  /** Refusal text from the server, kept until the next attempt. */
  saveError: string | null;
  /** Submit the whole store — see the call sites for why it is not ``onFinish``'s values. */
  submit: (values: FeatureFormValues) => Promise<void>;
  remove: () => Promise<void>;
}

export function useFeatureSettingsForm({
  feature,
  meta,
  open = true,
  onSaved,
  onDeleted,
}: FeatureSettingsFormOptions): FeatureSettingsFormState {
  const { t } = useTranslation();
  const [form] = Form.useForm<FeatureFormValues>();
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  /** Refusal text from the server, kept until the next attempt. */
  const [saveError, setSaveError] = useState<string | null>(null);

  const creating = feature === null;

  // Re-seed on every open, and on every definition that arrives afterwards: a
  // cancelled edit must not leak into the next one.
  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue(
      feature ? featureToFormValues(feature) : emptyFormValues(meta),
    );
    setSaveError(null);
  }, [open, feature, meta, form]);

  const submit = useCallback(
    async (values: FeatureFormValues) => {
      // Both gates mirror ``validate_manifest``: a half-filled bilingual pair and
      // an empty field list are refused by the server, so they are named here.
      const incomplete = incompleteCopyFields(values.fields);
      if (incomplete.length > 0) {
        setSaveError(
          t("features.settingsFieldCopyIncomplete", {
            fields: incomplete.join(", "),
          }),
        );
        return;
      }
      // A ``validate`` gate that no object artifact could ever satisfy is refused
      // by the server at write time, so it is named here first. Nothing else about
      // a step blocks the save: ``mode`` and ``agent_role`` say how the step runs,
      // and the write carries both through exactly as the author left them.
      const badGates = validateGateProblems(values.steps ?? []);
      if (badGates.length > 0) {
        setSaveError(
          t("features.settingsStepValidateSchemaRefused", {
            steps: badGates.join(", "),
          }),
        );
        return;
      }
      setSaving(true);
      setSaveError(null);
      try {
        const body = formValuesToDefinition(values, feature);
        const written = feature
          ? await featuresApi.updateFeature(feature.id, body)
          : await featuresApi.createFeature(body);
        message.success(
          feature ? t("features.settingsSaved") : t("features.settingsCreated"),
        );
        onSaved(written.feature_id, creating);
      } catch (err) {
        setSaveError(apiErrorMessage(err, t("features.settingsSaveFailed"), t));
      } finally {
        setSaving(false);
      }
    },
    [creating, feature, onSaved, t],
  );

  const remove = useCallback(async () => {
    if (!feature) return;
    setDeleting(true);
    try {
      await featuresApi.deleteFeature(feature.id);
      message.success(t("features.settingsDeleted"));
      onDeleted?.(feature.id);
    } catch (err) {
      message.error(apiErrorMessage(err, t("common.deleteFailed"), t));
    } finally {
      setDeleting(false);
    }
  }, [feature, onDeleted, t]);

  return { form, saving, deleting, saveError, submit, remove };
}

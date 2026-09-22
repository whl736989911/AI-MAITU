/**
 * New feature — the light drawer. It holds the definition essentials and
 * nothing else: the capability block and the step skeleton are left to the
 * settings page the author lands on after the definition exists.
 *
 * That split is deliberate. Both of those blocks fetch their choices from the
 * caller's *agent*, and a definition nobody has saved yet must not cost an
 * agent start — which is also why the block is absent here rather than merely
 * collapsed.
 *
 * The full editor is one of the new feature's own tabs (``/features/:id``).
 */

import { Drawer, Form } from "antd";
import { useTranslation } from "react-i18next";
import type { FeatureMeta } from "../../../api/modules/features";
import {
  FeatureBasicsSection,
  FeatureInputFieldsSection,
  FeaturePermissionsSection,
  FeatureOutputSection,
  FeaturePromptSection,
  FeatureUnitSection,
  SaveErrorAlert,
  SettingsFooter,
} from "./FeatureSettingsSections";
import { useFeatureSettingsForm } from "./useFeatureSettingsForm";
import type { FeatureFormValues } from "./featureSettings";
import styles from "../index.module.less";

export interface FeatureCreateDrawerProps {
  open: boolean;
  meta: FeatureMeta;
  onClose: () => void;
  /** Called with the written id once the definition exists. */
  onCreated: (featureId: string) => void;
}

export default function FeatureCreateDrawer({
  open,
  meta,
  onClose,
  onCreated,
}: FeatureCreateDrawerProps) {
  const { t } = useTranslation();
  const { form, saving, saveError, submit } = useFeatureSettingsForm({
    feature: null,
    meta,
    open,
    onSaved: (featureId) => onCreated(featureId),
  });

  return (
    <Drawer
      width="min(880px, 96vw)"
      placement="right"
      title={t("features.settingsNewTitle")}
      open={open}
      onClose={onClose}
      destroyOnHidden
      styles={{
        body: {
          padding: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          height: "calc(100vh - 55px)",
        },
      }}
    >
      <div className={styles.shell}>
        <div className={styles.settingsBody}>
          <Form
            form={form}
            layout="vertical"
            className={styles.settingsForm}
            onFinish={() =>
              // The whole store, not just the registered fields — the same call
              // the settings page makes, so a save never drops a value the form
              // holds but has not mounted.
              void submit(form.getFieldsValue(true) as FeatureFormValues)
            }
          >
            <SaveErrorAlert error={saveError} />

            <FeatureBasicsSection creating meta={meta} />
            <FeatureInputFieldsSection />
            <FeaturePromptSection />
            <FeatureOutputSection meta={meta} />
            <FeatureUnitSection meta={meta} />
            <FeaturePermissionsSection meta={meta} />
          </Form>
        </div>

        <SettingsFooter
          saving={saving}
          onCancel={onClose}
          onSubmit={() => form.submit()}
        />
      </div>
    </Drawer>
  );
}

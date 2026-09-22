/**
 * The definition editor — the whole ``feature.json`` across two tabs, and the
 * one place a definition is written from.
 *
 * The page above owns the shell, the tab row and the load; this owns the form,
 * its blocks and the commit row. Only ever mounted for a definition the caller
 * may write, so every gate below is a property of the surface as a whole and not
 * of a block.
 *
 * A tab that has been opened stays mounted and is hidden with ``display``
 * instead, so switching between the definition and the steps neither loses what
 * was typed nor unregisters the fields a save has to carry.
 */

import { Form } from "antd";
import type { Feature, FeatureMeta } from "../../../api/modules/features";
import { FeatureCapabilitySection } from "./FeatureCapabilityFields";
import { FeatureStepsSection } from "./FeatureStepsFields";
import {
  FeatureBasicsSection,
  FeatureDangerSection,
  FeatureInputFieldsSection,
  FeatureOutputSection,
  FeaturePermissionsSection,
  FeaturePromptSection,
  FeatureUnitSection,
  SaveErrorAlert,
  SettingsFooter,
} from "./FeatureSettingsSections";
import { useFeatureSettingsForm } from "./useFeatureSettingsForm";
import type { FeatureFormValues } from "./featureSettings";
import styles from "../index.module.less";

/** The two tabs a definition is edited in. */
export type FeatureSettingsTab = "definition" | "steps";

export interface FeatureDefinitionEditorProps {
  feature: Feature;
  meta: FeatureMeta;
  /** The tab on screen; the other stays mounted once opened. */
  activeTab: FeatureSettingsTab;
  /** Whether a tab has ever been opened. */
  isMounted: (tab: FeatureSettingsTab) => boolean;
  /** Called with the written id once a save lands — the header follows it. */
  onSaved: (featureId: string, created: boolean) => void;
  /** Called with the id once a delete lands: the definition is gone. */
  onDeleted: (featureId: string) => void;
  /**
   * Leave without writing. The page owns it because leaving has to discard: the
   * editor stays mounted while the feature's other surfaces are on screen, so
   * nothing else would put the server's own copy back in the form.
   */
  onCancel: () => void;
}

export default function FeatureDefinitionEditor({
  feature,
  meta,
  activeTab,
  isMounted,
  onSaved,
  onDeleted,
  onCancel,
}: FeatureDefinitionEditorProps) {
  const { form, saving, deleting, saveError, submit, remove } =
    useFeatureSettingsForm({ feature, meta, onSaved, onDeleted });

  return (
    <>
      <Form
        form={form}
        layout="vertical"
        className={`${styles.settingsForm} ${styles.settingsScroll}`}
        onFinish={() =>
          // The whole store, not just the registered fields. Two blocks are
          // collapsed by default (a collapsed antd ``Collapse`` does not mount
          // its content, so its fields are unregistered), and a tab nobody has
          // opened yet has never mounted its panel at all. Reading the store is
          // what makes one commit write the whole definition instead of
          // silently dropping the parts that were never on screen.
          void submit(form.getFieldsValue(true) as FeatureFormValues)
        }
      >
        <SaveErrorAlert error={saveError} />

        {isMounted("definition") && (
          <div
            className={styles.settingsPanel}
            style={{
              display: activeTab === "definition" ? "flex" : "none",
            }}
            aria-hidden={activeTab !== "definition"}
          >
            <FeatureBasicsSection creating={false} meta={meta} />
            <FeatureInputFieldsSection />
            <FeaturePromptSection />
            {/* The scopes below are intersected with the agent a *run* uses, so
                the choices have to be that agent's — see the hook. */}
            <FeatureCapabilitySection featureId={feature.id} />
            <FeatureOutputSection meta={meta} />
            <FeatureUnitSection meta={meta} />
            <FeaturePermissionsSection meta={meta} />
            <FeatureDangerSection
              feature={feature}
              deleting={deleting}
              onDelete={() => void remove()}
            />
          </div>
        )}

        {isMounted("steps") && (
          <div
            className={styles.settingsPanel}
            style={{
              display: activeTab === "steps" ? "flex" : "none",
            }}
            aria-hidden={activeTab !== "steps"}
          >
            <FeatureStepsSection />
          </div>
        )}
      </Form>

      {/* The commit row belongs to the editor, not to the page: it is how a
          definition is written, and it is only there while one is on screen. */}
      <SettingsFooter
        className={styles.settingsFooter}
        saving={saving}
        onCancel={onCancel}
        onSubmit={() => form.submit()}
      />
    </>
  );
}

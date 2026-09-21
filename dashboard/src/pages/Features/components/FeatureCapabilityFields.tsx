/**
 * The capability block of a feature definition: model, sampling knobs, tools,
 * skills, subagents, connectors and knowledge bases.
 *
 * Design 5.1/5.2 decides what belongs here. The *configuration* half — model,
 * tools, skills, subagents — is authored once and identical for every caller,
 * so it comes from the definition. The *data* half — connectors, knowledge
 * bases — is resolved through whoever runs the feature, so the editor offers
 * the caller's own lists and the server narrows them again at run time.
 *
 * Two rules shape the surface:
 *   - The choices that need an agent (skills, subagents) are fetched only once
 *     this block is opened. Loading them means starting the caller's agent, and
 *     that must not be a precondition for opening the settings drawer — so
 *     ``_meta`` stays agent-free and this block carries its own loading state,
 *     its own refusal and its own retry.
 *   - A field left as inherited is *omitted* from ``feature.json``; a field
 *     declared as none is written as an empty list. The two are different
 *     scopes, and the inherit switch is what keeps them apart.
 *
 * The knobs and the two data lists are rendered by the same components the
 * expert drawers use (``AgentAdvancedConfigFields``,
 * ``ExpertComposerDefaultsFields``); only the model / tool / skill / subagent
 * pickers are new, because an expert has no equivalent — its skills and
 * subagents are files managed through their own drawers.
 */

import { useState } from "react";
import { Alert, Button, Checkbox, Collapse, Form, Select, Spin } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AgentAdvancedConfigFields } from "../../../components/AgentAdvancedConfigFields";
import ExpertComposerDefaultsFields from "../../Experts/components/ExpertComposerDefaultsFields";
import { MODEL_AUTO_VALUE } from "../../../utils/modelOptions";
import { useFeatureCapabilities } from "./useFeatureCapabilities";
import styles from "../index.module.less";

/** One declared list: ``undefined`` inherits, an array is the scope itself. */
type ScopeValue = string[] | undefined;

/** The four lists that may be inherited or declared — one switch each. */
const SCOPE_FIELDS = [
  { name: "skills", labelKey: "features.settingsCapabilitySkills" },
  { name: "subagents", labelKey: "features.settingsCapabilitySubagents" },
] as const;

/**
 * One declared-vs-inherited list, using the same switch the reused connector /
 * knowledge-base fields render (``ExpertComposerDefaultsFields``'s
 * ``ScopeField``) so every scope in the drawer reads the same way.
 *
 * Also used by the step block, whose tool whitelist follows the same convention:
 * a list is a scope the author declared, and leaving it inherited keeps whatever
 * the run would otherwise have.
 */
export function DeclaredListField({
  name,
  labelKey,
  hintKey,
  options,
  loading,
  inheritLabel,
}: {
  /** Form path — a plain name, or a nested path inside a repeated card. */
  name: string | (string | number)[];
  labelKey: string;
  hintKey: string;
  options: { value: string; label: string }[];
  loading: boolean;
  /** Defaults to the capability block's wording. */
  inheritLabel?: string;
}) {
  const { t } = useTranslation();
  const form = Form.useFormInstance<Record<string, unknown>>();
  const value = Form.useWatch<ScopeValue>(name, form);
  const inherited = value === undefined;

  return (
    <Form.Item label={t(labelKey)} extra={t(hintKey)}>
      <div className={styles.scopeRow}>
        <Form.Item name={name} noStyle>
          <Select
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            loading={loading}
            disabled={inherited}
            options={options}
            // The two states mean opposite things, so they cannot share a
            // placeholder: "inherit" while an empty list is declared tells the
            // author their scope is the caller's when the run will use none.
            placeholder={
              inherited
                ? t("features.settingsCapabilityScopePlaceholder")
                : t("features.settingsCapabilityScopeNone")
            }
          />
        </Form.Item>
        <Checkbox
          checked={inherited}
          onChange={(event) =>
            form.setFieldValue(name, event.target.checked ? undefined : [])
          }
        >
          {inheritLabel ?? t("features.settingsCapabilityInherit")}
        </Checkbox>
      </div>
    </Form.Item>
  );
}

export interface FeatureCapabilityFieldsProps {
  /** The capability block is only fetched once this is true (block expanded). */
  open: boolean;
}

export default function FeatureCapabilityFields({
  open,
}: FeatureCapabilityFieldsProps) {
  const { t } = useTranslation();
  const { choices, loading, error, load } = useFeatureCapabilities(open);

  if (loading) {
    return (
      <div className={styles.capabilityStatus}>
        <Spin size="small" />
        <span>{t("features.settingsCapabilityLoading")}</span>
      </div>
    );
  }

  if (error !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("features.settingsCapabilityLoadFailed")}
        description={error}
        action={
          <Button size="small" icon={<RefreshCw size={13} />} onClick={() => void load()}>
            {t("features.settingsCapabilityRetry")}
          </Button>
        }
      />
    );
  }

  if (choices === null) {
    return null;
  }

  return (
    <div className={styles.blockGrid}>
      <Form.Item
        name="agentModel"
        label={t("features.settingsCapabilityModel")}
        tooltip={t("features.settingsCapabilityModelHint")}
      >
        <Select
          showSearch
          optionFilterProp="label"
          placeholder={t("features.settingsCapabilityModelPlaceholder")}
          options={[
            {
              value: MODEL_AUTO_VALUE,
              label: t("features.settingsCapabilityModelInherit"),
            },
            ...choices.models.map((model) => ({
              value: model.ref,
              label: model.label,
            })),
          ]}
        />
      </Form.Item>

      {/* The expert drawers' own runtime knobs, shared as one component. */}
      <AgentAdvancedConfigFields />

      <div className={styles.blockWide}>
        <Form.Item
          name="toolsDisabled"
          label={t("features.settingsCapabilityTools")}
          extra={t("features.settingsCapabilityToolsHint")}
        >
          <Select
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t("features.settingsCapabilityToolsPlaceholder")}
            options={choices.tools.map((tool) => ({
              value: tool.name,
              label: `${tool.name} · ${tool.category}`,
            }))}
          />
        </Form.Item>
      </div>

      <div className={styles.blockWide}>
        {SCOPE_FIELDS.map((field) => (
          <DeclaredListField
            key={field.name}
            name={field.name}
            labelKey={field.labelKey}
            hintKey={
              field.name === "skills"
                ? "features.settingsCapabilitySkillsHint"
                : "features.settingsCapabilitySubagentsHint"
            }
            loading={false}
            options={choices[field.name].map((name) => ({
              value: name,
              label: name,
            }))}
          />
        ))}
      </div>

      {/* The caller's own data lists, through the expert composer's fields. */}
      <div className={styles.blockWide}>
        <ExpertComposerDefaultsFields
          knowledgeBases={choices.knowledge_bases}
          connectors={choices.mcp_servers.map((choice) => ({
            value: choice.name,
            label: choice.label,
          }))}
          inheritable
          inheritLabel={t("features.settingsCapabilityInherit")}
        />
      </div>
    </div>
  );
}

/**
 * The capability block as a collapsible settings block of the drawer.
 *
 * Collapsed by default, and the fields it holds are fetched on the first
 * expand: the choices that need an agent cost an agent start, and opening the
 * drawer to rename a feature must not pay that.
 */
export function FeatureCapabilitySection() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <section className={styles.settingsBlock}>
      <Collapse
        ghost
        className={styles.capabilityCollapse}
        onChange={(keys) =>
          setOpen(Array.isArray(keys) ? keys.length > 0 : Boolean(keys))
        }
        items={[
          {
            key: "capability",
            label: t("features.settingsSectionCapability"),
            children: (
              <>
                <div className={styles.blockHint}>
                  {t("features.settingsSectionCapabilityHint")}
                </div>
                <FeatureCapabilityFields open={open} />
              </>
            ),
          },
        ]}
      />
    </section>
  );
}

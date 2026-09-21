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

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Checkbox, Collapse, Form, Select, Spin } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureCapabilities,
} from "../../../api/modules/features";
import { AgentAdvancedConfigFields } from "../../../components/AgentAdvancedConfigFields";
import { apiErrorMessage } from "../../../utils/apiError";
import ExpertComposerDefaultsFields from "../../Experts/components/ExpertComposerDefaultsFields";
import { MODEL_AUTO_VALUE } from "../../../utils/modelOptions";
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
 * ``ScopeField``) so the whole block reads the same way.
 */
function DeclaredListField({
  name,
  labelKey,
  hintKey,
  options,
  loading,
}: {
  name: string;
  labelKey: string;
  hintKey: string;
  options: { value: string; label: string }[];
  loading: boolean;
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
            placeholder={t("features.settingsCapabilityScopePlaceholder")}
          />
        </Form.Item>
        <Checkbox
          checked={inherited}
          onChange={(event) =>
            form.setFieldValue(name, event.target.checked ? undefined : [])
          }
        >
          {t("features.settingsCapabilityInherit")}
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
  const [choices, setChoices] = useState<FeatureCapabilities | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setChoices(await featuresApi.getFeatureCapabilities());
    } catch (err) {
      // Reported, never answered with empty lists: "could not load" and "you
      // have none" are different facts, and empty lists would let this editor
      // declare a scope no run could honour.
      setChoices(null);
      setError(
        apiErrorMessage(err, t("features.settingsCapabilityLoadFailed"), t),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (open && choices === null && !loading && error === null) {
      void load();
    }
  }, [open, choices, loading, error, load]);

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

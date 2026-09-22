/**
 * The blocks of the definition editor — one component per titled section, plus
 * the two pieces of chrome the surfaces that write ``feature.json`` share.
 *
 * Two surfaces write a definition and both are assembled from these blocks:
 *   - the create drawer, which holds the definition essentials only (a new
 *     definition must not cost an agent start), and
 *   - the settings page at ``/features/:id/settings``, which holds every block.
 *
 * Nothing here owns the form or the write: the caller supplies the antd form
 * instance, the save/delete handlers and the order the blocks appear in, so a
 * section can be moved between surfaces without touching a field, a rule or a
 * validation. The gate stays at the call site too — a bundled definition is
 * never offered the danger zone, because the server would refuse it anyway.
 */

import type { ReactNode } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Checkbox,
  ColorPicker,
  Form,
  Input,
  Popconfirm,
  Select,
  Tooltip,
} from "antd";
import type { AggregationColor } from "antd/es/color-picker/color";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  type Feature,
  type FeatureMeta,
  type FeatureOutputKind,
} from "../../../api/modules/features";
import { BRAND } from "../../../brand.generated";
import SchemaNodeEditor from "./SchemaNodeEditor";
import {
  ALL_UNITS_KEY,
  emptyFieldRow,
  FEATURE_ROLE_OPTIONS,
  isValidFeatureId,
  type FeatureFieldRow,
} from "./featureSettings";
import styles from "../index.module.less";

/** ``output.kind`` is a fixed vocabulary; the picker labels it in the UI language. */
const OUTPUT_KIND_LABEL_KEYS: Record<FeatureOutputKind, string> = {
  markdown: "features.settingsOutputMarkdown",
  json: "features.settingsOutputJson",
  text: "features.settingsOutputText",
};

/** One place for "must be text": whitespace-only input is not text. */
function requiredRule(message: string) {
  return { required: true, whitespace: true, message };
}

/** One titled block of the settings form. */
export function SettingsSection({
  titleKey,
  hintKey,
  children,
}: {
  titleKey: string;
  hintKey?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.settingsBlock}>
      <div className={styles.blockHead}>
        <div className={styles.blockTitle}>{t(titleKey)}</div>
        {hintKey && <div className={styles.blockHint}>{t(hintKey)}</div>}
      </div>
      {children}
    </section>
  );
}

/**
 * Accent colour of the card. Antd's picker hands over a colour *object*, while a
 * definition stores a hex string (``null`` = the theme accent), so this keeps the
 * form value a string.
 */
function ColorField({
  value,
  onChange,
}: {
  value?: string;
  onChange?: (value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.colorField}>
      <ColorPicker
        value={value || BRAND.color.accent}
        disabledAlpha
        onChangeComplete={(color: AggregationColor) =>
          onChange?.(color.toHexString())
        }
      />
      {value ? (
        <>
          <span className={styles.colorValue}>{value.toUpperCase()}</span>
          <Button size="small" type="text" onClick={() => onChange?.("")}>
            {t("features.settingsColorClear")}
          </Button>
        </>
      ) : (
        <span className={styles.fieldHint}>
          {t("features.settingsColorDefault")}
        </span>
      )}
    </div>
  );
}

/**
 * A refused write stays on screen next to the fields that need fixing, and the
 * server's own words are what it shows.
 */
export function SaveErrorAlert({ error }: { error: string | null }) {
  const { t } = useTranslation();
  if (!error) return null;
  return (
    <Alert
      type="error"
      showIcon
      message={t("features.settingsSaveFailed")}
      description={error}
    />
  );
}

/** The commit row: a long definition never pushes Save out of reach. */
export function SettingsFooter({
  saving,
  onCancel,
  onSubmit,
  className,
}: {
  saving: boolean;
  onCancel: () => void;
  onSubmit: () => void;
  /** The drawer's bar by default; the settings page lays the same row out itself. */
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className={className ?? styles.footer}>
      <Button onClick={onCancel}>{t("common.cancel")}</Button>
      <Button type="primary" loading={saving} onClick={onSubmit}>
        {t("common.save")}
      </Button>
    </div>
  );
}

/** Name, icon and copy — the catalog card, and the id the definition lives under. */
export function FeatureBasicsSection({
  creating,
  meta,
}: {
  /** A new definition may still choose its id; an existing one may not. */
  creating: boolean;
  meta: FeatureMeta;
}) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionBasics"
      hintKey="features.settingsSectionBasicsHint"
    >
      <div className={styles.blockGrid}>
        <Form.Item
          name="id"
          label={t("features.settingsId")}
          tooltip={t("features.settingsIdHint")}
          rules={
            creating
              ? [
                  requiredRule(t("features.settingsIdRequired")),
                  {
                    validator: async (_, value: string) => {
                      if (value && !isValidFeatureId(value)) {
                        throw new Error(t("features.settingsIdInvalid"));
                      }
                    },
                  },
                ]
              : []
          }
        >
          <Input
            disabled={!creating}
            placeholder="quote-draft"
            autoComplete="off"
          />
        </Form.Item>

        <Form.Item
          name="iconName"
          label={t("features.settingsIcon")}
          tooltip={t("features.settingsIconHint")}
        >
          <Select
            showSearch
            placeholder={t("features.settingsIconPlaceholder")}
            options={meta.icons.map((name) => ({
              value: name,
              label: name,
            }))}
          />
        </Form.Item>

        <Form.Item
          name="labelZh"
          label={t("features.settingsLabelZh")}
          rules={[requiredRule(t("features.settingsLabelRequired"))]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="labelEn"
          label={t("features.settingsLabelEn")}
          rules={[requiredRule(t("features.settingsLabelRequired"))]}
        >
          <Input autoComplete="off" />
        </Form.Item>

        <Form.Item
          name="descriptionZh"
          label={t("features.settingsDescriptionZh")}
          rules={[requiredRule(t("features.settingsDescriptionRequired"))]}
        >
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
        </Form.Item>
        <Form.Item
          name="descriptionEn"
          label={t("features.settingsDescriptionEn")}
          rules={[requiredRule(t("features.settingsDescriptionRequired"))]}
        >
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
        </Form.Item>

        <Form.Item
          name="color"
          label={t("features.settingsColor")}
          className={styles.blockWide}
        >
          <ColorField />
        </Form.Item>
      </div>
    </SettingsSection>
  );
}

/** One row per field of the run form; the order here is the order it renders in. */
export function FeatureInputFieldsSection() {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionFields"
      hintKey="features.settingsSectionFieldsHint"
    >
      <Form.List
        name="fields"
        rules={[
          {
            validator: async (_, value: FeatureFieldRow[] | undefined) => {
              // ``input_schema.properties`` has to hold at least one
              // field; an empty form is not a definition.
              if (!value || value.length === 0) {
                throw new Error(t("features.settingsFieldsRequired"));
              }
            },
          },
        ]}
      >
        {(rows, { add, remove, move }) => (
          <div className={styles.fieldList}>
            {rows.length === 0 && (
              <div className={styles.fieldEmpty}>
                {t("features.settingsFieldsEmpty")}
              </div>
            )}
            {rows.map((field, index) => (
              <article className={styles.fieldCard} key={field.key}>
                <div className={styles.fieldCardHead}>
                  <Form.Item
                    name={[field.name, "name"]}
                    className={styles.fieldNameItem}
                    rules={[
                      requiredRule(t("features.settingsFieldNameRequired")),
                      ({ getFieldValue }) => ({
                        validator: async (_, value: string) => {
                          const name = (value ?? "").trim();
                          if (!name) return;
                          const used: FeatureFieldRow[] =
                            getFieldValue("fields") ?? [];
                          // A name is the key the prompt receives: two rows
                          // sharing one means ``properties`` keeps only the
                          // last of them and the first field disappears.
                          const clash = used.filter(
                            (item) => item.name.trim() === name,
                          );
                          if (clash.length > 1) {
                            throw new Error(
                              t("features.settingsFieldNameDuplicate"),
                            );
                          }
                        },
                      }),
                    ]}
                  >
                    <Input
                      placeholder={t("features.settingsFieldNamePlaceholder")}
                      autoComplete="off"
                    />
                  </Form.Item>

                  <Form.Item
                    name={[field.name, "required"]}
                    valuePropName="checked"
                    className={styles.fieldRequiredItem}
                  >
                    <Checkbox>{t("features.settingsFieldRequired")}</Checkbox>
                  </Form.Item>

                  <div className={styles.fieldCardTools}>
                    <Tooltip title={t("features.settingsFieldMoveUp")}>
                      <Button
                        type="text"
                        size="small"
                        aria-label={t("features.settingsFieldMoveUp")}
                        disabled={index === 0}
                        icon={<ArrowUp size={13} />}
                        onClick={() => move(index, index - 1)}
                      />
                    </Tooltip>
                    <Tooltip title={t("features.settingsFieldMoveDown")}>
                      <Button
                        type="text"
                        size="small"
                        aria-label={t("features.settingsFieldMoveDown")}
                        disabled={index === rows.length - 1}
                        icon={<ArrowDown size={13} />}
                        onClick={() => move(index, index + 1)}
                      />
                    </Tooltip>
                    <Tooltip title={t("features.settingsFieldRemove")}>
                      <Button
                        type="text"
                        size="small"
                        danger
                        aria-label={t("features.settingsFieldRemove")}
                        icon={<Trash2 size={13} />}
                        onClick={() => remove(field.name)}
                      />
                    </Tooltip>
                  </div>
                </div>

                <Form.Item name={[field.name, "schema"]} noStyle>
                  <SchemaNodeEditor depth={0} />
                </Form.Item>
              </article>
            ))}

            <Button
              className={styles.addFieldButton}
              icon={<Plus size={14} />}
              onClick={() => add(emptyFieldRow())}
            >
              {t("features.settingsFieldsAdd")}
            </Button>
          </div>
        )}
      </Form.List>
    </SettingsSection>
  );
}

/** ``PROMPT.md`` and the template every run is filled into. */
export function FeaturePromptSection() {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionPrompt"
      hintKey="features.settingsSectionPromptHint"
    >
      <Form.Item
        name="systemPrompt"
        label={t("features.settingsSystemPrompt")}
        extra={t("features.settingsSystemPromptHint")}
      >
        <Input.TextArea
          className={styles.promptEditor}
          placeholder={t("features.settingsSystemPromptPlaceholder")}
          autoSize={{ minRows: 8, maxRows: 24 }}
        />
      </Form.Item>
      <Form.Item
        name="userTemplate"
        label={t("features.settingsUserTemplate")}
        extra={
          <div className={styles.templatePlaceholders}>
            {t("features.settingsUserTemplateHint")} <code>{"{{inputs}}"}</code>
            <code>{"{{inputs_json}}"}</code>
          </div>
        }
        rules={[requiredRule(t("features.settingsUserTemplateRequired"))]}
      >
        <Input.TextArea
          className={styles.promptEditor}
          autoSize={{ minRows: 6, maxRows: 20 }}
        />
      </Form.Item>
    </SettingsSection>
  );
}

/** How the generated text is presented to the user. */
export function FeatureOutputSection({ meta }: { meta: FeatureMeta }) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionOutput"
      hintKey="features.settingsSectionOutputHint"
    >
      <Form.Item name="outputKind" label={t("features.settingsOutputKind")}>
        <Select
          options={meta.output_kinds.map((kind) => ({
            value: kind,
            label: t(OUTPUT_KIND_LABEL_KEYS[kind]),
          }))}
        />
      </Form.Item>
    </SettingsSection>
  );
}

/** Which group of the catalog this feature is filed under. */
export function FeatureUnitSection({ meta }: { meta: FeatureMeta }) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionUnit"
      hintKey="features.settingsSectionUnitHint"
    >
      <Form.Item
        name="unit"
        label={t("features.settingsUnit")}
        rules={[requiredRule(t("features.settingsUnitRequired"))]}
      >
        <AutoComplete
          options={meta.units.map((key) => ({ value: key }))}
          placeholder={t("features.settingsUnitPlaceholder")}
        />
      </Form.Item>
    </SettingsSection>
  );
}

/** Access rules, stored with the definition; empty declares no restriction. */
export function FeaturePermissionsSection({ meta }: { meta: FeatureMeta }) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      titleKey="features.settingsSectionPermissions"
      hintKey="features.settingsPermissionsHint"
    >
      <Form.Item name="allowUnits" label={t("features.settingsAllowUnits")}>
        <Select
          mode="tags"
          tokenSeparators={[","]}
          placeholder={t("features.settingsPermissionsPlaceholder")}
          options={[
            {
              value: ALL_UNITS_KEY,
              label: t("features.settingsAllUnits"),
            },
            ...meta.units.map((key) => ({ value: key, label: key })),
          ]}
        />
      </Form.Item>
      <Form.Item name="allowRoles" label={t("features.settingsAllowRoles")}>
        <Select
          mode="tags"
          tokenSeparators={[","]}
          placeholder={t("features.settingsPermissionsPlaceholder")}
          options={[
            {
              value: ALL_UNITS_KEY,
              label: t("features.settingsAllRoles"),
            },
            ...FEATURE_ROLE_OPTIONS.map((role) => ({
              value: role,
              label: role,
            })),
          ]}
        />
      </Form.Item>
    </SettingsSection>
  );
}

/**
 * Deleting a definition. ``feature`` is what is being deleted, and the caller
 * decides whether the block appears at all — a bundled definition never gets it.
 */
export function FeatureDangerSection({
  feature,
  deleting,
  onDelete,
}: {
  feature: Feature;
  deleting: boolean;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  return (
    <SettingsSection titleKey="features.settingsDangerZone">
      <div className={styles.blockHint}>{t("features.settingsDeleteHint")}</div>
      <Popconfirm
        title={t("features.settingsDeleteConfirmTitle")}
        description={t("features.settingsDeleteConfirmDesc", {
          name: feature.id,
        })}
        okText={t("common.delete")}
        okButtonProps={{ danger: true }}
        cancelText={t("common.cancel")}
        onConfirm={onDelete}
      >
        <Button danger icon={<Trash2 size={14} />} loading={deleting}>
          {t("features.settingsDelete")}
        </Button>
      </Popconfirm>
    </SettingsSection>
  );
}

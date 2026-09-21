/**
 * Feature settings — one drawer holding the whole definition: metadata, the
 * input-form field editor, the prompts, the output shape, the owning unit and
 * the access rules.
 *
 * Nothing here is typed as JSON. ``input_schema`` is assembled from field rows,
 * each row owning its schema node (see ``SchemaNodeEditor``), and ``PROMPT.md``
 * is an ordinary text area. The prompts travel as *content*: the server owns the
 * file name.
 *
 * Two rules the surface obeys:
 *   - Only an administrator may write, and a bundled feature belongs to the app
 *     rather than to the workspace — both cases hide the controls instead of
 *     offering a button that can only be refused.
 *   - A refused write stays on screen: a definition the server rejects is
 *     reported in the drawer, next to the fields that need fixing.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Checkbox,
  ColorPicker,
  Drawer,
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
  featuresApi,
  type Feature,
  type FeatureMeta,
  type FeatureOutputKind,
} from "../../../api/modules/features";
import { BRAND } from "../../../brand.generated";
import { apiErrorMessage } from "../../../utils/apiError";
import { message } from "@/utils/antdMessage";
import SchemaNodeEditor from "./SchemaNodeEditor";
import { FeatureCapabilitySection } from "./FeatureCapabilityFields";
import {
  ALL_UNITS_KEY,
  emptyFieldRow,
  emptyFormValues,
  FEATURE_ROLE_OPTIONS,
  featureToFormValues,
  formValuesToDefinition,
  incompleteCopyFields,
  isValidFeatureId,
  type FeatureFieldRow,
  type FeatureFormValues,
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
function SettingsSection({
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

export interface FeatureSettingsDrawerProps {
  open: boolean;
  /** Definition being edited; ``null`` creates a new one. */
  feature: Feature | null;
  meta: FeatureMeta;
  onClose: () => void;
  /** Called with the written id once a create/update succeeds. */
  onSaved: (featureId: string, created: boolean) => void;
  /** Called with the id once a delete succeeds. */
  onDeleted?: (featureId: string) => void;
}

export default function FeatureSettingsDrawer({
  open,
  feature,
  meta,
  onClose,
  onSaved,
  onDeleted,
}: FeatureSettingsDrawerProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<FeatureFormValues>();
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  /** Refusal text from the server, kept until the next attempt. */
  const [saveError, setSaveError] = useState<string | null>(null);

  const creating = feature === null;
  // Bundled definitions ship with the app: the server refuses to write them, and
  // the drawer does not offer what it cannot do.
  const bundled = feature !== null && meta.bundled_ids.includes(feature.id);

  // Re-seed on every open: a cancelled edit must not leak into the next one.
  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue(
      feature ? featureToFormValues(feature) : emptyFormValues(meta),
    );
    setSaveError(null);
  }, [open, feature, meta, form]);

  const handleSubmit = useCallback(
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
        setSaveError(
          apiErrorMessage(err, t("features.settingsSaveFailed"), t),
        );
      } finally {
        setSaving(false);
      }
    },
    [creating, feature, onSaved, t],
  );

  const handleDelete = useCallback(async () => {
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

  return (
    <Drawer
      width="min(880px, 96vw)"
      placement="right"
      title={
        creating ? t("features.settingsNewTitle") : t("features.settingsEditTitle")
      }
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
              // The whole store, not just the registered fields: the capability
              // block is collapsed by default, and a collapsed antd ``Collapse``
              // does not mount its content — so its fields are *unregistered*,
              // and ``onFinish``'s values would silently drop the declared
              // capability layer on every save that never opened it.
              void handleSubmit(form.getFieldsValue(true) as FeatureFormValues)
            }
          >
            {saveError && (
              <Alert
                type="error"
                showIcon
                message={t("features.settingsSaveFailed")}
                description={saveError}
              />
            )}

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
                                throw new Error(
                                  t("features.settingsIdInvalid"),
                                );
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

            <SettingsSection
              titleKey="features.settingsSectionFields"
              hintKey="features.settingsSectionFieldsHint"
            >
              <Form.List
                name="fields"
                rules={[
                  {
                    validator: async (
                      _,
                      value: FeatureFieldRow[] | undefined,
                    ) => {
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
                              requiredRule(
                                t("features.settingsFieldNameRequired"),
                              ),
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
                              placeholder={t(
                                "features.settingsFieldNamePlaceholder",
                              )}
                              autoComplete="off"
                            />
                          </Form.Item>

                          <Form.Item
                            name={[field.name, "required"]}
                            valuePropName="checked"
                            className={styles.fieldRequiredItem}
                          >
                            <Checkbox>
                              {t("features.settingsFieldRequired")}
                            </Checkbox>
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
                            <Tooltip
                              title={t("features.settingsFieldMoveDown")}
                            >
                              <Button
                                type="text"
                                size="small"
                                aria-label={t(
                                  "features.settingsFieldMoveDown",
                                )}
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
                    {t("features.settingsUserTemplateHint")}{" "}
                    <code>{"{{inputs}}"}</code>
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

            <FeatureCapabilitySection />

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

            <SettingsSection
              titleKey="features.settingsSectionPermissions"
              hintKey="features.settingsPermissionsHint"
            >
              <Form.Item
                name="allowUnits"
                label={t("features.settingsAllowUnits")}
              >
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
              <Form.Item
                name="allowRoles"
                label={t("features.settingsAllowRoles")}
              >
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

            {feature && !bundled && (
              <SettingsSection titleKey="features.settingsDangerZone">
                <div className={styles.blockHint}>
                  {t("features.settingsDeleteHint")}
                </div>
                <Popconfirm
                  title={t("features.settingsDeleteConfirmTitle")}
                  description={t("features.settingsDeleteConfirmDesc", {
                    name: feature.id,
                  })}
                  okText={t("common.delete")}
                  okButtonProps={{ danger: true }}
                  cancelText={t("common.cancel")}
                  onConfirm={() => void handleDelete()}
                >
                  <Button danger icon={<Trash2 size={14} />} loading={deleting}>
                    {t("features.settingsDelete")}
                  </Button>
                </Popconfirm>
              </SettingsSection>
            )}
          </Form>
        </div>

        <div className={styles.footer}>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            type="primary"
            loading={saving}
            onClick={() => form.submit()}
          >
            {t("common.save")}
          </Button>
        </div>
      </div>
    </Drawer>
  );
}

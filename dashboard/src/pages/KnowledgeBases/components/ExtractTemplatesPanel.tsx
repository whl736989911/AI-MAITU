import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CircleCheck,
  CircleSlash,
  Link2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../../components/EmptyState";
import { ResizableTable } from "../../../components/ResizableTable";
import {
  dataSourcesApi,
  type DataSource,
} from "../../../api/modules/dataSources";
import {
  EXTRACT_FIELD_TYPES,
  extractTemplatesApi,
  type ExtractBinding,
  type ExtractField,
  type ExtractMatch,
  type ExtractTemplate,
} from "../../../api/modules/extractTemplates";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { apiErrorMessage } from "../../../utils/apiError";
import { PERM, userCanKey } from "../../../utils/permissions";
import styles from "./ExtractTemplatesPanel.module.less";

interface ExtractTemplatesPanelProps {
  /** Knowledge base whose data-source bindings this panel lists. */
  baseId: string;
  /** Page-computed write access to the base; managing templates also needs the settings key. */
  canWriteBase: boolean;
}

/** One editable field row. Options travel as a comma-separated string while editing. */
interface FieldRow {
  name: string;
  type: string;
  required: boolean;
  instruction: string;
  options: string;
}

interface EditorValues {
  name: string;
  description?: string;
  instruction?: string;
  applies_to?: string;
  fields?: FieldRow[];
}

interface BindingValues {
  data_source_id: string;
  path?: string;
  extension?: string;
  mime_type?: string;
  name_pattern?: string;
  match_regex?: string;
}

const EMPTY_FIELD: FieldRow = {
  name: "",
  type: "text",
  required: false,
  instruction: "",
  options: "",
};

function fieldRows(fields: ExtractField[] | undefined): FieldRow[] {
  return (fields ?? []).map((field) => ({
    name: field.name,
    type: field.type,
    required: Boolean(field.required),
    instruction: field.instruction || "",
    options: (field.options ?? []).join(", "),
  }));
}

/** Form rows back to the payload the API takes; blank rows are dropped. */
function toFields(rows: FieldRow[] | undefined): ExtractField[] {
  return (rows ?? [])
    .filter((row) => (row?.name || "").trim().length > 0)
    .map((row) => {
      const options = (row.options || "")
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
      return {
        name: row.name.trim(),
        type: (row.type || "text") as ExtractField["type"],
        required: Boolean(row.required),
        instruction: (row.instruction || "").trim(),
        ...(options.length > 0 ? { options } : {}),
      };
    });
}

function conditionSummary(binding: ExtractBinding): string {
  return [
    binding.extension ? `.${binding.extension.replace(/^\./, "")}` : "",
    binding.mime_type,
    binding.name_pattern,
    binding.match_regex,
  ]
    .filter(Boolean)
    .join(" · ");
}

export default function ExtractTemplatesPanel({
  baseId,
  canWriteBase,
}: ExtractTemplatesPanelProps) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const user = useCurrentUser();
  const canManage = canWriteBase && userCanKey(user, PERM.knowledgeSettings);
  const [templates, setTemplates] = useState<ExtractTemplate[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ExtractTemplate | null>(null);
  const [form] = Form.useForm<EditorValues>();

  const [bindingOpen, setBindingOpen] = useState(false);
  const [bindingTarget, setBindingTarget] = useState<ExtractTemplate | null>(
    null,
  );
  const [bindings, setBindings] = useState<ExtractBinding[]>([]);
  const [bindingForm] = Form.useForm<BindingValues>();
  const [checkPath, setCheckPath] = useState("");
  const [checkResult, setCheckResult] = useState<ExtractMatch | null>(null);
  const [checking, setChecking] = useState(false);

  const sourceNames = useMemo(() => {
    const byId = new Map<string, string>();
    for (const source of sources) {
      byId.set(source.data_source_id, source.name);
    }
    return byId;
  }, [sources]);

  const folderSources = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.kind === "local" ||
          source.kind === "smb" ||
          source.kind === "nfs",
      ),
    [sources],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rows, sourceRows] = await Promise.all([
        extractTemplatesApi.list(),
        dataSourcesApi.list(baseId),
      ]);
      setTemplates(rows);
      setSources(sourceRows);
    } catch (error) {
      message.error(
        apiErrorMessage(
          error,
          t("knowledgeBases.extractTemplates.loadFailed"),
          t,
        ),
      );
    } finally {
      setLoading(false);
    }
  }, [baseId, message, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = useCallback(() => {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      description: "",
      instruction: "",
      applies_to: "",
      fields: [{ ...EMPTY_FIELD }],
    });
    setEditorOpen(true);
  }, [form]);

  const openEdit = useCallback(
    (template: ExtractTemplate) => {
      setEditing(template);
      form.setFieldsValue({
        name: template.name,
        description: template.description,
        instruction: template.instruction,
        applies_to: template.applies_to,
        fields: fieldRows(template.fields),
      });
      setEditorOpen(true);
    },
    [form],
  );

  const submitEditor = useCallback(
    async (values: EditorValues) => {
      setSaving(true);
      try {
        const fields = toFields(values.fields);
        if (editing) {
          // design §7.3: an edit is a new version, so earlier results keep the
          // version that produced them.
          await extractTemplatesApi.addVersion(editing.template_id, {
            fields,
            instruction: values.instruction || "",
            applies_to: values.applies_to || "",
          });
          message.success(t("knowledgeBases.extractTemplates.versionCreated"));
        } else {
          await extractTemplatesApi.create({
            name: values.name.trim(),
            description: values.description || "",
            fields,
            instruction: values.instruction || "",
            applies_to: values.applies_to || "",
          });
          message.success(t("knowledgeBases.extractTemplates.saved"));
        }
        setEditorOpen(false);
        await load();
      } catch (error) {
        message.error(
          apiErrorMessage(
            error,
            t("knowledgeBases.extractTemplates.saveFailed"),
            t,
          ),
        );
      } finally {
        setSaving(false);
      }
    },
    [editing, load, message, t],
  );

  const toggleStatus = useCallback(
    async (template: ExtractTemplate) => {
      try {
        await extractTemplatesApi.update(template.template_id, {
          status: template.status === "active" ? "disabled" : "active",
        });
        await load();
      } catch (error) {
        message.error(
          apiErrorMessage(
            error,
            t("knowledgeBases.extractTemplates.saveFailed"),
            t,
          ),
        );
      }
    },
    [load, message, t],
  );

  const removeTemplate = useCallback(
    (template: ExtractTemplate) => {
      modal.confirm({
        title: t("knowledgeBases.extractTemplates.deleteConfirm", {
          name: template.name,
        }),
        okText: t("common.delete"),
        okButtonProps: { danger: true },
        cancelText: t("common.cancel"),
        onOk: async () => {
          try {
            await extractTemplatesApi.remove(template.template_id);
            await load();
          } catch (error) {
            // A bound template is refused with a reason that says how many
            // bindings stand in the way (design §7.2).
            message.error(
              apiErrorMessage(
                error,
                t("knowledgeBases.extractTemplates.deleteFailed"),
                t,
              ),
            );
          }
        },
      });
    },
    [load, message, modal, t],
  );

  const openBindings = useCallback(
    async (template: ExtractTemplate) => {
      setBindingTarget(template);
      setBindingOpen(true);
      setCheckPath("");
      setCheckResult(null);
      bindingForm.resetFields();
      try {
        setBindings(
          await extractTemplatesApi.listBindings(template.template_id),
        );
      } catch (error) {
        message.error(
          apiErrorMessage(
            error,
            t("knowledgeBases.extractTemplates.loadFailed"),
            t,
          ),
        );
      }
    },
    [bindingForm, message, t],
  );

  const addBinding = useCallback(
    async (values: BindingValues) => {
      if (!bindingTarget) return;
      try {
        await extractTemplatesApi.bind(bindingTarget.template_id, {
          data_source_id: values.data_source_id,
          path: values.path || "",
          extension: values.extension || "",
          mime_type: values.mime_type || "",
          name_pattern: values.name_pattern || "",
          match_regex: values.match_regex || "",
        });
        bindingForm.resetFields();
        setCheckResult(null);
        setBindings(
          await extractTemplatesApi.listBindings(bindingTarget.template_id),
        );
        await load();
      } catch (error) {
        message.error(
          apiErrorMessage(
            error,
            t("knowledgeBases.extractTemplates.bindingFailed"),
            t,
          ),
        );
      }
    },
    [bindingForm, bindingTarget, load, message, t],
  );

  const clearBinding = useCallback(
    async (binding: ExtractBinding) => {
      if (!bindingTarget) return;
      try {
        await extractTemplatesApi.unbind(binding.binding_id);
        setBindings(
          await extractTemplatesApi.listBindings(bindingTarget.template_id),
        );
        setCheckResult(null);
        await load();
      } catch (error) {
        message.error(
          apiErrorMessage(
            error,
            t("knowledgeBases.extractTemplates.unbindFailed"),
            t,
          ),
        );
      }
    },
    [bindingTarget, load, message, t],
  );

  const runCheck = useCallback(async () => {
    const path = checkPath.trim();
    const sourceId = bindingForm.getFieldValue("data_source_id") as
      | string
      | undefined;
    if (!path || !sourceId) return;
    setChecking(true);
    try {
      setCheckResult(await extractTemplatesApi.resolve(sourceId, path));
    } catch (error) {
      message.error(
        apiErrorMessage(
          error,
          t("knowledgeBases.extractTemplates.loadFailed"),
          t,
        ),
      );
    } finally {
      setChecking(false);
    }
  }, [bindingForm, checkPath, message, t]);

  const checkVerdict = useMemo(() => {
    if (!checkResult) return "";
    if (checkResult.conflicts.length > 0) {
      const names = checkResult.conflicts.map(
        (id) => templates.find((row) => row.template_id === id)?.name || id,
      );
      return t("knowledgeBases.extractTemplates.checkConflict", {
        names: names.join("、"),
      });
    }
    if (checkResult.template_id) {
      const name =
        templates.find((row) => row.template_id === checkResult.template_id)
          ?.name || checkResult.template_id;
      return t("knowledgeBases.extractTemplates.checkMatch", {
        name,
        level: t(
          `knowledgeBases.extractTemplates.level_${
            checkResult.level || "source"
          }`,
        ),
      });
    }
    return t("knowledgeBases.extractTemplates.checkNone");
  }, [checkResult, t, templates]);

  return (
    <section
      className={styles.panel}
      aria-label={t("knowledgeBases.extractTemplates.title")}
    >
      <div className={styles.header}>
        <span className={styles.headerTitle}>
          {t("knowledgeBases.extractTemplates.title")}
          {templates.length > 0 ? (
            <Tag className={styles.countTag}>{templates.length}</Tag>
          ) : null}
        </span>
        {canManage ? (
          <Button
            size="small"
            type="primary"
            icon={<Plus size={14} />}
            onClick={openCreate}
          >
            {t("knowledgeBases.extractTemplates.create")}
          </Button>
        ) : null}
      </div>
      <Typography.Text className={styles.hint} type="secondary">
        {t("knowledgeBases.extractTemplates.hint")}
      </Typography.Text>

      {loading ? (
        <div className={styles.loading}>
          <Spin size="small" />
        </div>
      ) : templates.length === 0 ? (
        <EmptyState
          title={t("knowledgeBases.extractTemplates.empty")}
          description={t("knowledgeBases.extractTemplates.emptyHint")}
          actionLabel={
            canManage ? t("knowledgeBases.extractTemplates.create") : undefined
          }
          onAction={canManage ? openCreate : undefined}
        />
      ) : (
        <ResizableTable
          storageKey="kb-extract-templates"
          rowKey="template_id"
          size="small"
          pagination={false}
          dataSource={templates}
          columns={[
            {
              title: t("knowledgeBases.extractTemplates.name"),
              dataIndex: "name",
              key: "name",
              render: (_: string, template: ExtractTemplate) => (
                <span className={styles.nameCell}>
                  <span className={styles.templateName} title={template.name}>
                    {template.name}
                  </span>
                  {template.applies_to ? (
                    <span
                      className={styles.templateDetail}
                      title={template.applies_to}
                    >
                      {t("knowledgeBases.extractTemplates.appliesToShort", {
                        types: template.applies_to,
                      })}
                    </span>
                  ) : null}
                </span>
              ),
            },
            {
              title: t("knowledgeBases.extractTemplates.status"),
              dataIndex: "status",
              key: "status",
              width: 110,
              render: (status: string) => (
                <Tag color={status === "active" ? "green" : "default"}>
                  {status === "active"
                    ? t("knowledgeBases.extractTemplates.statusActive")
                    : t("knowledgeBases.extractTemplates.statusDisabled")}
                </Tag>
              ),
            },
            {
              title: t("knowledgeBases.extractTemplates.version"),
              dataIndex: "current_version",
              key: "current_version",
              width: 90,
              render: (version: number) => `v${version}`,
            },
            {
              title: t("knowledgeBases.extractTemplates.fields"),
              dataIndex: "fields",
              key: "fields",
              width: 90,
              render: (fields: ExtractField[]) => fields.length,
            },
            {
              title: t("knowledgeBases.extractTemplates.bindings"),
              dataIndex: "bindings",
              key: "bindings",
              width: 90,
              render: (count: number, template: ExtractTemplate) => (
                <Button
                  size="small"
                  type="link"
                  onClick={() => void openBindings(template)}
                >
                  {count}
                </Button>
              ),
            },
            {
              title: t("common.actions"),
              key: "actions",
              width: canManage ? 150 : 60,
              render: (_: unknown, template: ExtractTemplate) => (
                <span className={styles.rowActions}>
                  {canManage ? (
                    <>
                      <Tooltip title={t("common.edit")}>
                        <span className={styles.actionSlot}>
                          <Button
                            size="small"
                            type="text"
                            icon={<Pencil size={14} />}
                            aria-label={t("common.edit")}
                            onClick={() => openEdit(template)}
                          />
                        </span>
                      </Tooltip>
                      <Tooltip
                        title={
                          template.status === "active"
                            ? t("knowledgeBases.extractTemplates.disable")
                            : t("knowledgeBases.extractTemplates.enable")
                        }
                      >
                        <span className={styles.actionSlot}>
                          <Button
                            size="small"
                            type="text"
                            icon={
                              template.status === "active" ? (
                                <CircleSlash size={14} />
                              ) : (
                                <CircleCheck size={14} />
                              )
                            }
                            aria-label={
                              template.status === "active"
                                ? t("knowledgeBases.extractTemplates.disable")
                                : t("knowledgeBases.extractTemplates.enable")
                            }
                            onClick={() => void toggleStatus(template)}
                          />
                        </span>
                      </Tooltip>
                      <Tooltip title={t("common.delete")}>
                        <span className={styles.actionSlot}>
                          <Button
                            size="small"
                            type="text"
                            danger
                            icon={<Trash2 size={14} />}
                            aria-label={t("common.delete")}
                            onClick={() => removeTemplate(template)}
                          />
                        </span>
                      </Tooltip>
                    </>
                  ) : null}
                  <Tooltip
                    title={t("knowledgeBases.extractTemplates.bindings")}
                  >
                    <span className={styles.actionSlot}>
                      <Button
                        size="small"
                        type="text"
                        icon={<Link2 size={14} />}
                        aria-label={t(
                          "knowledgeBases.extractTemplates.bindings",
                        )}
                        onClick={() => void openBindings(template)}
                      />
                    </span>
                  </Tooltip>
                </span>
              ),
            },
          ]}
        />
      )}

      <Modal
        open={editorOpen}
        title={
          editing
            ? t("knowledgeBases.extractTemplates.editTitle", {
                name: editing.name,
              })
            : t("knowledgeBases.extractTemplates.createTitle")
        }
        onCancel={() => setEditorOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={saving}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        width={760}
        destroyOnHidden
      >
        {editing ? (
          <Typography.Text type="secondary" className={styles.hint}>
            {t("knowledgeBases.extractTemplates.editHint", {
              version: editing.current_version + 1,
            })}
          </Typography.Text>
        ) : null}
        <Form form={form} layout="vertical" onFinish={submitEditor}>
          <Form.Item
            name="name"
            label={t("knowledgeBases.extractTemplates.name")}
            rules={[
              {
                required: true,
                message: t("knowledgeBases.extractTemplates.nameRequired"),
              },
            ]}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("knowledgeBases.extractTemplates.description")}
          >
            <Input maxLength={500} />
          </Form.Item>
          <Form.Item
            name="applies_to"
            label={t("knowledgeBases.extractTemplates.appliesTo")}
            extra={t("knowledgeBases.extractTemplates.appliesToHint")}
          >
            <Input placeholder="doc, docx, pdf" maxLength={500} />
          </Form.Item>
          <Form.Item
            name="instruction"
            label={t("knowledgeBases.extractTemplates.instruction")}
          >
            <Input.TextArea rows={2} maxLength={8000} />
          </Form.Item>
          <Form.List name="fields">
            {(rows, { add, remove }) => (
              <div className={styles.fields}>
                <div className={styles.fieldsHeader}>
                  <span>{t("knowledgeBases.extractTemplates.fields")}</span>
                  <Button
                    size="small"
                    icon={<Plus size={14} />}
                    onClick={() => add({ ...EMPTY_FIELD })}
                  >
                    {t("knowledgeBases.extractTemplates.addField")}
                  </Button>
                </div>
                {rows.map((row) => (
                  <div className={styles.fieldRow} key={row.key}>
                    <Form.Item
                      name={[row.name, "name"]}
                      rules={[
                        {
                          required: true,
                          message: t(
                            "knowledgeBases.extractTemplates.fieldNameRequired",
                          ),
                        },
                      ]}
                    >
                      <Input
                        placeholder={t(
                          "knowledgeBases.extractTemplates.fieldName",
                        )}
                      />
                    </Form.Item>
                    <Form.Item name={[row.name, "type"]}>
                      <Select
                        options={EXTRACT_FIELD_TYPES.map((type) => ({
                          value: type,
                          label: type,
                        }))}
                      />
                    </Form.Item>
                    <Form.Item
                      name={[row.name, "required"]}
                      valuePropName="checked"
                    >
                      <Switch
                        checkedChildren={t(
                          "knowledgeBases.extractTemplates.fieldRequiredShort",
                        )}
                        unCheckedChildren={t(
                          "knowledgeBases.extractTemplates.fieldOptionalShort",
                        )}
                      />
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate>
                      {({ getFieldValue }) => (
                        <Form.Item name={[row.name, "options"]}>
                          <Input
                            placeholder={t(
                              "knowledgeBases.extractTemplates.fieldOptions",
                            )}
                            disabled={
                              getFieldValue(["fields", row.name, "type"]) !==
                              "enum"
                            }
                          />
                        </Form.Item>
                      )}
                    </Form.Item>
                    <Form.Item name={[row.name, "instruction"]}>
                      <Input
                        placeholder={t(
                          "knowledgeBases.extractTemplates.fieldInstruction",
                        )}
                        maxLength={2000}
                      />
                    </Form.Item>
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<Trash2 size={14} />}
                      aria-label={t(
                        "knowledgeBases.extractTemplates.removeField",
                      )}
                      onClick={() => remove(row.name)}
                    />
                  </div>
                ))}
              </div>
            )}
          </Form.List>
        </Form>
      </Modal>

      <Modal
        open={bindingOpen}
        title={t("knowledgeBases.extractTemplates.bindingsTitle", {
          name: bindingTarget?.name || "",
        })}
        onCancel={() => setBindingOpen(false)}
        footer={null}
        width={720}
        destroyOnHidden
      >
        {bindings.length === 0 ? (
          <Typography.Text type="secondary">
            {t("knowledgeBases.extractTemplates.bindingEmpty")}
          </Typography.Text>
        ) : (
          <ul className={styles.bindingList}>
            {bindings.map((binding) => (
              <li className={styles.bindingItem} key={binding.binding_id}>
                <span className={styles.bindingName}>
                  {sourceNames.get(binding.data_source_id) ||
                    binding.data_source_id}
                  {binding.path ? ` / ${binding.path}` : ""}
                </span>
                <span className={styles.bindingDetail}>
                  {conditionSummary(binding)}
                </span>
                {canManage ? (
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<Trash2 size={14} />}
                    aria-label={t(
                      "knowledgeBases.extractTemplates.bindingClear",
                    )}
                    onClick={() => void clearBinding(binding)}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        )}

        {canManage ? (
          <Form
            form={bindingForm}
            layout="vertical"
            onFinish={addBinding}
            className={styles.bindingForm}
          >
            <Form.Item
              name="data_source_id"
              label={t("knowledgeBases.extractTemplates.bindingSource")}
              rules={[
                {
                  required: true,
                  message: t(
                    "knowledgeBases.extractTemplates.bindingSourceRequired",
                  ),
                },
              ]}
            >
              <Select
                options={folderSources.map((source) => ({
                  value: source.data_source_id,
                  label: source.name,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="path"
              label={t("knowledgeBases.extractTemplates.bindingPath")}
              extra={t("knowledgeBases.extractTemplates.bindingPathHint")}
            >
              <Input placeholder="legal/2026/contract.pdf" maxLength={1000} />
            </Form.Item>
            <Space wrap>
              <Form.Item
                name="extension"
                label={t("knowledgeBases.extractTemplates.bindingExtension")}
              >
                <Input placeholder="pdf" maxLength={200} />
              </Form.Item>
              <Form.Item
                name="mime_type"
                label={t("knowledgeBases.extractTemplates.bindingMime")}
              >
                <Input placeholder="application/pdf" maxLength={200} />
              </Form.Item>
              <Form.Item
                name="name_pattern"
                label={t("knowledgeBases.extractTemplates.bindingNamePattern")}
              >
                <Input placeholder="invoice-*.pdf" maxLength={200} />
              </Form.Item>
              <Form.Item
                name="match_regex"
                label={t("knowledgeBases.extractTemplates.bindingRegex")}
              >
                <Input placeholder="^legal/" maxLength={500} />
              </Form.Item>
            </Space>
            <Space>
              <Button type="primary" htmlType="submit">
                {t("knowledgeBases.extractTemplates.bindingAdd")}
              </Button>
              <Input
                value={checkPath}
                onChange={(event) => setCheckPath(event.target.value)}
                placeholder={t(
                  "knowledgeBases.extractTemplates.checkPlaceholder",
                )}
              />
              <Button
                icon={<Search size={14} />}
                loading={checking}
                onClick={() => void runCheck()}
              >
                {t("knowledgeBases.extractTemplates.check")}
              </Button>
            </Space>
            {checkResult ? (
              <Typography.Text
                className={
                  checkResult.conflicts.length > 0
                    ? styles.conflict
                    : styles.verdict
                }
              >
                {checkVerdict}
              </Typography.Text>
            ) : null}
          </Form>
        ) : null}
      </Modal>
    </section>
  );
}

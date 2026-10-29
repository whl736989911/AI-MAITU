import {
  createContext,
  useContext,
  useRef,
  useState,
  useEffect,
  type Dispatch,
  type SetStateAction,
  type ReactNode,
} from "react";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from "antd";
import { useTranslation } from "react-i18next";
import { Download, Plus, Search } from "lucide-react";
import type {
  DemoCollectionProps,
  DemoField,
  DemoRow,
  DemoScalar,
  DemoSettingsProps,
  DemoValues,
} from "./contract";
import styles from "./DemoKit.module.less";

interface MemoryContextValue {
  values: Record<string, unknown>;
  setValues: Dispatch<SetStateAction<Record<string, unknown>>>;
}
const MemoryContext = createContext<MemoryContextValue | null>(null);

export function DemoStateProvider({ children }: { children: ReactNode }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  return (
    <MemoryContext.Provider value={{ values, setValues }}>
      {children}
    </MemoryContext.Provider>
  );
}

export function useDemoState<T>(
  key: string,
  initialValue: T,
): [T, Dispatch<SetStateAction<T>>] {
  const context = useContext(MemoryContext);
  const initial = useRef({ key, value: initialValue });
  if (initial.current.key !== key)
    initial.current = { key, value: initialValue };
  // Defaults have state semantics: keep their identity stable until this key changes.
  const fallback = initial.current.value;
  if (!context) throw new Error("DemoStateProvider is required");
  const value = Object.hasOwn(context.values, key)
    ? (context.values[key] as T)
    : fallback;
  const setValue: Dispatch<SetStateAction<T>> = (action) =>
    context.setValues((previous) => {
      const before = Object.hasOwn(previous, key)
        ? (previous[key] as T)
        : fallback;
      const after =
        typeof action === "function"
          ? (action as (value: T) => T)(before)
          : action;
      return { ...previous, [key]: after };
    });
  return [value, setValue];
}

export function DemoBoundary({ children }: { children?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Alert
      className={styles.boundary}
      showIcon
      type="info"
      message={t("interfacePreview.full.common.boundary")}
      description={children}
    />
  );
}

export function DemoExternalAction({
  label,
  detail,
}: {
  label: string;
  detail?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>{label}</Button>
      <Modal
        open={open}
        title={label}
        onCancel={() => setOpen(false)}
        footer={
          <Button type="primary" onClick={() => setOpen(false)}>
            {t("interfacePreview.full.common.understood")}
          </Button>
        }
      >
        <Alert
          showIcon
          type="info"
          message={t("interfacePreview.full.common.externalOnly")}
        />
        <Typography.Paragraph className={styles.note}>
          {detail || t("interfacePreview.full.common.noExternalExecution")}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}

function fieldControl(field: DemoField) {
  switch (field.kind) {
    case "textarea":
      return <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} />;
    case "number":
      return <InputNumber style={{ width: "100%" }} />;
    case "switch":
      return <Switch />;
    case "select":
      return <Select options={field.options} />;
    case "password":
      return <Input.Password autoComplete="new-password" />;
    case "date":
      return <Input type="date" />;
    default:
      return <Input />;
  }
}

function DemoFields({ fields }: { fields: DemoField[] }) {
  const { t } = useTranslation();
  return (
    <>
      {fields.map((field) => (
        <Form.Item
          key={field.name}
          name={field.name}
          label={field.label}
          valuePropName={field.kind === "switch" ? "checked" : "value"}
          rules={
            field.required
              ? [
                  {
                    required: true,
                    message: t("interfacePreview.full.common.required", {
                      label: field.label,
                    }),
                  },
                ]
              : undefined
          }
          extra={
            field.help ||
            (field.kind === "password"
              ? t("interfacePreview.full.common.demoCredentials")
              : undefined)
          }
        >
          {fieldControl(field)}
        </Form.Item>
      ))}
    </>
  );
}

function defaultsFor(
  fields: DemoField[],
  initial: DemoValues = {},
): DemoValues {
  return Object.fromEntries(
    fields.map((field) => [
      field.name,
      initial[field.name] ??
        field.initialValue ??
        (field.kind === "switch" ? false : field.kind === "number" ? 0 : ""),
    ]),
  ) as DemoValues;
}
function valueFor(row: DemoRow, name: string): DemoScalar {
  if (name === "name") return row.name;
  if (name === "description") return row.description || "";
  if (name === "enabled") return row.enabled ?? true;
  return row.values?.[name] ?? "";
}
function rowFromValues(
  id: string,
  fields: DemoField[],
  values: DemoValues,
): DemoRow {
  const result: DemoRow = { id, name: String(values.name || ""), values: {} };
  for (const field of fields) {
    const value = values[field.name] ?? field.initialValue ?? "";
    if (field.name === "name") result.name = String(value);
    else if (field.name === "description") result.description = String(value);
    else if (field.name === "enabled") result.enabled = Boolean(value);
    else result.values![field.name] = value;
  }
  return result;
}
function csvCell(value: DemoScalar) {
  const text = String(value);
  return `"${(/^\s*[=+\-@]/.test(text) ? `'${text}` : text).replace(
    /"/g,
    '""',
  )}"`;
}

export function DemoCollection({
  storageKey,
  title,
  description,
  initialRows,
  fields,
  canCreate = true,
  canEdit = true,
  canDelete = true,
  canToggle = false,
  extra,
  rowActions = [],
  rowFilter,
  onSaved,
  onDeleted,
}: DemoCollectionProps) {
  const { t } = useTranslation();
  const common = (key: string) => t(`interfacePreview.full.common.${key}`);
  const [rows, setRows] = useDemoState<DemoRow[]>(storageKey, initialRows);
  const [query, setQuery] = useState("");
  const [editor, setEditor] = useState<DemoRow | "new" | null>(null);
  const [detail, setDetail] = useState<DemoRow | null>(null);
  const [notice, setNotice] = useState("");
  const [form] = Form.useForm<DemoValues>();
  const editFields: DemoField[] = fields.some((field) => field.name === "name")
    ? fields.map((field) =>
        field.name === "name"
          ? { ...field, required: field.required ?? true }
          : field,
      )
    : [{ name: "name", label: common("name"), required: true }, ...fields];
  if (canToggle && !editFields.some((field) => field.name === "enabled"))
    editFields.push({
      name: "enabled",
      label: common("enabled"),
      kind: "switch",
      initialValue: true,
    });
  const filtered = rows.filter(
    (row) =>
      (!rowFilter || rowFilter(row)) &&
      [
        row.id,
        row.name,
        row.description || "",
        ...Object.values(row.values || {}),
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
  );
  const showEditor = (row: DemoRow | "new") => {
    form.resetFields();
    form.setFieldsValue(
      row === "new"
        ? defaultsFor(editFields)
        : (Object.fromEntries(
            editFields.map((field) => [field.name, valueFor(row, field.name)]),
          ) as DemoValues),
    );
    setEditor(row);
  };
  const save = (values: DemoValues) => {
    if (!editor) return;
    const edited = rowFromValues(
      editor === "new" ? `demo-${crypto.randomUUID()}` : editor.id,
      editFields,
      values,
    );
    const row =
      editor === "new"
        ? edited
        : {
            ...editor,
            ...edited,
            values: { ...editor.values, ...edited.values },
          };
    setRows((previous) =>
      editor === "new"
        ? [...previous, row]
        : previous.map((item) => (item.id === row.id ? row : item)),
    );
    onSaved?.(row, editor === "new" ? undefined : editor);
    setEditor(null);
    setNotice(common("saved"));
  };
  const columns: TableColumnsType<DemoRow> = editFields
    .filter((field) => field.name !== "enabled")
    .map((field) => ({
      key: field.name,
      title: field.label,
      width: field.name === "name" ? 220 : 180,
      render: (_, row) =>
        field.name === "name" ? (
          <Button type="link" onClick={() => setDetail(row)}>
            {row.name}
          </Button>
        ) : field.kind === "password" ? (
          "••••••"
        ) : field.kind === "switch" ? (
          <Tag>{valueFor(row, field.name) ? common("yes") : common("no")}</Tag>
        ) : (
          String(valueFor(row, field.name))
        ),
    }));
  if (canToggle)
    columns.push({
      key: "enabled",
      title: common("enabled"),
      width: 100,
      render: (_, row) => (
        <Switch
          checked={row.enabled ?? true}
          aria-label={`${common("toggle")} ${row.name}`}
          onChange={(enabled) =>
            setRows((previous) =>
              previous.map((item) =>
                item.id === row.id ? { ...item, enabled } : item,
              ),
            )
          }
        />
      ),
    });
  columns.push({
    key: "actions",
    title: t("common.actions"),
    width: 240,
    render: (_, row) => (
      <Space wrap>
        <Button type="link" onClick={() => setDetail(row)}>
          {t("common.view")}
        </Button>
        {canEdit && (
          <Button type="link" onClick={() => showEditor(row)}>
            {t("common.edit")}
          </Button>
        )}
        {canDelete && (
          <Popconfirm
            title={common("deleteConfirm")}
            description={common("localOnly")}
            onConfirm={() => {
              setRows((previous) =>
                previous.filter((item) => item.id !== row.id),
              );
              onDeleted?.(row);
              setNotice(common("deleted"));
            }}
          >
            <Button type="link" danger>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        )}
        {rowActions.map((action) => (
          <Button
            type="link"
            key={action.key}
            onClick={() => action.onClick(row)}
          >
            {action.label}
          </Button>
        ))}
      </Space>
    ),
  });
  const download = () => {
    const exportedFields = editFields.filter(
      (field) => field.kind !== "password",
    );
    const csv = [
      exportedFields.map((field) => csvCell(field.label)).join(","),
      ...rows.map((row) =>
        exportedFields
          .map((field) => csvCell(valueFor(row, field.name)))
          .join(","),
      ),
    ].join("\r\n");
    const url = URL.createObjectURL(
      new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "MAITU_demo.csv";
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice(common("exported"));
  };
  return (
    <section className={styles.collection} data-demo-collection={storageKey}>
      {title && <Typography.Title level={5}>{title}</Typography.Title>}
      {description && (
        <Typography.Paragraph type="secondary">
          {description}
        </Typography.Paragraph>
      )}
      <div className={styles.tools}>
        <Input
          allowClear
          prefix={<Search size={15} />}
          aria-label={common("search")}
          placeholder={common("search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Typography.Text type="secondary">
          {filtered.length} {common("records")}
        </Typography.Text>
        <Space wrap>
          {extra}
          <Button icon={<Download size={15} />} onClick={download}>
            {common("export")}
          </Button>
          {canCreate && (
            <Button
              type="primary"
              icon={<Plus size={15} />}
              onClick={() => showEditor("new")}
            >
              {t("common.create")}
            </Button>
          )}
        </Space>
      </div>
      {notice && (
        <Typography.Paragraph role="status" className={styles.notice}>
          {notice}
        </Typography.Paragraph>
      )}
      <Table
        rowKey="id"
        columns={columns}
        dataSource={filtered}
        scroll={{ x: Math.max(720, columns.length * 180) }}
        pagination={{
          pageSize: 8,
          showSizeChanger: false,
          hideOnSinglePage: true,
        }}
        locale={{ emptyText: <Empty description={common("empty")} /> }}
      />
      <Modal
        open={editor !== null}
        title={editor === "new" ? t("common.create") : t("common.edit")}
        onCancel={() => setEditor(null)}
        onOk={() => form.submit()}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Form
          name={`${storageKey}-editor`}
          form={form}
          layout="vertical"
          onFinish={save}
        >
          <DemoFields fields={editFields} />
        </Form>
      </Modal>
      <Modal
        open={detail !== null}
        title={detail?.name}
        onCancel={() => setDetail(null)}
        footer={
          <Button onClick={() => setDetail(null)}>{t("common.close")}</Button>
        }
      >
        <Descriptions
          bordered
          column={1}
          size="small"
          items={
            detail
              ? [
                  {
                    key: "id",
                    label: common("identifier"),
                    children: detail.id,
                  },
                  ...editFields.map((field) => ({
                    key: field.name,
                    label: field.label,
                    children:
                      field.kind === "password"
                        ? "••••••"
                        : typeof valueFor(detail, field.name) === "boolean"
                        ? valueFor(detail, field.name)
                          ? common("yes")
                          : common("no")
                        : String(valueFor(detail, field.name)),
                  })),
                ]
              : []
          }
        />
      </Modal>
    </section>
  );
}

export function DemoSettings({
  storageKey,
  title,
  description,
  fields,
  initialValues = {},
  onSaved,
}: DemoSettingsProps) {
  const { t } = useTranslation();
  const [values, setValues] = useDemoState<DemoValues>(
    storageKey,
    defaultsFor(fields, initialValues),
  );
  const [saved, setSaved] = useState(false);
  const [form] = Form.useForm<DemoValues>();
  useEffect(() => {
    form.setFieldsValue(values);
  }, [form, values]);
  return (
    <section className={styles.settings} data-demo-settings={storageKey}>
      {title && <Typography.Title level={5}>{title}</Typography.Title>}
      {description && (
        <Typography.Paragraph type="secondary">
          {description}
        </Typography.Paragraph>
      )}
      <Form
        name={`${storageKey}-settings`}
        form={form}
        layout="vertical"
        onFinish={(next) => {
          const normalized = defaultsFor(fields, next);
          setValues(normalized);
          setSaved(true);
          onSaved?.(normalized);
        }}
        onValuesChange={() => setSaved(false)}
      >
        <DemoFields fields={fields} />
        <Space wrap>
          <Button type="primary" htmlType="submit">
            {t("common.save")}
          </Button>
          <Button
            onClick={() => {
              const defaults = defaultsFor(fields, initialValues);
              setValues(defaults);
              form.resetFields();
              form.setFieldsValue(defaults);
              setSaved(false);
            }}
          >
            {t("common.reset")}
          </Button>
          {saved && (
            <Typography.Text role="status" type="success">
              {t("interfacePreview.full.common.saved")}
            </Typography.Text>
          )}
        </Space>
      </Form>
    </section>
  );
}

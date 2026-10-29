import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { PreviewDocument, PreviewRecord } from "../model";
import { DemoBoundary, DemoCollection, useDemoState } from "./DemoKit";
import styles from "./WorkspacePages.module.less";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import {
  formatServerDateTime,
  formatServerYmd,
} from "../../../utils/formatMessageTime";

interface BusinessField {
  id: string;
  label: string;
  type: "text" | "number" | "date" | "select" | "file" | "document" | "record";
  required: boolean;
  options: string[];
}
interface BusinessObject {
  id: string;
  name: string;
  description: string;
  fields: BusinessField[];
}
interface BusinessRecord {
  id: string;
  objectId: string;
  name: string;
  values: Record<string, string>;
  updated: string;
}
interface BusinessAttachment {
  id: string;
  name: string;
  record: string;
  versions: { version: number; size: string; note: string; updated: string }[];
}
interface FeatureDataDemoProps {
  featureId: string;
  t: (key: string) => string;
  openRecord: (record: PreviewRecord) => void;
  openDocument: (documentId: string) => void;
  previewRecords: readonly PreviewRecord[];
  previewDocuments: readonly PreviewDocument[];
  tab?: string;
  navigate: (path: string, tab?: string, item?: string) => void;
}
interface ObjectFormValues {
  name: string;
  description: string;
}
interface FieldFormValues {
  label: string;
  type: BusinessField["type"];
  required: boolean;
  options: string;
}

const fieldTypes = [
  "text",
  "number",
  "date",
  "select",
  "file",
  "document",
  "record",
] as const;
const FEATURE_DATA_TABS = [
  "schema",
  "records",
  "attachments",
  "history",
  "notifications",
];

function initialObjects(featureId: string): BusinessObject[] {
  const contractId = `${featureId}-contracts`;
  const equipmentId = `${featureId}-equipment`;
  return [
    {
      id: contractId,
      name: "合同档案",
      description: "合同编号、供应商、金额与期限",
      fields: [
        {
          id: "supplier",
          label: "供应商",
          type: "text",
          required: true,
          options: [],
        },
        {
          id: "amount",
          label: "合同金额",
          type: "number",
          required: true,
          options: [],
        },
        {
          id: "due",
          label: "到期日",
          type: "date",
          required: false,
          options: [],
        },
        {
          id: "contract-file",
          label: "合同文件",
          type: "file",
          required: false,
          options: [],
        },
        {
          id: "certificate",
          label: "关联合规文件",
          type: "document",
          required: false,
          options: ["检定证书.pdf", "采购框架协议.pdf"],
        },
        {
          id: "status",
          label: "状态",
          type: "select",
          required: true,
          options: ["待审阅", "生效中", "已归档"],
        },
      ],
    },
    {
      id: equipmentId,
      name: "设备台账",
      description: "设备信息、责任人与维护状态",
      fields: [
        {
          id: "asset",
          label: "设备编号",
          type: "text",
          required: true,
          options: [],
        },
        {
          id: "owner",
          label: "责任人",
          type: "text",
          required: true,
          options: [],
        },
        {
          id: "state",
          label: "设备状态",
          type: "select",
          required: true,
          options: ["正常", "待检修", "停用"],
        },
        {
          id: "maintenance-record",
          label: "关联维护记录",
          type: "record",
          required: false,
          options: ["包装线一号机维护记录"],
        },
      ],
    },
  ];
}

function initialRecords(featureId: string): BusinessRecord[] {
  return [
    {
      id: `${featureId}-r1`,
      objectId: `${featureId}-contracts`,
      name: "ACME框架采购合同",
      values: {
        supplier: "ACME供应链",
        amount: "128000",
        due: "2026-12-15",
        status: "待审阅",
      },
      updated: "10月24日 14:03",
    },
    {
      id: `${featureId}-r2`,
      objectId: `${featureId}-contracts`,
      name: "北区设备维保协议",
      values: {
        supplier: "北区服务中心",
        amount: "42600",
        due: "2027-03-31",
        status: "生效中",
      },
      updated: "10月22日 09:15",
    },
    {
      id: `${featureId}-r3`,
      objectId: `${featureId}-equipment`,
      name: "包装线一号机",
      values: { asset: "PKG-001", owner: "陈明", state: "待检修" },
      updated: "10月25日 16:40",
    },
  ];
}

function formControl(field: BusinessField, t: (key: string) => string) {
  if (field.type === "number") return <InputNumber style={{ width: "100%" }} />;
  if (field.type === "date") return <Input type="date" />;
  if (field.type === "file")
    return (
      <Input
        placeholder={t("interfacePreview.businessFlow.fileReferenceHint")}
      />
    );
  if (["select", "document", "record"].includes(field.type))
    return (
      <Select
        options={field.options.map((option) => ({
          label: option,
          value: option,
        }))}
      />
    );
  return <Input />;
}

export default function FeatureDataDemo({
  featureId,
  t,
  openRecord,
  openDocument,
  previewRecords,
  previewDocuments,
  tab,
  navigate,
}: FeatureDataDemoProps) {
  const key = `workspace.featureData.${featureId}`;
  const [activeTab, setActiveTab] = useState(
    FEATURE_DATA_TABS.includes(tab || "") ? tab! : "schema",
  );
  useEffect(() => {
    setActiveTab(FEATURE_DATA_TABS.includes(tab || "") ? tab! : "schema");
  }, [tab]);
  const [objects, setObjects] = useDemoState<BusinessObject[]>(
    `${key}.objects`,
    initialObjects(featureId),
  );
  const [records, setRecords] = useDemoState<BusinessRecord[]>(
    `${key}.records`,
    initialRecords(featureId),
  );
  const [history, setHistory] = useDemoState<string[]>(`${key}.history`, [
    "10月26日 10:20 · 工作流规则已更新（示例历史）",
    "10月24日 14:03 · 新增 ACME 框架采购合同（示例历史）",
    "10月22日 09:15 · 设备台账字段已调整（示例历史）",
  ]);
  const [attachments, setAttachments] = useDemoState<BusinessAttachment[]>(
    `${key}.attachments`,
    [
      {
        id: "attachment-contract",
        name: "采购框架协议.pdf",
        record: "ACME框架采购合同",
        versions: [
          {
            version: 2,
            size: "248 KB",
            note: "补充签章页",
            updated: "10月24日",
          },
          { version: 1, size: "231 KB", note: "初始上传", updated: "10月20日" },
        ],
      },
    ],
  );
  const [selectedAttachment, setSelectedAttachment] =
    useState<BusinessAttachment | null>(null);
  const [objectId, setObjectId] = useState(
    `${featureId}-${
      featureId === "feature-inspection" ? "equipment" : "contracts"
    }`,
  );
  const [recordQuery, setRecordQuery] = useState("");
  const [recordObjectFilter, setRecordObjectFilter] = useState(
    featureId === "feature-inspection" ? `${featureId}-equipment` : "all",
  );
  const [inspectionQuery, setInspectionQuery] = useState("");
  const [inspectionStatus, setInspectionStatus] = useState("all");
  const [selectedRecord, setSelectedRecord] = useState<BusinessRecord | null>(
    null,
  );
  const timeZone = useServerTimezone();
  const [objectEditorOpen, setObjectEditorOpen] = useState(false);
  const [fieldEditorOpen, setFieldEditorOpen] = useState(false);
  const [recordEditorOpen, setRecordEditorOpen] = useState(false);
  const [editingObjectId, setEditingObjectId] = useState<string | null>(null);
  const [editingFieldId, setEditingFieldId] = useState<string | null>(null);
  const [editingRecordId, setEditingRecordId] = useState<string | null>(null);
  const [recordDraftObjectId, setRecordDraftObjectId] = useState(objectId);
  const [recordForm] = Form.useForm<Record<string, string>>();
  const [objectForm] = Form.useForm<ObjectFormValues>();
  const [fieldForm] = Form.useForm<FieldFormValues>();
  const object = objects.find((item) => item.id === objectId) || objects[0];
  const recordObject =
    objects.find((item) => item.id === recordDraftObjectId) || objects[0];
  const recordList = records.filter(
    (record) =>
      (recordObjectFilter === "all" ||
        record.objectId === recordObjectFilter) &&
      `${record.name} ${Object.values(record.values).join(" ")}`
        .toLocaleLowerCase()
        .includes(recordQuery.trim().toLocaleLowerCase()),
  );
  const inspectionRecords = previewRecords.filter((record) => {
    const normalizedQuery = inspectionQuery.trim().toLocaleLowerCase();
    const matchesQuery =
      `${record.code} ${record.name} ${record.department} ${record.responsible}`
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    const matchesStatus =
      inspectionStatus === "all" ||
      (inspectionStatus === "overdue" && record.remainingDays < 0) ||
      (inspectionStatus === "soon" &&
        record.remainingDays >= 0 &&
        record.remainingDays <= 7) ||
      (inspectionStatus === "normal" && record.remainingDays > 7);
    return matchesQuery && matchesStatus;
  });
  const addHistory = (event: string) =>
    setHistory((current) => [
      `${formatServerDateTime(
        Date.now() / 1000,
        timeZone,
      )} · ${event}（本地示例）`,
      ...current,
    ]);
  const editObject = (item?: BusinessObject) => {
    setEditingObjectId(item?.id || null);
    objectForm.setFieldsValue({
      name: item?.name || "",
      description: item?.description || "",
    });
    setObjectEditorOpen(true);
  };
  const saveObject = (values: ObjectFormValues) => {
    if (editingObjectId) {
      setObjects((current) =>
        current.map((item) =>
          item.id === editingObjectId
            ? {
                ...item,
                name: values.name.trim(),
                description: values.description.trim(),
              }
            : item,
        ),
      );
      addHistory(`编辑业务对象：${values.name.trim()}`);
    } else {
      const next: BusinessObject = {
        id: `${featureId}-object-${crypto.randomUUID()}`,
        name: values.name.trim(),
        description: values.description.trim(),
        fields: [],
      };
      setObjects((current) => [...current, next]);
      setObjectId(next.id);
      addHistory(`创建业务对象：${next.name}`);
    }
    setObjectEditorOpen(false);
  };
  const editField = (field?: BusinessField) => {
    setEditingFieldId(field?.id || null);
    fieldForm.setFieldsValue({
      label: field?.label || "",
      type: field?.type || "text",
      required: field?.required ?? false,
      options: field?.options.join("、") || "",
    });
    setFieldEditorOpen(true);
  };
  const saveField = (values: FieldFormValues) => {
    if (!object) return;
    const next: BusinessField = {
      id: editingFieldId || `field-${crypto.randomUUID()}`,
      label: values.label.trim(),
      type: values.type,
      required: values.required,
      options: ["select", "document", "record"].includes(values.type)
        ? values.options
            .split(/[、,，]/)
            .map((option) => option.trim())
            .filter(Boolean)
        : [],
    };
    setObjects((current) =>
      current.map((item) =>
        item.id === object.id
          ? {
              ...item,
              fields: editingFieldId
                ? item.fields.map((field) =>
                    field.id === editingFieldId ? next : field,
                  )
                : [...item.fields, next],
            }
          : item,
      ),
    );
    addHistory(`${editingFieldId ? "编辑" : "添加"}字段：${next.label}`);
    setFieldEditorOpen(false);
  };
  const openRecordEditor = (record?: BusinessRecord) => {
    const targetObjectId = record?.objectId || objectId;
    const target = objects.find((item) => item.id === targetObjectId);
    if (!target) return;
    setEditingRecordId(record?.id || null);
    setRecordDraftObjectId(targetObjectId);
    recordForm.resetFields();
    recordForm.setFieldsValue({ name: record?.name || "", ...record?.values });
    setRecordEditorOpen(true);
  };
  const saveRecord = (values: Record<string, string>) => {
    const target = objects.find((item) => item.id === recordDraftObjectId);
    if (!target) return;
    const fieldValues = Object.fromEntries(
      target.fields.map((field) => [field.id, String(values[field.id] ?? "")]),
    );
    const next: BusinessRecord = {
      id: editingRecordId || `${featureId}-record-${crypto.randomUUID()}`,
      objectId: target.id,
      name: String(values.name).trim(),
      values: fieldValues,
      updated: t("interfacePreview.full.workspace.justNow"),
    };
    setRecords((current) =>
      editingRecordId
        ? current.map((record) =>
            record.id === editingRecordId ? next : record,
          )
        : [next, ...current],
    );
    addHistory(`${editingRecordId ? "编辑" : "新增"}记录：${next.name}`);
    setRecordEditorOpen(false);
  };
  const tabItems = [
    {
      key: "schema",
      label: t("interfacePreview.full.workspace.businessObjects"),
      children: (
        <div className={styles.dataGrid}>
          <Card
            title={t("interfacePreview.full.workspace.businessObjects")}
            extra={
              <Button type="primary" onClick={() => editObject()}>
                {t("interfacePreview.full.workspace.addObject")}
              </Button>
            }
          >
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={objects}
              columns={[
                {
                  title: t("interfacePreview.full.workspace.name"),
                  dataIndex: "name",
                  render: (name: string, row: BusinessObject) => (
                    <Button type="link" onClick={() => setObjectId(row.id)}>
                      {name}
                    </Button>
                  ),
                },
                {
                  title: t("interfacePreview.full.workspace.fields"),
                  key: "count",
                  render: (_, row: BusinessObject) => row.fields.length,
                },
                {
                  title: t("interfacePreview.full.workspace.actions"),
                  key: "actions",
                  render: (_, row: BusinessObject) => (
                    <Space>
                      <Button
                        type="link"
                        onClick={() => {
                          setObjectId(row.id);
                          editObject(row);
                        }}
                      >
                        {t("common.edit")}
                      </Button>
                      <Popconfirm
                        title={t(
                          "interfacePreview.full.workspace.deleteObjectConfirm",
                        )}
                        onConfirm={() => {
                          setObjects((current) =>
                            current.filter((item) => item.id !== row.id),
                          );
                          setRecords((current) =>
                            current.filter(
                              (record) => record.objectId !== row.id,
                            ),
                          );
                          if (objectId === row.id)
                            setObjectId(
                              objects.find((item) => item.id !== row.id)?.id ||
                                "",
                            );
                          addHistory(`删除业务对象：${row.name}`);
                        }}
                      >
                        <Button type="link" danger>
                          {t("common.delete")}
                        </Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
          {object && (
            <Card
              title={`${t("interfacePreview.full.workspace.fieldSchema")} · ${
                object.name
              }`}
              extra={
                <Button onClick={() => editField()}>
                  {t("interfacePreview.full.workspace.addField")}
                </Button>
              }
            >
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={object.fields}
                columns={[
                  {
                    title: t("interfacePreview.full.workspace.fieldName"),
                    dataIndex: "label",
                  },
                  {
                    title: t("interfacePreview.full.workspace.fieldType"),
                    dataIndex: "type",
                    render: (value: BusinessField["type"]) =>
                      t(`interfacePreview.businessFlow.fieldTypes.${value}`),
                  },
                  {
                    title: t("interfacePreview.full.workspace.required"),
                    dataIndex: "required",
                    render: (required: boolean) => (
                      <Tag>
                        {required
                          ? t("interfacePreview.full.workspace.yes")
                          : t("interfacePreview.full.workspace.no")}
                      </Tag>
                    ),
                  },
                  {
                    title: t("interfacePreview.full.workspace.options"),
                    dataIndex: "options",
                    render: (options: string[]) => options.join("、") || "—",
                  },
                  {
                    title: t("interfacePreview.full.workspace.actions"),
                    key: "actions",
                    render: (_, field: BusinessField) => (
                      <Space>
                        <Button type="link" onClick={() => editField(field)}>
                          {t("common.edit")}
                        </Button>
                        <Popconfirm
                          title={t(
                            "interfacePreview.full.workspace.deleteFieldConfirm",
                          )}
                          onConfirm={() => {
                            setObjects((current) =>
                              current.map((item) =>
                                item.id === object.id
                                  ? {
                                      ...item,
                                      fields: item.fields.filter(
                                        (candidate) =>
                                          candidate.id !== field.id,
                                      ),
                                    }
                                  : item,
                              ),
                            );
                            addHistory(`删除字段：${field.label}`);
                          }}
                        >
                          <Button type="link" danger>
                            {t("common.delete")}
                          </Button>
                        </Popconfirm>
                      </Space>
                    ),
                  },
                ]}
              />
            </Card>
          )}
        </div>
      ),
    },
    {
      key: "records",
      label: t("interfacePreview.full.workspace.records"),
      children: (
        <>
          {featureId === "feature-inspection" && (
            <Card
              title={t("interfacePreview.full.workspace.inspectionLedgerTitle")}
              className={styles.ledgerCard}
            >
              <div className={styles.recordFilters}>
                <Input.Search
                  allowClear
                  value={inspectionQuery}
                  onChange={(event) => setInspectionQuery(event.target.value)}
                  placeholder={t(
                    "interfacePreview.full.workspace.inspectionSearchPlaceholder",
                  )}
                />
                <Select
                  value={inspectionStatus}
                  onChange={setInspectionStatus}
                  options={[
                    {
                      value: "all",
                      label: t(
                        "interfacePreview.full.workspace.inspectionFilterAll",
                      ),
                    },
                    {
                      value: "overdue",
                      label: t(
                        "interfacePreview.full.workspace.inspectionFilterOverdue",
                      ),
                    },
                    {
                      value: "soon",
                      label: t(
                        "interfacePreview.full.workspace.inspectionFilterSoon",
                      ),
                    },
                    {
                      value: "normal",
                      label: t(
                        "interfacePreview.full.workspace.inspectionFilterNormal",
                      ),
                    },
                  ]}
                />
              </div>
              <Table
                rowKey="id"
                dataSource={inspectionRecords}
                pagination={false}
                scroll={{ x: 1120 }}
                columns={[
                  {
                    title: t("interfacePreview.full.workspace.inspectionCode"),
                    dataIndex: "code",
                    width: 110,
                    render: (code: string, row: PreviewRecord) => (
                      <Button type="link" onClick={() => openRecord(row)}>
                        {code}
                      </Button>
                    ),
                  },
                  {
                    title: t("interfacePreview.full.workspace.inspectionName"),
                    dataIndex: "name",
                    width: 140,
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionDepartment",
                    ),
                    dataIndex: "department",
                    width: 140,
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionResponsible",
                    ),
                    dataIndex: "responsible",
                    width: 100,
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionIssuedOn",
                    ),
                    dataIndex: "issuedOn",
                    width: 120,
                  },
                  {
                    title: t("interfacePreview.full.workspace.inspectionCycle"),
                    dataIndex: "cycleMonths",
                    width: 100,
                    render: (value: number) =>
                      `${value} ${t(
                        "interfacePreview.full.workspace.inspectionMonths",
                      )}`,
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionNextDue",
                    ),
                    dataIndex: "nextDue",
                    width: 120,
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionRemaining",
                    ),
                    dataIndex: "remainingDays",
                    width: 120,
                    defaultSortOrder: "ascend",
                    sorter: (left: PreviewRecord, right: PreviewRecord) =>
                      left.remainingDays - right.remainingDays,
                    render: (value: number) => (
                      <Tag
                        color={
                          value < 0 ? "red" : value <= 7 ? "orange" : "green"
                        }
                      >
                        {value < 0
                          ? `${t(
                              "interfacePreview.full.workspace.inspectionOverdueBy",
                            )} ${Math.abs(value)} ${t(
                              "interfacePreview.full.workspace.inspectionDays",
                            )}`
                          : `${value} ${t(
                              "interfacePreview.full.workspace.inspectionDays",
                            )}`}
                      </Tag>
                    ),
                  },
                  {
                    title: t(
                      "interfacePreview.full.workspace.inspectionCertificate",
                    ),
                    key: "documents",
                    width: 220,
                    render: (_: unknown, row: PreviewRecord) => (
                      <Space wrap>
                        {row.documentIds.map((documentId) => {
                          const document = previewDocuments.find(
                            (entry) => entry.id === documentId,
                          );
                          return document ? (
                            <Button
                              key={document.id}
                              type="link"
                              size="small"
                              onClick={() => openDocument(document.id)}
                            >
                              {document.name}
                            </Button>
                          ) : null;
                        })}
                      </Space>
                    ),
                  },
                ]}
              />
              <Typography.Text type="secondary">
                {t("interfacePreview.full.workspace.inspectionReferenceDate")}
              </Typography.Text>
            </Card>
          )}
          <Card
            title={t("interfacePreview.full.workspace.records")}
            extra={
              <Button
                type="primary"
                disabled={!objects.length}
                onClick={() => openRecordEditor()}
              >
                {t("interfacePreview.full.workspace.addRecord")}
              </Button>
            }
          >
            <div className={styles.recordFilters}>
              <Input.Search
                allowClear
                value={recordQuery}
                onChange={(event) => setRecordQuery(event.target.value)}
                placeholder={t("interfacePreview.full.workspace.filterRecords")}
              />
              <Select
                value={recordObjectFilter}
                onChange={setRecordObjectFilter}
                options={[
                  {
                    value: "all",
                    label: t("interfacePreview.full.workspace.allObjects"),
                  },
                  ...objects.map((item) => ({
                    value: item.id,
                    label: item.name,
                  })),
                ]}
              />
            </div>
            <Table
              rowKey="id"
              dataSource={recordList}
              pagination={{ pageSize: 6 }}
              columns={[
                {
                  title: t("interfacePreview.full.workspace.name"),
                  dataIndex: "name",
                  render: (name: string, row: BusinessRecord) => (
                    <Button type="link" onClick={() => setSelectedRecord(row)}>
                      {name}
                    </Button>
                  ),
                },
                {
                  title: t("interfacePreview.full.workspace.businessObject"),
                  dataIndex: "objectId",
                  render: (id: string) =>
                    objects.find((item) => item.id === id)?.name || "—",
                },
                {
                  title: t("interfacePreview.full.workspace.updated"),
                  dataIndex: "updated",
                },
                {
                  title: t("interfacePreview.full.workspace.actions"),
                  key: "actions",
                  render: (_, row: BusinessRecord) => (
                    <Space>
                      <Button
                        type="link"
                        onClick={() => setSelectedRecord(row)}
                      >
                        {t("interfacePreview.full.workspace.viewRecord")}
                      </Button>
                      <Button type="link" onClick={() => openRecordEditor(row)}>
                        {t("common.edit")}
                      </Button>
                      <Popconfirm
                        title={t(
                          "interfacePreview.full.workspace.deleteRecordConfirm",
                        )}
                        onConfirm={() => {
                          setRecords((current) =>
                            current.filter((record) => record.id !== row.id),
                          );
                          addHistory(`删除记录：${row.name}`);
                        }}
                      >
                        <Button type="link" danger>
                          {t("common.delete")}
                        </Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
        </>
      ),
    },
    {
      key: "attachments",
      label: t("interfacePreview.full.workspace.attachments"),
      children: (
        <>
          <Card
            title={t("interfacePreview.full.workspace.attachments")}
            extra={
              <label className={styles.localFilePicker}>
                <input
                  type="file"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    setAttachments((current) => {
                      const existing = current.find(
                        (item) => item.name === file.name,
                      );
                      const version = (existing?.versions[0]?.version || 0) + 1;
                      const nextVersion = {
                        version,
                        size: `${Math.max(1, Math.round(file.size / 1024))} KB`,
                        note: t(
                          "interfacePreview.businessFlow.localFileMetadataOnly",
                        ),
                        updated: formatServerYmd(Date.now(), timeZone),
                      };
                      return existing
                        ? current.map((item) =>
                            item.id === existing.id
                              ? {
                                  ...item,
                                  versions: [nextVersion, ...item.versions],
                                }
                              : item,
                          )
                        : [
                            {
                              id: `attachment-${crypto.randomUUID()}`,
                              name: file.name,
                              record: t(
                                "interfacePreview.businessFlow.unlinkedRecord",
                              ),
                              versions: [nextVersion],
                            },
                            ...current,
                          ];
                    });
                    event.target.value = "";
                  }}
                />
                <Button>
                  {t("interfacePreview.businessFlow.addAttachmentVersion")}
                </Button>
              </label>
            }
          >
            <Table
              rowKey="id"
              pagination={false}
              dataSource={attachments}
              locale={{
                emptyText: t("interfacePreview.full.workspace.noAttachments"),
              }}
              columns={[
                {
                  title: t("interfacePreview.full.workspace.name"),
                  dataIndex: "name",
                },
                {
                  title: t("interfacePreview.full.workspace.records"),
                  dataIndex: "record",
                },
                {
                  title: t("interfacePreview.full.workspace.versions"),
                  key: "versions",
                  render: (_, row: BusinessAttachment) => row.versions.length,
                },
                {
                  title: t("interfacePreview.full.workspace.actions"),
                  key: "actions",
                  render: (_, row: BusinessAttachment) => (
                    <Button
                      type="link"
                      onClick={() => setSelectedAttachment(row)}
                    >
                      {t("interfacePreview.businessFlow.versionHistory")}
                    </Button>
                  ),
                },
              ]}
            />
          </Card>
          <Modal
            open={selectedAttachment !== null}
            title={selectedAttachment?.name}
            onCancel={() => setSelectedAttachment(null)}
            footer={
              <Button onClick={() => setSelectedAttachment(null)}>
                {t("common.close")}
              </Button>
            }
          >
            <List
              dataSource={selectedAttachment?.versions || []}
              renderItem={(version) => (
                <List.Item>
                  <Space direction="vertical">
                    <Typography.Text>
                      v{version.version} · {version.updated} · {version.size}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      {version.note}
                    </Typography.Text>
                  </Space>
                </List.Item>
              )}
            />
          </Modal>
          <DemoBoundary>
            {t("interfacePreview.full.workspace.attachmentLocalNotice")}
          </DemoBoundary>
        </>
      ),
    },
    {
      key: "history",
      label: t("interfacePreview.full.workspace.changeHistory"),
      children: (
        <Card
          title={t("interfacePreview.full.workspace.changeHistory")}
          extra={
            <Button
              onClick={() =>
                addHistory(
                  t("interfacePreview.full.workspace.manualHistoryEntry"),
                )
              }
            >
              {t("interfacePreview.full.workspace.addSampleHistory")}
            </Button>
          }
        >
          <List
            dataSource={history}
            renderItem={(item) => (
              <List.Item>
                {item}
                <Tag>{t("interfacePreview.full.workspace.sampleRecord")}</Tag>
              </List.Item>
            )}
          />
        </Card>
      ),
    },
    {
      key: "notifications",
      label: t("interfacePreview.full.workspace.reminders"),
      children: (
        <DemoCollection
          storageKey={`${key}.reminders`}
          title={t("interfacePreview.full.workspace.reminders")}
          initialRows={[
            {
              id: "reminder-contract",
              name: "合同到期前 30 天提醒",
              description: "关联对象：合同档案",
              enabled: true,
              values: { cadence: "每天 09:00", recipient: "合同负责人" },
            },
            {
              id: "reminder-inspection",
              name: "巡检异常跟进",
              description: "关联对象：设备台账",
              enabled: false,
              values: { cadence: "每周一", recipient: "运营团队" },
            },
          ]}
          fields={[
            {
              name: "name",
              label: t("interfacePreview.full.workspace.name"),
              required: true,
            },
            {
              name: "description",
              label: t("interfacePreview.full.workspace.description"),
              kind: "textarea",
            },
            {
              name: "cadence",
              label: t("interfacePreview.full.workspace.cadence"),
            },
            {
              name: "recipient",
              label: t("interfacePreview.full.workspace.recipient"),
            },
            {
              name: "enabled",
              label: t("interfacePreview.full.workspace.enabled"),
              kind: "switch",
            },
          ]}
          canToggle
          extra={
            <DemoBoundary>
              {t("interfacePreview.full.workspace.remindersLocalNotice")}
            </DemoBoundary>
          }
        />
      ),
    },
  ];
  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={(nextTab) => {
          setActiveTab(nextTab);
          navigate("/features", nextTab, featureId);
        }}
        items={tabItems}
      />
      <Modal
        title={
          editingObjectId
            ? t("interfacePreview.full.workspace.editObject")
            : t("interfacePreview.full.workspace.addObject")
        }
        open={objectEditorOpen}
        onCancel={() => setObjectEditorOpen(false)}
        onOk={() => objectForm.submit()}
        destroyOnHidden
      >
        <Form form={objectForm} layout="vertical" onFinish={saveObject}>
          <Form.Item
            name="name"
            label={t("interfacePreview.full.workspace.name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("interfacePreview.full.workspace.description")}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={
          editingFieldId
            ? t("interfacePreview.full.workspace.editField")
            : t("interfacePreview.full.workspace.addField")
        }
        open={fieldEditorOpen}
        onCancel={() => setFieldEditorOpen(false)}
        onOk={() => fieldForm.submit()}
        destroyOnHidden
      >
        <Form form={fieldForm} layout="vertical" onFinish={saveField}>
          <Form.Item
            name="label"
            label={t("interfacePreview.full.workspace.fieldName")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="type"
            label={t("interfacePreview.full.workspace.fieldType")}
          >
            <Select
              options={fieldTypes.map((value) => ({
                value,
                label: t(`interfacePreview.businessFlow.fieldTypes.${value}`),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="required"
            label={t("interfacePreview.full.workspace.required")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(previous, next) => previous.type !== next.type}
          >
            {({ getFieldValue }) =>
              ["select", "document", "record"].includes(
                getFieldValue("type"),
              ) ? (
                <Form.Item
                  name="options"
                  label={t("interfacePreview.full.workspace.options")}
                >
                  <Input.TextArea
                    placeholder={t(
                      "interfacePreview.full.workspace.optionsHint",
                    )}
                  />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={
          editingRecordId
            ? t("interfacePreview.full.workspace.editRecord")
            : t("interfacePreview.full.workspace.addRecord")
        }
        open={recordEditorOpen}
        onCancel={() => setRecordEditorOpen(false)}
        onOk={() => recordForm.submit()}
        destroyOnHidden
      >
        <Form form={recordForm} layout="vertical" onFinish={saveRecord}>
          <Form.Item
            label={t("interfacePreview.full.workspace.businessObject")}
          >
            <Select
              value={recordDraftObjectId}
              disabled={!!editingRecordId}
              onChange={(value) => {
                setRecordDraftObjectId(value);
                recordForm.resetFields();
              }}
              options={objects.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="name"
            label={t("interfacePreview.full.workspace.name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
          {recordObject?.fields.map((field) => (
            <Form.Item
              key={field.id}
              name={field.id}
              label={field.label}
              rules={
                field.required
                  ? [
                      {
                        required: true,
                        message: t(
                          "interfacePreview.full.workspace.requiredField",
                        ),
                      },
                    ]
                  : undefined
              }
            >
              {formControl(field, t)}
            </Form.Item>
          ))}
        </Form>
      </Modal>
      <Modal
        title={selectedRecord?.name}
        open={!!selectedRecord}
        onCancel={() => setSelectedRecord(null)}
        footer={
          <Button onClick={() => setSelectedRecord(null)}>
            {t("common.close")}
          </Button>
        }
      >
        <Descriptions
          bordered
          column={1}
          size="small"
          items={
            selectedRecord
              ? [
                  {
                    key: "object",
                    label: t("interfacePreview.full.workspace.businessObject"),
                    children: objects.find(
                      (item) => item.id === selectedRecord.objectId,
                    )?.name,
                  },
                  ...Object.entries(selectedRecord.values).map(
                    ([id, value]) => ({
                      key: id,
                      label:
                        objects
                          .find((item) => item.id === selectedRecord.objectId)
                          ?.fields.find((field) => field.id === id)?.label ||
                        id,
                      children: value,
                    }),
                  ),
                  {
                    key: "updated",
                    label: t("interfacePreview.full.workspace.updated"),
                    children: selectedRecord.updated,
                  },
                ]
              : []
          }
        />
      </Modal>
    </>
  );
}

import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Card,
  Modal,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import TextDocumentEditorModal, {
  type TextDocumentEditorValues,
} from "../../KnowledgeBases/TextDocumentEditorModal";
import type { DemoPageProps, DemoRow } from "./contract";
import {
  DemoBoundary,
  DemoCollection,
  DemoExternalAction,
  DemoSettings,
  useDemoState,
} from "./DemoKit";
import {
  DEFAULT_DEMO_BASE,
  INITIAL_DEMO_BASES,
  INITIAL_DEMO_NOTES,
} from "./knowledgeData";
import {
  DEMO_NOTIFICATION_KEY,
  initialDemoNotifications,
  type DemoNotification,
} from "./notifications";

const initialGrants: DemoRow[] = [
  {
    id: "grant-parent",
    name: "林工",
    description: "独立的示例授权",
    values: {
      baseId: DEFAULT_DEMO_BASE,
      resource: "demo-doc-01",
      permission: "write",
      delegate: true,
      parentGrant: "",
    },
  },
  {
    id: "grant-dependent",
    name: "陈工",
    description: "依赖林工的示例授权",
    values: {
      baseId: DEFAULT_DEMO_BASE,
      resource: "demo-doc-01",
      permission: "read",
      delegate: false,
      parentGrant: "grant-parent",
    },
  },
];

export function KnowledgePanels(
  props: DemoPageProps & { baseId: string; onSelectBase: (id: string) => void },
) {
  const { data, copy, actions, tab, baseId, onSelectBase } = props;
  const { t } = useTranslation();
  const tx = (key: string) => t(`interfacePreview.full.knowledge.${key}`);
  const [bases] = useDemoState<DemoRow[]>(
    "knowledge.bases",
    INITIAL_DEMO_BASES,
  );
  const [grants, setGrants] = useDemoState<DemoRow[]>(
    "knowledge.grants",
    initialGrants,
  );
  const [, setNotifications] = useDemoState<DemoNotification[]>(
    DEMO_NOTIFICATION_KEY,
    initialDemoNotifications(t),
  );
  const [showOutput, setShowOutput] = useState(false);
  const [notes, setNotes] = useDemoState<DemoRow[]>(
    "knowledge.notes",
    INITIAL_DEMO_NOTES,
  );
  const [noteEditorOpen, setNoteEditorOpen] = useState(false);
  const [connectionScenario, setConnectionScenario] = useState<
    "connected" | "failed" | "read_only"
  >("connected");
  const [connectionResult, setConnectionResult] = useState<
    "connected" | "failed" | "read_only" | null
  >(null);
  const [editingNote, setEditingNote] = useState<DemoRow | null>(null);
  const [delegating, setDelegating] = useState<DemoRow | null>(null);
  const [recipient, setRecipient] = useState(copy.sampleUser);
  const [permission, setPermission] = useState("read");
  const [delegate, setDelegate] = useState(false);
  const [notice, setNotice] = useState("");
  const scopes = [
    { value: "enterprise", label: copy.enterpriseScope },
    { value: "department", label: copy.departmentScope },
    { value: "personal", label: copy.personalScope },
  ];
  const docs = data.documents.filter((document) => document.baseId === baseId);
  const allowedDocs = docs
    .filter((document) => document.scope !== "personal")
    .map((document) => ({ value: document.id, label: document.name }));
  const notify = (rows: DemoRow[]) => {
    const mine = rows.filter(
      (row) => row.name === "陈工" || row.name === copy.sampleUser,
    );
    if (!mine.length) return;
    setNotifications((current) => [
      ...mine.map((row) => ({
        id: `notice-${crypto.randomUUID()}`,
        title: tx("revoked"),
        body: t("interfacePreview.full.knowledge.revokedBody", {
          name: row.name,
        }),
        read: false,
        path: "/knowledge-bases",
        tab: "permissions",
      })),
      ...current,
    ]);
  };
  const removeDependents = (parent: DemoRow, direct: boolean) => {
    const removedIds = new Set([parent.id]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const row of grants)
        if (
          row.values?.parentGrant &&
          removedIds.has(String(row.values.parentGrant)) &&
          !removedIds.has(row.id)
        ) {
          removedIds.add(row.id);
          changed = true;
        }
    }
    const removed = grants.filter(
      (row) => removedIds.has(row.id) && (direct || row.id !== parent.id),
    );
    setGrants((current) =>
      current.filter(
        (row) => !removedIds.has(row.id) || (!direct && row.id === parent.id),
      ),
    );
    notify(removed);
  };
  if (tab === "bases")
    return (
      <DemoCollection
        storageKey="knowledge.bases"
        title={tx("bases")}
        description={tx("baseHint")}
        initialRows={INITIAL_DEMO_BASES}
        fields={[
          {
            name: "name",
            label: t("interfacePreview.full.common.name"),
            required: true,
          },
          {
            name: "description",
            label: t("knowledgeBases.description"),
            kind: "textarea",
          },
          {
            name: "visibility",
            label: copy.permissionScope,
            kind: "select",
            options: scopes,
            initialValue: "enterprise",
          },
        ]}
        canToggle
        rowActions={[
          {
            key: "documents",
            label: copy.originalViewDocuments,
            onClick: (row) => onSelectBase(row.id),
          },
        ]}
      />
    );
  if (tab === "documents")
    return (
      <>
        <DemoBoundary>{tx("documentHint")}</DemoBoundary>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Button
            type="primary"
            onClick={() => {
              setEditingNote(null);
              setNoteEditorOpen(true);
            }}
          >
            {t("knowledgeBases.createFile")}
          </Button>
          {notes
            .filter((row) => row.values?.baseId === baseId)
            .map((row) => (
              <Card
                key={row.id}
                title={row.name}
                extra={
                  <Button
                    onClick={() => {
                      setEditingNote(row);
                      setNoteEditorOpen(true);
                    }}
                  >
                    {t("common.edit")}
                  </Button>
                }
              >
                <Typography.Paragraph type="secondary">
                  {String(row.values?.folder || tx("folder"))}
                </Typography.Paragraph>
                <Typography.Paragraph ellipsis={{ rows: 3 }}>
                  {String(row.values?.content || "")}
                </Typography.Paragraph>
              </Card>
            ))}
          {notes.filter((row) => row.values?.baseId === baseId).length ===
            0 && <Alert type="info" message={tx("documentHint")} />}
        </Space>
        <TextDocumentEditorModal
          open={noteEditorOpen}
          mode={editingNote ? "edit" : "create"}
          initialName={editingNote?.name}
          initialFormat={
            String(
              editingNote?.values?.format ||
                (editingNote?.name.endsWith(".txt") ? "txt" : "md"),
            ) as "md" | "txt"
          }
          initialContent={String(editingNote?.values?.content || "")}
          onCancel={() => setNoteEditorOpen(false)}
          onSubmit={async (values: TextDocumentEditorValues) => {
            if (editingNote) {
              setNotes((current) =>
                current.map((row) =>
                  row.id === editingNote.id
                    ? {
                        ...row,
                        name: values.name,
                        values: {
                          ...row.values,
                          content: values.content,
                          format: values.format,
                        },
                      }
                    : row,
                ),
              );
            } else {
              setNotes((current) => [
                {
                  id: `note-${crypto.randomUUID()}`,
                  name: values.name,
                  description: t("interfacePreview.businessFlow.localNote"),
                  values: {
                    baseId,
                    folder: "示例资料",
                    content: values.content,
                    format: values.format,
                    scope: "enterprise",
                  },
                },
                ...current,
              ]);
            }
            setNoteEditorOpen(false);
          }}
        />
      </>
    );
  if (tab === "sources")
    return (
      <>
        <Alert
          type="info"
          showIcon
          message={t("interfacePreview.businessFlow.connectionFixtureOnly")}
          description={t(
            "interfacePreview.businessFlow.connectionNotAttempted",
          )}
        />
        <Space wrap>
          <Select
            aria-label={t("interfacePreview.businessFlow.connectionResult")}
            value={connectionScenario}
            onChange={setConnectionScenario}
            options={["connected", "failed", "read_only"].map((value) => ({
              value,
              label: t(
                `interfacePreview.businessFlow.connectionStates.${value}`,
              ),
            }))}
          />
          <Button onClick={() => setConnectionResult(connectionScenario)}>
            {t("interfacePreview.businessFlow.showConnectionFixture")}
          </Button>
        </Space>
        {connectionResult && (
          <Alert
            type={connectionResult === "connected" ? "success" : "warning"}
            showIcon
            message={t(
              `interfacePreview.businessFlow.connectionStates.${connectionResult}`,
            )}
            description={t(
              "interfacePreview.businessFlow.connectionFixtureOnly",
            )}
          />
        )}
        <DemoCollection
          storageKey={`knowledge.sources.${baseId}`}
          title={tx("sources")}
          initialRows={
            baseId === DEFAULT_DEMO_BASE
              ? [
                  {
                    id: "source-share",
                    name: "企业共享资料",
                    description: "未连接实际目录的SMB示例",
                    enabled: true,
                    values: {
                      type: "smb",
                      root: "\\\\demo-server\\质量资料",
                      upload: false,
                      interval: 3600,
                      credential: "",
                    },
                  },
                ]
              : []
          }
          fields={[
            {
              name: "name",
              label: t("interfacePreview.full.common.name"),
              required: true,
            },
            {
              name: "type",
              label: tx("sourceType"),
              kind: "select",
              options: [
                { value: "local", label: tx("localSource") },
                { value: "smb", label: "SMB" },
                { value: "nfs", label: "NFS" },
                {
                  value: "url",
                  label: t("interfacePreview.businessFlow.urlSource"),
                },
                {
                  value: "connector",
                  label: t("interfacePreview.businessFlow.connectorSource"),
                },
              ],
              initialValue: "local",
            },
            { name: "root", label: tx("sourceRoot"), required: true },
            { name: "upload", label: tx("allowUpload"), kind: "switch" },
            {
              name: "interval",
              label: tx("scanInterval"),
              kind: "number",
              initialValue: 3600,
            },
            { name: "credential", label: tx("credential"), kind: "password" },
            {
              name: "include",
              label: t("interfacePreview.businessFlow.includePatterns"),
              kind: "textarea",
            },
            {
              name: "exclude",
              label: t("interfacePreview.businessFlow.excludePatterns"),
              kind: "textarea",
            },
          ]}
          canToggle
        />
        <Space wrap>
          <DemoExternalAction label={tx("scan")} />
          <DemoExternalAction label={tx("reindex")} />
        </Space>
        <DemoCollection
          storageKey={`knowledge.runs.${baseId}`}
          title={tx("scanRuns")}
          initialRows={[
            {
              id: "scan-example",
              name: "2026-10-25 09:00",
              values: {
                trigger: "scheduled",
                state: "示例记录（未在本页执行）",
                result: "6 份资料，其中 1 份仅存档",
              },
            },
          ]}
          fields={[
            { name: "name", label: copy.updated },
            { name: "trigger", label: tx("trigger") },
            { name: "state", label: tx("state") },
            { name: "result", label: tx("result") },
          ]}
          canCreate={false}
          canEdit={false}
          canDelete={false}
        />
      </>
    );
  if (tab === "collections")
    return (
      <>
        <DemoCollection
          storageKey={`knowledge.collections.${baseId}`}
          title={tx("collections")}
          initialRows={[
            {
              id: "collection-equipment",
              name: "设备检定专题",
              description: "按设备编号归纳规程、证书与台账",
              values: {
                prompt: "按设备编号整理资料，保留原文引用，缺失项明确标记。",
                scope: "全部已授权资料",
              },
            },
          ]}
          fields={[
            {
              name: "name",
              label: t("interfacePreview.full.common.name"),
              required: true,
            },
            {
              name: "prompt",
              label: tx("prompt"),
              kind: "textarea",
              required: true,
            },
            { name: "scope", label: tx("scope") },
          ]}
          rowActions={[
            {
              key: "output",
              label: tx("outputs"),
              onClick: () => setShowOutput(true),
            },
          ]}
        />
        <DemoExternalAction label={tx("organize")} />
        <Modal
          open={showOutput}
          title={tx("outputs")}
          onCancel={() => setShowOutput(false)}
          footer={
            <Button onClick={() => setShowOutput(false)}>{copy.close}</Button>
          }
        >
          <TagOutput text={tx("topicOutput")} />
          <Space wrap>
            {docs.slice(0, 2).map((document) => (
              <Button
                key={document.id}
                onClick={() => actions.openDocument(document)}
              >
                {document.name}
              </Button>
            ))}
          </Space>
        </Modal>
      </>
    );
  if (tab === "templates")
    return (
      <>
        <DemoCollection
          storageKey={`knowledge.templates.${baseId}`}
          title={tx("templates")}
          initialRows={[
            {
              id: "extract-certificate",
              name: "检定证书字段",
              values: {
                prompt: "提取证书编号、设备编号、检定日期，附带原文定位。",
                schema:
                  "certificate_no: string\nequipment_no: string\nissued_on: date",
              },
            },
          ]}
          fields={[
            {
              name: "name",
              label: t("interfacePreview.full.common.name"),
              required: true,
            },
            { name: "prompt", label: tx("prompt"), kind: "textarea" },
            { name: "schema", label: tx("schema"), kind: "textarea" },
          ]}
        />
        <DemoExternalAction label={tx("extract")} />
      </>
    );
  if (tab === "settings")
    return (
      <DemoSettings
        storageKey={`knowledge.settings.${baseId}`}
        title={tx("settings")}
        fields={[
          {
            name: "enabled",
            label: tx("enabled"),
            kind: "switch",
            initialValue: true,
          },
          { name: "vector", label: tx("vector"), kind: "switch" },
          { name: "ocr", label: tx("ocr"), kind: "switch", initialValue: true },
          {
            name: "maxDocuments",
            label: tx("maxDocuments"),
            kind: "number",
            initialValue: 1000,
          },
          {
            name: "chunkSize",
            label: tx("chunkSize"),
            kind: "number",
            initialValue: 1000,
          },
          {
            name: "processor",
            label: tx("processor"),
            kind: "select",
            options: [
              { value: "auto", label: tx("automatic") },
              { value: "text", label: tx("textOnly") },
            ],
            initialValue: "auto",
          },
        ]}
      />
    );
  if (tab === "permissions")
    return (
      <>
        <Alert
          type="info"
          showIcon
          message={copy.permissionNote}
          description={tx("grantHint")}
        />
        <DemoSettings
          storageKey={`knowledge.policy.${baseId}`}
          title={tx("permissionPolicy")}
          fields={[
            {
              name: "scope",
              label: tx("defaultScope"),
              kind: "select",
              options: scopes,
              initialValue: "enterprise",
            },
          ]}
        />
        {notice && <Alert type="warning" showIcon message={notice} />}
        <DemoCollection
          storageKey="knowledge.grants"
          title={tx("grantTitle")}
          initialRows={initialGrants}
          rowFilter={(row) => row.values?.baseId === baseId}
          fields={[
            {
              name: "name",
              label: tx("grantee"),
              kind: "select",
              options: [copy.sampleUser, "林工", "赵工"].map((value) => ({
                value,
                label: value,
              })),
              required: true,
            },
            {
              name: "baseId",
              label: tx("bases"),
              kind: "select",
              options: [
                {
                  value: baseId,
                  label:
                    bases.find((base) => base.id === baseId)?.name || baseId,
                },
              ],
              initialValue: baseId,
              required: true,
            },
            {
              name: "resource",
              label: tx("resource"),
              kind: "select",
              options: allowedDocs,
              required: true,
            },
            {
              name: "permission",
              label: tx("permission"),
              kind: "select",
              options: [
                { value: "read", label: tx("read") },
                { value: "write", label: tx("write") },
              ],
              initialValue: "write",
            },
            { name: "delegate", label: tx("delegate"), kind: "switch" },
          ]}
          onDeleted={(row) => removeDependents(row, true)}
          onSaved={(row, previous) => {
            if (previous?.values?.delegate && !row.values?.delegate)
              removeDependents(row, false);
          }}
          rowActions={[
            {
              key: "delegate",
              label: tx("delegateAction"),
              onClick: (row) => {
                if (!row.values?.delegate) {
                  setNotice(tx("delegationDenied"));
                  return;
                }
                setNotice("");
                setDelegating(row);
                setRecipient(copy.sampleUser);
                setPermission(
                  row.values.permission === "write" ? "write" : "read",
                );
                setDelegate(false);
              },
            },
          ]}
        />
        <Typography.Paragraph type="secondary">
          {copy.originalPrivateHint}
        </Typography.Paragraph>
        <Button onClick={() => actions.openImport(baseId)}>
          {copy.originalTryUpload}
        </Button>
        <Modal
          open={delegating !== null}
          title={tx("delegateAction")}
          onCancel={() => setDelegating(null)}
          onOk={() => {
            if (!delegating) return;
            setGrants((current) => [
              ...current,
              {
                id: `grant-${crypto.randomUUID()}`,
                name: recipient,
                description: tx("dependentGrant"),
                values: {
                  baseId,
                  resource: delegating.values?.resource || "",
                  permission,
                  delegate,
                  parentGrant: delegating.id,
                },
              },
            ]);
            setDelegating(null);
          }}
          okText={t("common.save")}
          cancelText={t("common.cancel")}
        >
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>{tx("grantee")}</Typography.Text>
            <Select
              style={{ width: "100%" }}
              value={recipient}
              onChange={setRecipient}
              options={[copy.sampleUser, "林工", "赵工"].map((value) => ({
                value,
                label: value,
              }))}
            />
            <Typography.Text>{tx("permission")}</Typography.Text>
            <Select
              style={{ width: "100%" }}
              value={permission}
              onChange={setPermission}
              options={[
                { value: "read", label: tx("read") },
                ...(delegating?.values?.permission === "write"
                  ? [{ value: "write", label: tx("write") }]
                  : []),
              ]}
            />
            <Space>
              <Switch checked={delegate} onChange={setDelegate} />
              {tx("delegate")}
            </Space>
            <Alert type="info" message={copy.permissionNote} />
          </Space>
        </Modal>
      </>
    );
  return null;
}
function TagOutput({ text }: { text: string }) {
  return <Typography.Paragraph>{text}</Typography.Paragraph>;
}

import { useState } from "react";
import KnowledgeProcessorNotice from "../../KnowledgeBases/components/KnowledgeProcessorNotice";
import {
  Alert,
  Badge,
  Button,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  type TableColumnsType,
} from "antd";
import { useTranslation } from "react-i18next";
import { Database, Download, Search, ShieldCheck, Upload } from "lucide-react";
import PageShell from "../../../layouts/PageShell";
import knowledgeStyles from "../../KnowledgeBases/index.module.less";
import { DocumentTypeIcon, scopeLabel } from "../PreviewShared";
import type {
  PreviewDocument,
  PreviewDocumentStatus,
  PreviewSource,
} from "../model";
import type { DemoPageProps } from "./contract";
import { useDemoState } from "./DemoKit";
import { KnowledgePanels } from "./KnowledgePanels";
import { DEFAULT_DEMO_BASE, INITIAL_DEMO_BASES } from "./knowledgeData";
import type { DemoRow } from "./contract";
import styles from "../variants/OriginalPreview.module.less";
import { registerPreviewApi } from "./previewTransport";

let processorNoticeScenario: "ready" | "failed" = "ready";
registerPreviewApi("business-knowledge-processors", (request) => {
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    url.pathname !== "/api/knowledge-bases/processors"
  )
    return undefined;
  if (processorNoticeScenario === "failed")
    return Response.json(
      { error: { code: "PREVIEW_FIXTURE_FAILURE" } },
      { status: 503 },
    );
  return Response.json({
    archive_only: true,
    processors: [
      {
        id: "local-text",
        available: true,
        extensions: [".pdf", ".md", ".txt"],
        reason: null,
      },
    ],
  });
});

const { Text, Paragraph, Title } = Typography;
function KnowledgeContent(props: DemoPageProps) {
  const { data, copy, actions, navigate, item } = props;
  const { t } = useTranslation();
  const tx = (key: string) => t(`interfacePreview.full.knowledge.${key}`);
  const [bases] = useDemoState<DemoRow[]>(
    "knowledge.bases",
    INITIAL_DEMO_BASES,
  );
  const [selectedBase, setSelectedBase] = useDemoState(
    "knowledge.selectedBase",
    DEFAULT_DEMO_BASE,
  );
  const base =
    bases.find((base) => base.id === (item || selectedBase)) || bases[0];
  const baseId = base?.id || "";
  const libraryDocs = data.documents.filter(
    (document) => document.baseId === baseId,
  );
  const [source, setSource] = useDemoState<"all" | PreviewSource>(
    `knowledge.filter.source.${baseId}`,
    "all",
  );
  const [query, setQuery] = useDemoState(
    `knowledge.filter.query.${baseId}`,
    "",
  );
  const [status, setStatus] = useDemoState<"all" | PreviewDocumentStatus>(
    `knowledge.filter.status.${baseId}`,
    "all",
  );
  const [indexScenario, setIndexScenario] = useState<
    | "pending"
    | "processing"
    | "ready"
    | "failed"
    | "discovered"
    | "unsupported"
    | "archive_only"
    | "password_required"
  >("processing");
  const [processorScenario, setProcessorScenario] = useState<
    "ready" | "failed"
  >(processorNoticeScenario);
  const [processorFiles] = useState(() => [
    new File(["local fixture"], "guide.md", { type: "text/markdown" }),
    new File(["local fixture"], "drawing.dwg", {
      type: "application/octet-stream",
    }),
  ]);
  const tab = props.tab || "files";
  const setTab = (next: string) =>
    navigate("/knowledge-bases", next, baseId || undefined);
  const selectBase = (id: string) => {
    setSelectedBase(id);
    navigate("/knowledge-bases", "files", id);
  };
  const sourceOptions = [
    { value: "all" as const, label: copy.allDocuments },
    { value: "platform" as const, label: copy.platformSource },
    { value: "folder" as const, label: copy.folderSource },
  ];
  const filtered = libraryDocs.filter(
    (document) =>
      (source === "all" || document.source === source) &&
      (status === "all" || document.status === status) &&
      `${document.name} ${document.owner} ${document.kind} ${document.excerpt}`
        .toLocaleLowerCase()
        .includes(query.trim().toLocaleLowerCase()),
  );
  const statusLabel = (value: PreviewDocumentStatus) =>
    value === "ready"
      ? copy.ready
      : value === "archive_only"
      ? copy.archived
      : copy.processing;
  const columns: TableColumnsType<PreviewDocument> = [
    {
      title: copy.originalFileName,
      key: "name",
      width: 290,
      render: (_, document) => (
        <div className={styles.fileName}>
          <DocumentTypeIcon kind={document.kind} />
          <Button type="link" onClick={() => actions.openDocument(document)}>
            {document.name}
          </Button>
        </div>
      ),
    },
    {
      title: copy.sources,
      dataIndex: "source",
      width: 140,
      render: (value) => (
        <Tag>
          {value === "platform" ? copy.platformSource : copy.folderSource}
        </Tag>
      ),
    },
    {
      title: copy.permissionScope,
      key: "scope",
      width: 110,
      render: (_, document) => (
        <Tag
          color={
            document.scope === "enterprise"
              ? "green"
              : document.scope === "department"
              ? "blue"
              : undefined
          }
        >
          {scopeLabel(document.scope, copy)}
        </Tag>
      ),
    },
    {
      title: copy.status,
      dataIndex: "status",
      width: 140,
      render: (value: PreviewDocumentStatus) => (
        <Badge
          status={
            value === "ready"
              ? "success"
              : value === "processing"
              ? "processing"
              : "default"
          }
          text={statusLabel(value)}
        />
      ),
    },
    { title: copy.owner, dataIndex: "owner", width: 120 },
    { title: copy.updated, dataIndex: "updatedLabel", width: 160 },
    {
      title: copy.actions,
      key: "actions",
      width: 90,
      render: (_, document) => (
        <Button
          type="link"
          onClick={() => actions.openDocument(document)}
          aria-label={`${copy.preview} ${document.name}`}
        >
          {copy.preview}
        </Button>
      ),
    },
  ];
  return (
    <PageShell
      title={t("nav.knowledgeBases")}
      subtitle={copy.originalKnowledgeSubtitle}
    >
      <div className={styles.changeNote}>
        <ShieldCheck size={15} />
        <Text type="secondary">{copy.originalKnowledgeChange}</Text>
      </div>
      <div className={`${knowledgeStyles.layout} ${styles.knowledgeLayout}`}>
        <aside
          className={`${knowledgeStyles.baseList} ${styles.sourceList}`}
          aria-label={copy.sourceHealth}
        >
          <div className={styles.baseControls}>
            <Select
              aria-label={tx("bases")}
              value={base?.id}
              options={bases.map((base) => ({
                value: base.id,
                label: base.name,
              }))}
              onChange={selectBase}
            />
            <Button onClick={() => setTab("bases")}>{tx("manageBases")}</Button>
          </div>
          <div
            className={`${knowledgeStyles.listPanelHeader} ${styles.sourceHeader}`}
          >
            <span className={knowledgeStyles.listPanelTitle}>
              {copy.sourceHealth}
            </span>
          </div>
          {sourceOptions.map((option) => (
            <button
              key={option.value}
              aria-pressed={source === option.value}
              className={`${knowledgeStyles.listItem} ${styles.sourceItem} ${
                source === option.value ? knowledgeStyles.active : ""
              }`}
              onClick={() => {
                setSource(option.value);
                setTab("files");
              }}
            >
              <span className={knowledgeStyles.listName}>
                <Database size={17} />
                {option.label}
              </span>
              <span className={styles.sourceCount}>
                {
                  libraryDocs.filter(
                    (document) =>
                      option.value === "all" ||
                      document.source === option.value,
                  ).length
                }{" "}
                {copy.documents}
              </span>
            </button>
          ))}
          <Text type="secondary" className={styles.sourceHint}>
            {copy.originalSourceHint}
          </Text>
        </aside>
        <div className={styles.knowledgeDetail}>
          <div className={styles.sectionHeading}>
            <div>
              <Title level={5}>{base?.name || tx("bases")}</Title>
              <Text type="secondary">
                {libraryDocs.length} {copy.documents} ·{" "}
                {
                  libraryDocs.filter((document) => document.status === "ready")
                    .length
                }{" "}
                {copy.ready}
              </Text>
            </div>
            <Space wrap>
              <Button
                icon={<Download size={15} />}
                onClick={() => actions.exportDocuments(libraryDocs)}
              >
                {copy.exportList}
              </Button>
              <Button
                type="primary"
                icon={<Upload size={15} />}
                onClick={() => actions.openImport(baseId)}
                disabled={!base}
              >
                {copy.importDocuments}
              </Button>
            </Space>
          </div>
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              {
                key: "files",
                label: copy.originalFiles,
                children: (
                  <>
                    <div className={styles.tools}>
                      <Input
                        allowClear
                        prefix={<Search size={15} />}
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={copy.searchKnowledge}
                        aria-label={copy.searchKnowledge}
                      />
                      <Select
                        aria-label={copy.filterStatus}
                        value={status}
                        onChange={setStatus}
                        options={[
                          { value: "all", label: copy.allDocuments },
                          { value: "ready", label: copy.ready },
                          { value: "archive_only", label: copy.archived },
                          { value: "processing", label: copy.processing },
                        ]}
                      />
                      <Text type="secondary">
                        {filtered.length} {copy.documents}
                      </Text>
                    </div>
                    <Alert
                      type="info"
                      showIcon
                      message={t(
                        "interfacePreview.businessFlow.processorFixtureOnly",
                      )}
                    />
                    <Select
                      aria-label={t(
                        "interfacePreview.businessFlow.processorScenario",
                      )}
                      value={processorScenario}
                      onChange={(value: "ready" | "failed") => {
                        processorNoticeScenario = value;
                        setProcessorScenario(value);
                      }}
                      options={[
                        {
                          value: "ready",
                          label: t(
                            "interfacePreview.businessFlow.processorReady",
                          ),
                        },
                        {
                          value: "failed",
                          label: t(
                            "interfacePreview.businessFlow.processorFailed",
                          ),
                        },
                      ]}
                    />
                    <KnowledgeProcessorNotice
                      key={processorScenario}
                      files={processorFiles}
                    />
                    <Space
                      direction="vertical"
                      style={{ width: "100%", marginBottom: 12 }}
                    >
                      <Select
                        aria-label={t(
                          "interfacePreview.businessFlow.indexingScenario",
                        )}
                        value={indexScenario}
                        onChange={setIndexScenario}
                        options={[
                          "pending",
                          "processing",
                          "ready",
                          "failed",
                          "discovered",
                          "unsupported",
                          "archive_only",
                          "password_required",
                        ].map((value) => ({
                          value,
                          label: t(
                            `interfacePreview.businessFlow.indexState.${value}`,
                          ),
                        }))}
                      />
                      <Alert
                        type={
                          [
                            "failed",
                            "unsupported",
                            "password_required",
                          ].includes(indexScenario)
                            ? "warning"
                            : indexScenario === "ready"
                            ? "success"
                            : "info"
                        }
                        showIcon
                        message={t(
                          `interfacePreview.businessFlow.indexState.${indexScenario}`,
                        )}
                        description={t(
                          `interfacePreview.businessFlow.indexState.${indexScenario}Detail`,
                        )}
                        action={
                          indexScenario === "failed" ? (
                            <Button
                              size="small"
                              onClick={() => setIndexScenario("processing")}
                            >
                              {t("interfacePreview.businessFlow.retryLocally")}
                            </Button>
                          ) : undefined
                        }
                      />
                    </Space>
                    <Table
                      rowKey="id"
                      columns={columns}
                      dataSource={filtered}
                      pagination={false}
                      scroll={{ x: 1050 }}
                      locale={{ emptyText: copy.noResults }}
                    />
                    <Paragraph type="secondary" className={styles.footnote}>
                      {copy.originalArchiveHint}
                    </Paragraph>
                  </>
                ),
              },
              {
                key: "sources",
                label: copy.originalSourceManagement,
                children: (
                  <KnowledgePanels
                    {...props}
                    tab="sources"
                    baseId={baseId}
                    onSelectBase={selectBase}
                  />
                ),
              },
              {
                key: "permissions",
                label: copy.originalPermissionNotes,
                children: (
                  <KnowledgePanels
                    {...props}
                    tab="permissions"
                    baseId={baseId}
                    onSelectBase={selectBase}
                  />
                ),
              },
              ...[
                "bases",
                "documents",
                "collections",
                "templates",
                "settings",
              ].map((key) => ({
                key,
                label: tx(key),
                children: (
                  <KnowledgePanels
                    {...props}
                    tab={key}
                    baseId={baseId}
                    onSelectBase={selectBase}
                  />
                ),
              })),
            ]}
          />
        </div>
      </div>
    </PageShell>
  );
}

export default KnowledgeContent;

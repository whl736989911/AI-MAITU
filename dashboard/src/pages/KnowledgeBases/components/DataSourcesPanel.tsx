import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  FileUp,
  FolderOpen,
  Link2,
  Plus,
  PlugZap,
  RefreshCw,
  Server,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ResizableTable } from "../../../components/ResizableTable";
import { EmptyState } from "../../../components/EmptyState";
import {
  FOLDER_KINDS,
  dataSourcesApi,
  type DataSource,
  type DataSourceFolder,
  type DataSourceKind,
  type DataSourceSyncStatus,
} from "../../../api/modules/dataSources";
import {
  connectorsApi,
  type ConnectorInstance,
} from "../../../api/modules/connectors";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import { createDetailRequestGate } from "../../../utils/detailRequestGate";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { PERM, userCanKey } from "../../../utils/permissions";
import styles from "./DataSourcesPanel.module.less";

interface DataSourcesPanelProps {
  /** Knowledge base whose sources this panel lists. */
  baseId: string;
  /**
   * Page-computed write access to the base (owner or admin). Mirrors the
   * service-side ``get_writable_base`` check that guards create/sync/delete.
   */
  canWriteBase: boolean;
  /** Called after an ingest changed the base, so the page can reload documents. */
  onDocumentsChanged?: () => void;
}

type CreateFormValues = {
  name: string;
  kind: DataSourceKind;
  url?: string;
  server?: string;
  share?: string;
  rootPath?: string;
  username?: string;
  password?: string;
  scanIntervalSeconds?: number;
  includeGlobs?: string;
  excludeGlobs?: string;
};

/**
 * The folder settings a create/edit form describes.
 *
 * ``read_only`` is fixed on: the platform never writes to a source, so a switch
 * for it would be a setting nothing honours.
 */
function folderBody(values: CreateFormValues): DataSourceFolder {
  return {
    root_path: (values.rootPath ?? "").trim(),
    server: (values.server ?? "").trim(),
    share: (values.share ?? "").trim(),
    username: (values.username ?? "").trim(),
    // An empty string clears a stored secret; on create it simply means none.
    password: values.password ?? "",
    read_only: true,
    include_globs: (values.includeGlobs ?? "").trim(),
    exclude_globs: (values.excludeGlobs ?? "").trim(),
    scan_interval_seconds: values.scanIntervalSeconds ?? 0,
  };
}

/**
 * Kinds whose sync ingests content: ``upload`` replays a document of the base,
 * ``url`` fetches the page the source points at, and the folder kinds scan the
 * folder they name.
 *
 * ``connector`` is deliberately ``false`` — a connector instance carries
 * credentials for an MCP server, and the product has no way to pull documents
 * through one. The create form does not offer it either (see ``kindOptions``);
 * existing connector sources still list, and still report that they cannot sync.
 *
 * ``nfs`` is ``false`` for the same reason and cannot even be created: this
 * build has no NFS connector, so the backend refuses one with a reason instead
 * of storing a source that could never run.
 */
const SYNCABLE_KIND: Record<DataSourceKind, boolean> = {
  upload: true,
  url: true,
  connector: false,
  local: true,
  smb: true,
  nfs: false,
};
const DEFAULT_KIND: DataSourceKind = "upload";

function syncStatusColor(status: DataSourceSyncStatus) {
  if (status === "ok") return "success";
  if (status === "failed") return "error";
  if (status === "running") return "processing";
  return "default";
}

function connectionColor(status: DataSource["connection_status"]) {
  if (status === "ok") return "success";
  if (status === "failed") return "error";
  return "default";
}

/** What the name column shows beneath a source's name. */
function sourceDetail(
  source: DataSource,
  connectorName: (id: string) => string,
): string | undefined {
  if (source.kind === "upload") return source.config.path;
  if (source.kind === "url") return source.config.url;
  if (source.kind === "local") return source.root_path;
  if (source.kind === "smb") {
    return `\\\\${source.server}\\${source.share}\\${source.root_path}`;
  }
  const connectorId = source.config.connector_id;
  return connectorId ? connectorName(String(connectorId)) : undefined;
}

export default function DataSourcesPanel({
  baseId,
  canWriteBase,
  onDocumentsChanged,
}: DataSourcesPanelProps) {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const user = useCurrentUser();
  const timeZone = useServerTimezone();
  const [form] = Form.useForm<CreateFormValues>();

  const [sources, setSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [connectors, setConnectors] = useState<ConnectorInstance[] | null>(
    null,
  );
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const requestGate = useRef(createDetailRequestGate());

  const selectedKind = Form.useWatch("kind", form) ?? DEFAULT_KIND;
  // Both backend gates for a write: the module key (``require_permission``) and
  // write access to the base itself.
  const canWrite = canWriteBase && userCanKey(user, PERM.knowledgeBases);
  // Connector instances are only read to label a legacy connector row.
  const needsConnectorNames = sources.some(
    (source) => source.kind === "connector",
  );

  const load = useCallback(
    async (options?: { silent?: boolean }) => {
      const requestId = requestGate.current.begin();
      if (!options?.silent) setLoading(true);
      try {
        const rows = await dataSourcesApi.list(baseId);
        if (!requestGate.current.isCurrent(requestId)) return;
        setSources(rows);
        setLoadError(false);
      } catch (error) {
        if (!requestGate.current.isCurrent(requestId)) return;
        setLoadError(true);
        message.error(
          apiErrorMessage(error, t("knowledgeBases.dataSources.loadFailed"), t),
        );
      } finally {
        if (!options?.silent && requestGate.current.isCurrent(requestId)) {
          setLoading(false);
        }
      }
    },
    [baseId, t],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const loadConnectors = useCallback(async () => {
    try {
      setConnectors(await connectorsApi.listInstances());
    } catch (error) {
      setConnectors([]);
      message.error(
        apiErrorMessage(
          error,
          t("knowledgeBases.dataSources.connectorLoadFailed"),
          t,
        ),
      );
    }
  }, [t]);

  const connectorName = useCallback(
    (connectorId: string) =>
      connectors?.find((entry) => entry.instance_id === connectorId)
        ?.display_name || connectorId,
    [connectors],
  );

  useEffect(() => {
    // Only a connector source needs instance names; nothing else pays for it.
    if (needsConnectorNames && connectors === null) void loadConnectors();
  }, [connectors, loadConnectors, needsConnectorNames]);

  const openCreate = () => {
    form.resetFields();
    setFile(null);
    setCreateOpen(true);
  };

  const create = async () => {
    const values = await form.validateFields();
    if (values.kind === "upload" && !file) {
      message.warning(t("knowledgeBases.dataSources.fileRequired"));
      return;
    }
    setSubmitting(true);
    try {
      if (values.kind === "upload" && file) {
        // An upload source ingests a document of this base: put the file there
        // first, then register the source that points at it.
        const document = await knowledgeBasesApi.uploadDocument(
          baseId,
          file,
          file.name,
        );
        onDocumentsChanged?.();
        try {
          await dataSourcesApi.create(baseId, {
            name: values.name.trim(),
            kind: "upload",
            config: { document_id: document.id },
          });
        } catch (error) {
          // The file is already in the base — say so instead of implying both
          // halves failed.
          message.error(
            apiErrorMessage(
              error,
              t("knowledgeBases.dataSources.createAfterUploadFailed"),
              t,
            ),
          );
          setCreateOpen(false);
          await load({ silent: true });
          return;
        }
      } else if (FOLDER_KINDS.includes(values.kind)) {
        // A folder source stores where it is and how to reach it; the sync
        // scans it. The backend validates by building the connector, so a path
        // it refuses fails here rather than on the first scan.
        await dataSourcesApi.create(baseId, {
          name: values.name.trim(),
          kind: values.kind,
          folder: folderBody(values),
        });
      } else {
        // A link source has nothing to store here: the sync fetches the URL.
        await dataSourcesApi.create(baseId, {
          name: values.name.trim(),
          kind: "url",
          config: { url: (values.url ?? "").trim() },
        });
      }
      setCreateOpen(false);
      await load({ silent: true });
      message.success(t("knowledgeBases.dataSources.created"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.dataSources.createFailed"), t),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const sync = async (source: DataSource) => {
    setSyncingId(source.id);
    try {
      await dataSourcesApi.sync(source.id);
      if (FOLDER_KINDS.includes(source.kind)) {
        // A scan's outcome is its counts, not a bare "done": a run that found
        // files it is still waiting on is not the same as one that indexed them.
        const [run] = await dataSourcesApi.runs(source.id, 1);
        message.success(
          run
            ? t("knowledgeBases.dataSources.scanDone", {
                name: source.name,
                added: run.added,
                updated: run.updated,
                removed: run.removed,
                deferred: run.deferred,
                failed: run.failed,
              })
            : t("knowledgeBases.dataSources.syncDone", { name: source.name }),
        );
      } else {
        message.success(
          t("knowledgeBases.dataSources.syncDone", { name: source.name }),
        );
      }
      onDocumentsChanged?.();
    } catch (error) {
      // The backend refuses unimplemented kinds with a dedicated code; that is
      // "not yet", not "your ingest blew up".
      if (parseApiError(error)?.code === "DATA_SOURCE_SYNC_UNSUPPORTED") {
        message.warning(t("knowledgeBases.dataSources.syncUnsupported"));
      } else {
        message.error(
          apiErrorMessage(error, t("knowledgeBases.dataSources.syncFailed"), t),
        );
      }
    } finally {
      // A failed attempt is recorded on the row; show the outcome either way.
      await load({ silent: true });
      setSyncingId(null);
    }
  };

  const testConnection = async (source: DataSource) => {
    setTestingId(source.id);
    try {
      const result = await dataSourcesApi.test(source.id);
      message.success(
        t("knowledgeBases.dataSources.testOk", { detail: result.detail }),
      );
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.dataSources.testFailed"), t),
      );
    } finally {
      // The verdict is stored on the row either way, so show what it now says.
      await load({ silent: true });
      setTestingId(null);
    }
  };

  const remove = async (source: DataSource) => {
    setDeletingId(source.id);
    try {
      await dataSourcesApi.remove(source.id);
      message.success(t("knowledgeBases.dataSources.deleted"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.dataSources.deleteFailed"), t),
      );
    } finally {
      await load({ silent: true });
      setDeletingId(null);
    }
  };

  const confirmDelete = (source: DataSource) => {
    modal.confirm({
      title: t("knowledgeBases.dataSources.deleteTitle", { name: source.name }),
      content: t("knowledgeBases.dataSources.deleteBody"),
      okText: t("common.delete"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger: true },
      onOk: () => remove(source),
    });
  };

  const kindOptions = useMemo(
    () => [
      {
        value: "upload",
        label: (
          <span className={styles.kindOption}>
            <FileUp size={13} strokeWidth={1.8} />
            {t("knowledgeBases.dataSources.kinds.upload")}
          </span>
        ),
      },
      {
        value: "url",
        label: (
          <span className={styles.kindOption}>
            <Link2 size={13} strokeWidth={1.8} />
            {t("knowledgeBases.dataSources.kinds.url")}
          </span>
        ),
      },
      // No ``connector`` entry: a connector instance holds credentials for an
      // MCP server and the product cannot pull documents through one, so
      // offering it here would promise an ingest that cannot run.
      // No ``nfs`` entry either: this build has no NFS connector, so the
      // backend would refuse the source the moment it was submitted.
      {
        value: "local",
        label: (
          <span className={styles.kindOption}>
            <FolderOpen size={13} strokeWidth={1.8} />
            {t("knowledgeBases.dataSources.kinds.local")}
          </span>
        ),
      },
      {
        value: "smb",
        label: (
          <span className={styles.kindOption}>
            <Server size={13} strokeWidth={1.8} />
            {t("knowledgeBases.dataSources.kinds.smb")}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <section
      className={styles.panel}
      aria-label={t("knowledgeBases.dataSources.title")}
    >
      <div className={styles.header}>
        <span className={styles.headerTitle}>
          {t("knowledgeBases.dataSources.title")}
          {sources.length > 0 ? (
            <Tag className={styles.countTag}>{sources.length}</Tag>
          ) : null}
        </span>
        {canWrite ? (
          <Button
            size="small"
            icon={<Plus size={14} strokeWidth={1.8} />}
            onClick={openCreate}
          >
            {t("knowledgeBases.dataSources.add")}
          </Button>
        ) : null}
      </div>
      <Typography.Text type="secondary" className={styles.hint}>
        {t("knowledgeBases.dataSources.hint")}
      </Typography.Text>

      {loadError && !loading ? (
        <EmptyState
          variant="error"
          title={t("knowledgeBases.dataSources.loadFailed")}
          description={t("knowledgeBases.dataSources.loadFailedHint")}
          actionLabel={t("common.refresh")}
          onAction={() => void load()}
        />
      ) : loading ? (
        <div className={styles.loading}>
          <Spin size="small" />
        </div>
      ) : sources.length === 0 ? (
        <EmptyState
          title={t("knowledgeBases.dataSources.empty")}
          description={t("knowledgeBases.dataSources.emptyHint")}
          actionLabel={
            canWrite ? t("knowledgeBases.dataSources.add") : undefined
          }
          onAction={canWrite ? openCreate : undefined}
        />
      ) : (
        <ResizableTable
          storageKey="kb-data-sources"
          size="small"
          rowKey="id"
          pagination={false}
          scroll={{ x: 720 }}
          dataSource={sources}
          locale={{ emptyText: t("knowledgeBases.dataSources.empty") }}
          columns={[
            {
              title: t("knowledgeBases.dataSources.columnName"),
              dataIndex: "name",
              key: "name",
              ellipsis: true,
              render: (_, source) => {
                const detail = sourceDetail(source, connectorName);
                return (
                  <span className={styles.nameCell}>
                    <span className={styles.sourceName} title={source.name}>
                      {source.name}
                    </span>
                    {detail ? (
                      <span
                        className={styles.sourceDetail}
                        title={String(detail)}
                      >
                        {String(detail)}
                      </span>
                    ) : null}
                  </span>
                );
              },
            },
            {
              title: t("knowledgeBases.dataSources.columnKind"),
              dataIndex: "kind",
              key: "kind",
              width: 130,
              render: (kind: DataSourceKind) => (
                <Tag className={styles.kindTag}>
                  {t(`knowledgeBases.dataSources.kinds.${kind}`)}
                </Tag>
              ),
            },
            {
              title: t("knowledgeBases.dataSources.columnStatus"),
              dataIndex: "sync_status",
              key: "sync_status",
              width: 150,
              render: (_, source) =>
                SYNCABLE_KIND[source.kind] ? (
                  <Tooltip title={source.sync_error || undefined}>
                    <Tag color={syncStatusColor(source.sync_status)}>
                      {t(
                        `knowledgeBases.dataSources.syncStatus.${source.sync_status}`,
                      )}
                    </Tag>
                  </Tooltip>
                ) : (
                  // Honest up front: a connector has no ingest path, so the row
                  // advertises that instead of a fake status.
                  <Tooltip
                    title={t("knowledgeBases.dataSources.syncUnsupportedHint")}
                  >
                    <Tag>
                      {t("knowledgeBases.dataSources.syncUnsupportedTag")}
                    </Tag>
                  </Tooltip>
                ),
            },
            {
              title: t("knowledgeBases.dataSources.columnConnection"),
              key: "connection",
              width: 140,
              render: (_, source) =>
                FOLDER_KINDS.includes(source.kind) ? (
                  <Tooltip title={source.connection_error || undefined}>
                    <Tag color={connectionColor(source.connection_status)}>
                      {t(
                        `knowledgeBases.dataSources.connectionStatus.${source.connection_status}`,
                      )}
                    </Tag>
                  </Tooltip>
                ) : (
                  <span className={styles.never}>
                    {t("knowledgeBases.dataSources.connectionNotApplicable")}
                  </span>
                ),
            },
            {
              title: t("knowledgeBases.dataSources.columnLastSync"),
              dataIndex: "last_synced_at",
              key: "last_synced_at",
              width: 180,
              render: (lastSyncedAt: number | null) =>
                lastSyncedAt ? (
                  formatServerDateTime(lastSyncedAt, timeZone)
                ) : (
                  <span className={styles.never}>
                    {t("knowledgeBases.dataSources.never")}
                  </span>
                ),
            },
            ...(canWrite
              ? [
                  {
                    title: t("common.actions"),
                    key: "actions",
                    width: 160,
                    render: (_: unknown, source: DataSource) => {
                      const syncable = SYNCABLE_KIND[source.kind];
                      const testable = FOLDER_KINDS.includes(source.kind);
                      return (
                        <span className={styles.rowActions}>
                          {testable ? (
                            <Tooltip
                              title={t("knowledgeBases.dataSources.test")}
                            >
                              <span className={styles.actionSlot}>
                                <Button
                                  size="small"
                                  type="text"
                                  loading={testingId === source.id}
                                  aria-label={t(
                                    "knowledgeBases.dataSources.test",
                                  )}
                                  icon={<PlugZap size={14} strokeWidth={1.8} />}
                                  onClick={() => void testConnection(source)}
                                />
                              </span>
                            </Tooltip>
                          ) : null}
                          <Tooltip
                            title={
                              syncable
                                ? t("knowledgeBases.dataSources.sync")
                                : t(
                                    "knowledgeBases.dataSources.syncUnsupportedHint",
                                  )
                            }
                          >
                            <span className={styles.actionSlot}>
                              <Button
                                size="small"
                                type="text"
                                disabled={!syncable}
                                loading={syncingId === source.id}
                                aria-label={t(
                                  "knowledgeBases.dataSources.sync",
                                )}
                                icon={<RefreshCw size={14} strokeWidth={1.8} />}
                                onClick={() => void sync(source)}
                              />
                            </span>
                          </Tooltip>
                          <Button
                            size="small"
                            type="text"
                            danger
                            loading={deletingId === source.id}
                            aria-label={t("common.delete")}
                            icon={<Trash2 size={14} strokeWidth={1.8} />}
                            onClick={() => confirmDelete(source)}
                          />
                        </span>
                      );
                    },
                  },
                ]
              : []),
          ]}
        />
      )}

      <Modal
        title={t("knowledgeBases.dataSources.createTitle")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void create()}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        confirmLoading={submitting}
        okButtonProps={{ disabled: submitting }}
        width={520}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ kind: DEFAULT_KIND }}
          disabled={submitting}
        >
          <Form.Item
            name="name"
            label={t("knowledgeBases.dataSources.name")}
            rules={[
              {
                required: true,
                message: t("knowledgeBases.dataSources.nameRequired"),
              },
            ]}
          >
            <Input
              maxLength={200}
              placeholder={t("knowledgeBases.dataSources.namePlaceholder")}
            />
          </Form.Item>
          <Form.Item name="kind" label={t("knowledgeBases.dataSources.kind")}>
            <Segmented block options={kindOptions} />
          </Form.Item>

          {selectedKind === "upload" ? (
            <>
              <div className={styles.fileRow}>
                <input
                  ref={fileInputRef}
                  className={styles.fileInput}
                  type="file"
                  onChange={(event) => {
                    const picked = event.target.files?.[0] ?? null;
                    setFile(picked);
                    if (picked && !form.getFieldValue("name")) {
                      form.setFieldValue("name", picked.name);
                    }
                  }}
                />
                <Button
                  icon={<FileUp size={14} strokeWidth={1.8} />}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {t("knowledgeBases.dataSources.pickFile")}
                </Button>
                <span className={styles.fileName} title={file?.name}>
                  {file?.name || t("knowledgeBases.dataSources.noFileChosen")}
                </span>
              </div>
              <p className={styles.kindHint}>
                {t("knowledgeBases.dataSources.uploadHint")}
              </p>
            </>
          ) : null}

          {selectedKind === "url" ? (
            <>
              <Form.Item
                name="url"
                label={t("knowledgeBases.dataSources.urlLabel")}
                rules={[
                  {
                    required: true,
                    message: t("knowledgeBases.dataSources.urlRequired"),
                  },
                  {
                    pattern: /^https:\/\/\S+$/i,
                    message: t("knowledgeBases.dataSources.urlHttpsOnly"),
                  },
                ]}
              >
                <Input
                  placeholder={t("knowledgeBases.dataSources.urlPlaceholder")}
                />
              </Form.Item>
              <p className={styles.kindHint}>
                {t("knowledgeBases.dataSources.urlHint")}
              </p>
            </>
          ) : null}

          {FOLDER_KINDS.includes(selectedKind) ? (
            <>
              {selectedKind === "smb" ? (
                <>
                  <Form.Item
                    name="server"
                    label={t("knowledgeBases.dataSources.server")}
                    rules={[
                      {
                        required: true,
                        message: t("knowledgeBases.dataSources.serverRequired"),
                      },
                    ]}
                  >
                    <Input
                      maxLength={255}
                      placeholder={t(
                        "knowledgeBases.dataSources.serverPlaceholder",
                      )}
                    />
                  </Form.Item>
                  <Form.Item
                    name="share"
                    label={t("knowledgeBases.dataSources.share")}
                    rules={[
                      {
                        required: true,
                        message: t("knowledgeBases.dataSources.shareRequired"),
                      },
                    ]}
                  >
                    <Input
                      maxLength={255}
                      placeholder={t(
                        "knowledgeBases.dataSources.sharePlaceholder",
                      )}
                    />
                  </Form.Item>
                </>
              ) : null}
              <Form.Item
                name="rootPath"
                label={t("knowledgeBases.dataSources.rootPath")}
                rules={[
                  {
                    // A local source needs an absolute path; an SMB root is a
                    // path inside the share and may be empty for its top level.
                    required: selectedKind === "local",
                    message: t("knowledgeBases.dataSources.rootPathRequired"),
                  },
                ]}
              >
                <Input
                  maxLength={1000}
                  placeholder={
                    selectedKind === "smb"
                      ? t("knowledgeBases.dataSources.rootPathPlaceholderShare")
                      : t("knowledgeBases.dataSources.rootPathPlaceholderLocal")
                  }
                />
              </Form.Item>
              <Form.Item
                name="username"
                label={t("knowledgeBases.dataSources.username")}
              >
                <Input
                  maxLength={255}
                  autoComplete="off"
                  placeholder={t(
                    "knowledgeBases.dataSources.usernamePlaceholder",
                  )}
                />
              </Form.Item>
              <Form.Item
                name="password"
                label={t("knowledgeBases.dataSources.password")}
              >
                <Input.Password
                  autoComplete="new-password"
                  placeholder={t(
                    "knowledgeBases.dataSources.passwordPlaceholder",
                  )}
                />
              </Form.Item>
              <Form.Item
                name="includeGlobs"
                label={t("knowledgeBases.dataSources.includeGlobs")}
              >
                <Input.TextArea
                  rows={2}
                  maxLength={4000}
                  placeholder={t(
                    "knowledgeBases.dataSources.includeGlobsPlaceholder",
                  )}
                />
              </Form.Item>
              <Form.Item
                name="excludeGlobs"
                label={t("knowledgeBases.dataSources.excludeGlobs")}
              >
                <Input.TextArea
                  rows={2}
                  maxLength={4000}
                  placeholder={t(
                    "knowledgeBases.dataSources.excludeGlobsPlaceholder",
                  )}
                />
              </Form.Item>
              <p className={styles.kindHint}>
                {t("knowledgeBases.dataSources.folderHint")}
              </p>
            </>
          ) : null}
        </Form>
      </Modal>
    </section>
  );
}

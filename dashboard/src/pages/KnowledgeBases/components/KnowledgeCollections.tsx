import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type MouseEvent,
} from "react";
import {
  App,
  Button,
  Drawer,
  Empty,
  Input,
  Modal,
  Spin,
  Tabs,
  Tree,
  Typography,
} from "antd";
import type { TreeDataNode } from "antd";
import { File, Folder, Layers, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  knowledgeBasesApi,
  type KnowledgeCollection,
  type KnowledgeCollectionDocuments,
  type KnowledgeDocument,
} from "../../../api/modules/knowledgeBases";
import type { KnowledgePermissionMetadata } from "../../../api/modules/knowledgePermissions";
import { apiErrorMessage } from "../../../utils/apiError";
import Markdown from "../../../components/Markdown";
import styles from "./KnowledgeCollections.module.less";

type Props = {
  baseId: string;
  documents: KnowledgeDocument[];
  onOpenTarget: (documentId: string) => void;
  permissionsByDocument: ReadonlyMap<string, KnowledgePermissionMetadata>;
  onOpenCitation: (href: string) => void;
};

type DocumentTreeNode = TreeDataNode & {
  path: string;
  children: DocumentTreeNode[];
};

export default function KnowledgeCollections({
  baseId,
  documents,
  onOpenTarget,
  permissionsByDocument,
  onOpenCitation,
}: Props) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [open, setOpen] = useState(false);
  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<
    Record<string, { name: string; prompt: string }>
  >({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [inspecting, setInspecting] =
    useState<KnowledgeCollectionDocuments | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);
  const [createPrompt, setCreatePrompt] = useState("");
  const documentTree = useMemo(() => {
    const sources = new Map(
      documents.map((doc) => [doc.path || doc.filename, doc]),
    );
    const nodes = new Map<string, DocumentTreeNode>();
    const roots: DocumentTreeNode[] = [];
    const ensureNode = (path: string): DocumentTreeNode => {
      const existing = nodes.get(path);
      if (existing) return existing;
      const source = sources.get(path);
      const isDirectory = source ? !!source.is_dir : true;
      const separator = path.lastIndexOf("/");
      const name = path.slice(separator + 1);
      const node: DocumentTreeNode = {
        key: source?.id ?? `path:${path}`,
        path,
        title: (
          <span className={styles.target} title={path}>
            {isDirectory ? <Folder size={14} /> : <File size={14} />}
            <span className={styles.targetName}>{name}</span>
          </span>
        ),
        isLeaf: !isDirectory,
        checkable: !!source,
        children: [],
      };
      nodes.set(path, node);
      if (separator > 0) {
        ensureNode(path.slice(0, separator)).children.push(node);
      } else {
        roots.push(node);
      }
      return node;
    };
    for (const path of sources.keys()) ensureNode(path);
    const sortNodes = (items: DocumentTreeNode[]) => {
      items.sort(
        (a, b) =>
          Number(a.isLeaf) - Number(b.isLeaf) ||
          a.path.toLocaleLowerCase().localeCompare(b.path.toLocaleLowerCase()),
      );
      for (const node of items) sortNodes(node.children);
    };
    sortNodes(roots);
    return roots;
  }, [documents]);

  const selectionTree = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return { nodes: documentTree, expandedKeys: [] as string[] };
    const searchExpandedKeys: string[] = [];
    const filterNodes = (items: DocumentTreeNode[]): DocumentTreeNode[] => {
      const result: DocumentTreeNode[] = [];
      for (const node of items) {
        const children = filterNodes(node.children);
        if (
          !children.length &&
          !node.path.toLocaleLowerCase().includes(needle)
        ) {
          continue;
        }
        if (children.length) searchExpandedKeys.push(String(node.key));
        result.push({ ...node, children });
      }
      return result;
    };
    return {
      nodes: filterNodes(documentTree),
      expandedKeys: searchExpandedKeys,
    };
  }, [documentTree, query]);

  useEffect(() => {
    setExpandedKeys(selectionTree.expandedKeys);
  }, [selectionTree]);

  const loadCollections = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const rows = await knowledgeBasesApi.listCollections(baseId);
      setCollections(rows);
      setDrafts(
        Object.fromEntries(
          rows.map((row) => [row.id, { name: row.name, prompt: row.prompt }]),
        ),
      );
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [baseId]);

  useEffect(() => {
    if (open) void loadCollections();
  }, [open, loadCollections]);

  const create = async () => {
    const prompt = createPrompt.trim();
    if (!selectedIds.length || prompt.length > 2000) return;
    setCreating(true);
    try {
      await knowledgeBasesApi.createCollections(baseId, selectedIds, prompt);
      setSelectedIds([]);
      setCreatePrompt("");
      await loadCollections();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.collections.actionFailed"), t),
      );
    } finally {
      setCreating(false);
    }
  };

  const save = async (row: KnowledgeCollection) => {
    if (!permissionsByDocument.get(row.target_document_id)?.effective.write)
      return;
    const draft = drafts[row.id] ?? {
      name: row.name,
      prompt: row.prompt,
    };
    setBusyId(row.id);
    try {
      const updated = await knowledgeBasesApi.updateCollection(baseId, row.id, {
        name: draft.name.trim(),
        prompt: draft.prompt,
      });
      setCollections((current) =>
        current.map((item) => (item.id === row.id ? updated : item)),
      );
      message.success(t("knowledgeBases.collections.saved"));
      await loadCollections();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.collections.actionFailed"), t),
      );
    } finally {
      setBusyId(null);
    }
  };

  const generate = async (row: KnowledgeCollection) => {
    if (!permissionsByDocument.get(row.target_document_id)?.effective.write)
      return;
    const draft = drafts[row.id] ?? { name: row.name, prompt: row.prompt };
    if (!draft.prompt.trim()) {
      message.info(t("knowledgeBases.collections.promptRequired"));
      return;
    }
    setBusyId(row.id);
    try {
      if (draft.name.trim() !== row.name || draft.prompt !== row.prompt) {
        await knowledgeBasesApi.updateCollection(baseId, row.id, {
          name: draft.name.trim(),
          prompt: draft.prompt,
        });
      }
      const updated = await knowledgeBasesApi.generateCollection(
        baseId,
        row.id,
      );
      setCollections((current) =>
        current.map((item) => (item.id === row.id ? updated : item)),
      );
      if (updated.generation.state === "ready") {
        message.success(t("knowledgeBases.collections.generated"));
      } else if (updated.generation.state === "partial") {
        message.warning(t("knowledgeBases.collections.generation.partial"));
      } else {
        message.error(
          updated.generation.error ||
            t("knowledgeBases.collections.generation.error"),
        );
      }
      await loadCollections();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.collections.actionFailed"), t),
      );
    } finally {
      setBusyId(null);
    }
  };

  const inspect = async (row: KnowledgeCollection) => {
    setInspectLoading(true);
    try {
      setInspecting(
        await knowledgeBasesApi.getCollectionDocuments(baseId, row.id),
      );
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.collections.actionFailed"), t),
      );
    } finally {
      setInspectLoading(false);
    }
  };

  const remove = (row: KnowledgeCollection) => {
    if (!permissionsByDocument.get(row.target_document_id)?.effective.write)
      return;
    modal.confirm({
      title: t("knowledgeBases.collections.deleteConfirm", {
        name: row.name || row.target_path,
      }),
      content: t("knowledgeBases.collections.deleteHint"),
      okButtonProps: { danger: true },
      onOk: async () => {
        setBusyId(row.id);
        try {
          await knowledgeBasesApi.deleteCollection(baseId, row.id);
          setCollections((current) =>
            current.filter((item) => item.id !== row.id),
          );
          setDrafts((current) => {
            const next = { ...current };
            delete next[row.id];
            return next;
          });
        } catch (error) {
          message.error(
            apiErrorMessage(
              error,
              t("knowledgeBases.collections.actionFailed"),
              t,
            ),
          );
          throw error;
        } finally {
          setBusyId(null);
        }
      },
    });
  };
  const openCitation = (event: MouseEvent<HTMLDivElement>) => {
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest("a");
    if (!anchor) return;
    const url = new URL(anchor.href, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      url.pathname !== "/knowledge-bases" ||
      !url.searchParams.has("kb") ||
      !url.searchParams.has("doc") ||
      !url.searchParams.has("v") ||
      !url.searchParams.has("loc")
    ) {
      return;
    }
    event.preventDefault();
    setInspecting(null);
    onOpenCitation(url.href);
  };

  return (
    <>
      <Button icon={<Layers size={14} />} onClick={() => setOpen(true)}>
        {t("knowledgeBases.collections.open")}
      </Button>
      <Drawer
        title={t("knowledgeBases.collections.title")}
        open={open}
        onClose={() => setOpen(false)}
        width={640}
        destroyOnHidden
      >
        <section className={styles.section}>
          <Typography.Text strong>
            {t("knowledgeBases.collections.selectTitle")}
          </Typography.Text>
          <Typography.Text type="secondary">
            {t("knowledgeBases.collections.selectHint")}
          </Typography.Text>
          <Input.Search
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("knowledgeBases.collections.searchPlaceholder")}
            allowClear
          />
          <div className={styles.selectionList}>
            {selectionTree.nodes.length ? (
              <Tree
                className={styles.selectionTree}
                treeData={selectionTree.nodes}
                checkable
                checkStrictly
                selectable={false}
                blockNode
                height={240}
                expandedKeys={expandedKeys}
                autoExpandParent={false}
                onExpand={(keys) => setExpandedKeys(keys.map(String))}
                checkedKeys={selectedIds}
                onCheck={(_keys, info) => {
                  const id = String(info.node.key);
                  setSelectedIds((current) =>
                    info.checked
                      ? [...current, id]
                      : current.filter((selectedId) => selectedId !== id),
                  );
                }}
              />
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("knowledgeBases.collections.noDocuments")}
              />
            )}
          </div>
          <Input.TextArea
            aria-label={t("knowledgeBases.collections.prompt")}
            value={createPrompt}
            onChange={(event) => setCreatePrompt(event.target.value)}
            placeholder={t("knowledgeBases.collections.promptPlaceholder")}
            maxLength={2000}
            showCount
            autoSize={{ minRows: 3, maxRows: 6 }}
          />
          <Button
            type="primary"
            loading={creating}
            disabled={!selectedIds.length}
            onClick={() => void create()}
          >
            {t("knowledgeBases.collections.create", {
              count: selectedIds.length,
            })}
          </Button>
        </section>
        <section className={styles.section}>
          <div className={styles.heading}>
            <Typography.Text strong>
              {t("knowledgeBases.collections.manageTitle")}
            </Typography.Text>
            <Button
              icon={<RefreshCw size={14} />}
              onClick={() => void loadCollections()}
              loading={loading}
            >
              {t("common.refresh")}
            </Button>
          </div>
          {loading ? (
            <Spin />
          ) : loadError ? (
            <Typography.Text type="danger">
              {t("knowledgeBases.collections.loadFailed")}
            </Typography.Text>
          ) : collections.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("knowledgeBases.collections.empty")}
            />
          ) : (
            collections.map((row) => {
              const draft = drafts[row.id] ?? {
                name: row.name,
                prompt: row.prompt,
              };
              const canEdit = Boolean(
                permissionsByDocument.get(row.target_document_id)?.effective
                  .write,
              );
              return (
                <article className={styles.collection} key={row.id}>
                  <div className={styles.heading}>
                    <Typography.Text strong>{row.target_path}</Typography.Text>
                    <Typography.Text type="secondary">
                      {t(`knowledgeBases.collections.status.${row.status}`)} ·{" "}
                      {t(
                        `knowledgeBases.collections.generation.${row.generation.state}`,
                      )}{" "}
                      · {t("knowledgeBases.collections.coverage", row.coverage)}
                    </Typography.Text>
                  </div>
                  {row.generation.error ? (
                    <Typography.Text type="danger">
                      {row.generation.error}
                    </Typography.Text>
                  ) : null}
                  {canEdit ? (
                    <>
                      <Input
                        aria-label={t("knowledgeBases.collections.name")}
                        value={draft.name}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [row.id]: { ...draft, name: event.target.value },
                          }))
                        }
                        placeholder={t("knowledgeBases.collections.name")}
                      />
                      <Input.TextArea
                        aria-label={t("knowledgeBases.collections.prompt")}
                        value={draft.prompt}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [row.id]: { ...draft, prompt: event.target.value },
                          }))
                        }
                        placeholder={t(
                          "knowledgeBases.collections.promptPlaceholder",
                        )}
                        autoSize={{ minRows: 3, maxRows: 8 }}
                      />
                    </>
                  ) : null}
                  <div className={styles.actions}>
                    {canEdit ? (
                      <>
                        <Button
                          loading={busyId === row.id}
                          onClick={() => void save(row)}
                        >
                          {t("common.save")}
                        </Button>
                        <Button
                          type="primary"
                          icon={<RefreshCw size={14} />}
                          loading={
                            busyId === row.id ||
                            row.generation.state === "running"
                          }
                          disabled={
                            row.generation.state === "running" ||
                            !draft.prompt.trim()
                          }
                          onClick={() => void generate(row)}
                        >
                          {t("knowledgeBases.collections.organize")}
                        </Button>
                        <Button
                          icon={<Trash2 size={14} />}
                          danger
                          loading={busyId === row.id}
                          onClick={() => remove(row)}
                        >
                          {t("common.delete")}
                        </Button>
                      </>
                    ) : null}
                    <Button
                      loading={inspectLoading}
                      onClick={() => void inspect(row)}
                    >
                      {t("knowledgeBases.collections.inspect")}
                    </Button>
                    <Button
                      type="link"
                      onClick={() => onOpenTarget(row.target_document_id)}
                    >
                      {t("knowledgeBases.collections.openOriginal")}
                    </Button>
                  </div>
                </article>
              );
            })
          )}
        </section>
        <Typography.Text type="secondary">
          {t("knowledgeBases.collections.storageHint")}
        </Typography.Text>
      </Drawer>
      <Modal
        title={
          inspecting?.collection.name || t("knowledgeBases.collections.inspect")
        }
        open={Boolean(inspecting)}
        onCancel={() => setInspecting(null)}
        footer={null}
        width={900}
      >
        {inspecting ? (
          <>
            <Typography.Text type="secondary">
              {t("knowledgeBases.collections.storageHint")}
            </Typography.Text>
            <Tabs
              items={[
                {
                  key: "topic",
                  label: t("knowledgeBases.collections.topicDocument"),
                  children: (
                    <div className={styles.preview} onClick={openCitation}>
                      <Markdown content={inspecting.topic_document} />
                    </div>
                  ),
                },
                {
                  key: "reading",
                  label: t("knowledgeBases.collections.readingDocument"),
                  children: (
                    <div className={styles.preview} onClick={openCitation}>
                      <Markdown content={inspecting.reading_document} />
                    </div>
                  ),
                },
              ]}
            />
            <Button
              onClick={() => {
                setInspecting(null);
                onOpenTarget(inspecting.collection.target_document_id);
              }}
            >
              {t("knowledgeBases.collections.openOriginal")}
            </Button>
          </>
        ) : null}
      </Modal>
    </>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "antd";
import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronUp,
  CircleHelp,
  Eye,
  FolderOpen,
  LayoutPanelLeft,
  PanelTopOpen,
  RotateCcw,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { BRAND } from "../../brand.generated";
import { PREVIEW_DATA } from "./demoData";
import {
  PREVIEW_COPY_KEYS,
  type PreviewAgent,
  type PreviewActions,
  type PreviewCopy,
  type PreviewData,
  type PreviewDocument,
  type PreviewRecord,
  type PreviewScope,
  type PreviewSource,
} from "./model";
import {
  DocumentTypeIcon,
  DueBadge,
  scopeLabel,
  StatusBadge,
} from "./PreviewShared";
import PlatformPreview from "./full/PlatformPreview";
import { DemoStateProvider } from "./full/DemoKit";
import { PREVIEW_NAVIGATE_EVENT } from "./full/PreviewEnvironment";
import styles from "./InterfacePreview.module.less";

type PreviewDialog =
  | { kind: "document"; document: PreviewDocument }
  | { kind: "record"; record: PreviewRecord }
  | { kind: "agent"; agent: PreviewAgent }
  | { kind: "import" | "reminders" | "guide" };

function csvCell(value: string) {
  const safe = /^\s*[=+\-@]/.test(value) ? `'${value}` : value;
  return `"${safe.replace(/"/g, '""')}"`;
}

export default function InterfacePreview() {
  const { t } = useTranslation();
  const copy = useMemo(
    () =>
      Object.fromEntries(
        PREVIEW_COPY_KEYS.map((key) => [key, t(`interfacePreview.${key}`)]),
      ) as PreviewCopy,
    [t],
  );
  const [toolbarVisible, setToolbarVisible] = useState(true);
  const [dialog, setDialog] = useState<PreviewDialog | null>(null);
  const [extraDocuments, setExtraDocuments] = useState<PreviewDocument[]>([]);
  const [resetVersion, setResetVersion] = useState(0);
  const [notice, setNotice] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadSource, setUploadSource] = useState<PreviewSource>("platform");
  const [uploadScope, setUploadScope] = useState<PreviewScope>("enterprise");
  const [uploadBaseId, setUploadBaseId] = useState("demo-base-enterprise");
  const [allowDelegation, setAllowDelegation] = useState(false);
  const importSequence = useRef(0);
  const data = useMemo<PreviewData>(
    () => ({
      ...PREVIEW_DATA,
      documents: [...PREVIEW_DATA.documents, ...extraDocuments],
    }),
    [extraDocuments],
  );
  const dueRecords = data.records
    .filter((record) => record.remainingDays <= 7)
    .slice()
    .sort((a, b) => a.remainingDays - b.remainingDays);
  const openDemoPage = (path: string) => {
    setDialog(null);
    window.dispatchEvent(
      new CustomEvent(PREVIEW_NAVIGATE_EVENT, {
        detail: { path },
      }),
    );
  };
  const openChatDemo = () => openDemoPage("/chat");
  const openPageDirectory = () => {
    setDialog(null);
    window.dispatchEvent(
      new CustomEvent(PREVIEW_NAVIGATE_EVENT, {
        detail: { directory: true },
      }),
    );
  };
  useEffect(() => {
    document.title = `${BRAND.shortName.zh} · ${copy.originalScheme}`;
  }, [copy.originalScheme]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const exportDocuments = (
    documents: readonly PreviewDocument[] = data.documents,
  ) => {
    const header = [
      copy.originalFileName,
      copy.type,
      copy.sources,
      copy.status,
      copy.permissionScope,
      copy.owner,
      copy.updated,
    ];
    const rows = documents.map((document) => [
      document.name,
      document.kind,
      document.source === "platform" ? copy.platformSource : copy.folderSource,
      document.status === "ready"
        ? copy.ready
        : document.status === "archive_only"
        ? copy.archived
        : copy.processing,
      scopeLabel(document.scope, copy),
      document.owner,
      document.updatedLabel,
    ]);
    const blob = new Blob(
      [
        "\ufeff",
        [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n"),
      ],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = copy.exportFilename;
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice(copy.exportDemoDone);
  };
  const addDemoDocument = () => {
    if (!selectedFile) return;
    const extension = selectedFile.name.split(".").pop()?.toUpperCase();
    const kind: PreviewDocument["kind"] =
      extension === "PDF" ||
      extension === "DOCX" ||
      extension === "XLSX" ||
      extension === "DWG" ||
      extension === "TXT" ||
      extension === "MD"
        ? extension
        : "BIN";
    setExtraDocuments((documents) => [
      ...documents,
      {
        id: `demo-import-${++importSequence.current}`,
        baseId: uploadBaseId,
        name: selectedFile.name,
        kind,
        source: uploadSource,
        scope: uploadScope,
        status: "processing",
        owner: copy.sampleUser,
        sizeLabel:
          selectedFile.size < 1024 * 1024
            ? `${(selectedFile.size / 1024).toFixed(1)} KB`
            : `${(selectedFile.size / 1024 / 1024).toFixed(1)} MB`,
        updatedLabel: "2026.10.25",
        fingerprint: copy.browserLocalOnly,
        excerpt: copy.importLocalOnly,
      },
    ]);
    setSelectedFile(null);
    setDialog(null);
    setNotice(copy.uploadDemoDone);
  };
  const actions: PreviewActions = {
    openDocument: (document) => setDialog({ kind: "document", document }),
    openRecord: (record) => setDialog({ kind: "record", record }),
    openAgent: (agent) => setDialog({ kind: "agent", agent }),
    openImport: (baseId = "demo-base-enterprise") => {
      setUploadBaseId(baseId);
      setDialog({ kind: "import" });
    },
    openReminders: () => setDialog({ kind: "reminders" }),
    exportDocuments,
    openGuide: () => setDialog({ kind: "guide" }),
  };
  const resetDemo = () => {
    setExtraDocuments([]);
    setSelectedFile(null);
    setUploadSource("platform");
    setUploadScope("enterprise");
    setAllowDelegation(false);
    setDialog(null);
    setNotice("");
    setResetVersion((value) => value + 1);
  };
  const title =
    dialog?.kind === "document"
      ? copy.docPreviewTitle
      : dialog?.kind === "record"
      ? copy.recordDetail
      : dialog?.kind === "agent"
      ? copy.agentPreviewTitle
      : dialog?.kind === "import"
      ? copy.importDocuments
      : dialog?.kind === "reminders"
      ? copy.reminderTitle
      : copy.guideTitle;

  return (
    <div
      className={`${styles.root} ${toolbarVisible ? "" : styles.toolbarHidden}`}
      data-scheme="original"
    >
      {toolbarVisible ? (
        <header className={styles.toolbar}>
          <div className={styles.toolbarLabel}>
            <span>
              <LayoutPanelLeft size={17} />
            </span>
            <div>
              <strong>{copy.scheme}</strong>
              <small>{copy.demoSafety}</small>
            </div>
          </div>
          <nav aria-label={copy.schemeSwitch} className={styles.schemeSwitch}>
            <button onClick={openPageDirectory} aria-haspopup="dialog">
              {copy.originalScheme}
            </button>
          </nav>
          <div className={styles.toolbarActions}>
            <button
              onClick={() => setDialog({ kind: "guide" })}
              aria-label={copy.help}
              title={copy.help}
            >
              <CircleHelp size={17} />
            </button>
            <button
              onClick={resetDemo}
              aria-label={copy.resetDemo}
              title={copy.resetDemo}
            >
              <RotateCcw size={16} />
            </button>
            <button
              onClick={() => setToolbarVisible(false)}
              aria-label={copy.hideToolbar}
              title={copy.hideToolbar}
            >
              <ChevronUp size={17} />
            </button>
          </div>
        </header>
      ) : (
        <button
          className={styles.restoreToolbar}
          aria-label={copy.showToolbar}
          title={copy.showToolbar}
          onClick={() => setToolbarVisible(true)}
        >
          <PanelTopOpen size={15} />
          <span>{copy.demoShort}</span>
          <ChevronUp size={13} />
        </button>
      )}
      <DemoStateProvider key={resetVersion}>
        <PlatformPreview
          key={resetVersion}
          data={data}
          copy={copy}
          actions={actions}
        />
      </DemoStateProvider>
      {notice && (
        <div className={styles.notice} role="status">
          <Check size={17} />
          <span>{notice}</span>
          <button onClick={() => setNotice("")} aria-label={copy.close}>
            <X size={14} />
          </button>
        </div>
      )}
      <Modal
        open={dialog !== null}
        title={title}
        onCancel={() => setDialog(null)}
        footer={null}
        width="min(740px, calc(100vw - 32px))"
        centered
        destroyOnHidden
        wrapClassName={styles.modalWrap}
        styles={{ body: { maxHeight: "70dvh", overflowY: "auto" } }}
      >
        <div className={styles.modalContent}>
          {dialog?.kind === "document" && (
            <>
              <div className={styles.documentHeading}>
                <DocumentTypeIcon kind={dialog.document.kind} size={24} />
                <div>
                  <h2>{dialog.document.name}</h2>
                  <p>
                    {dialog.document.kind} · {dialog.document.sizeLabel} ·{" "}
                    {dialog.document.updatedLabel}
                  </p>
                </div>
                <StatusBadge status={dialog.document.status} copy={copy} />
              </div>
              <div className={styles.infoBanner}>
                <Eye size={17} />
                <span>{copy.docPreviewHint}</span>
              </div>
              <dl className={styles.metadata}>
                <div>
                  <dt>{copy.sources}</dt>
                  <dd>
                    {dialog.document.source === "platform"
                      ? copy.platformSource
                      : copy.folderSource}
                  </dd>
                </div>
                <div>
                  <dt>{copy.permissionScope}</dt>
                  <dd>{scopeLabel(dialog.document.scope, copy)}</dd>
                </div>
                <div>
                  <dt>{copy.owner}</dt>
                  <dd>{dialog.document.owner}</dd>
                </div>
                <div>
                  <dt>{copy.version}</dt>
                  <dd>{dialog.document.fingerprint}</dd>
                </div>
              </dl>
              <div className={styles.excerpt}>{dialog.document.excerpt}</div>
              <p className={styles.modalHint}>
                <ShieldCheck size={14} />
                {copy.permissionNote}
              </p>
            </>
          )}
          {dialog?.kind === "record" && (
            <>
              <div className={styles.recordHeading}>
                <div>
                  <span>{dialog.record.code}</span>
                  <h2>{dialog.record.name}</h2>
                </div>
                <DueBadge record={dialog.record} copy={copy} />
              </div>
              <dl className={styles.metadata}>
                <div>
                  <dt>{copy.department}</dt>
                  <dd>{dialog.record.department}</dd>
                </div>
                <div>
                  <dt>{copy.responsible}</dt>
                  <dd>{dialog.record.responsible}</dd>
                </div>
                <div>
                  <dt>{copy.issuedOn}</dt>
                  <dd>{dialog.record.issuedOn}</dd>
                </div>
                <div>
                  <dt>{copy.cycleMonths}</dt>
                  <dd>{dialog.record.cycleMonths}</dd>
                </div>
                <div>
                  <dt>{copy.computedDue}</dt>
                  <dd>{dialog.record.nextDue}</dd>
                </div>
                <div>
                  <dt>{copy.status}</dt>
                  <dd>
                    {dialog.record.remainingDays < 0
                      ? copy.overdue
                      : dialog.record.remainingDays <= 7
                      ? copy.dueWithinWeek
                      : copy.normal}
                  </dd>
                </div>
              </dl>
              <h3>{copy.linkedDocuments}</h3>
              <div className={styles.linkedFiles}>
                {dialog.record.documentIds
                  .map((id) =>
                    data.documents.find((document) => document.id === id),
                  )
                  .filter((document) => document !== undefined)
                  .map((document) => (
                    <button
                      key={document.id}
                      onClick={() => setDialog({ kind: "document", document })}
                    >
                      <DocumentTypeIcon kind={document.kind} />
                      <span>{document.name}</span>
                      <ArrowUpRight size={15} />
                    </button>
                  ))}
              </div>
              <p className={styles.modalHint}>{copy.reminderHint}</p>
            </>
          )}
          {dialog?.kind === "agent" && (
            <>
              <div className={styles.agentHeading}>
                <span>
                  <BookOpen size={28} />
                </span>
                <h2>{dialog.agent.name}</h2>
                <p>{dialog.agent.description}</p>
              </div>
              <div className={styles.infoBanner}>
                <ShieldCheck size={17} />
                <span>{copy.agentPreviewHint}</span>
              </div>
              <h3>{copy.contextFiles}</h3>
              <div className={styles.linkedFiles}>
                {dialog.agent.documentIds
                  .map((id) =>
                    data.documents.find((document) => document.id === id),
                  )
                  .filter((document) => document !== undefined)
                  .map((document) => (
                    <button
                      key={document.id}
                      onClick={() => setDialog({ kind: "document", document })}
                    >
                      <DocumentTypeIcon kind={document.kind} />
                      <span>{document.name}</span>
                      <ArrowUpRight size={15} />
                    </button>
                  ))}
              </div>
              <button className={styles.primaryButton} onClick={openChatDemo}>
                {copy.startDemoConversation}
                <ArrowRight size={15} />
              </button>
            </>
          )}
          {dialog?.kind === "reminders" && (
            <>
              <p className={styles.intro}>{copy.reminderHint}</p>
              <div className={styles.reminderList}>
                {dueRecords.map((record) => (
                  <button
                    key={record.id}
                    onClick={() => setDialog({ kind: "record", record })}
                  >
                    <div>
                      <span>{record.code}</span>
                      <strong>{record.name}</strong>
                      <small>
                        {record.department} · {record.responsible}
                      </small>
                    </div>
                    <DueBadge record={record} copy={copy} />
                    <ArrowUpRight size={15} />
                  </button>
                ))}
              </div>
            </>
          )}
          {dialog?.kind === "import" && (
            <>
              <div className={styles.infoBanner}>
                <ShieldCheck size={17} />
                <span>{copy.importLocalOnly}</span>
              </div>
              <label className={styles.filePicker}>
                <Upload size={25} />
                <strong>{copy.selectFile}</strong>
                <span>
                  {selectedFile
                    ? `${copy.fileSelected} · ${selectedFile.name}`
                    : copy.noFileSelected}
                </span>
                <input
                  type="file"
                  aria-label={copy.selectFile}
                  onChange={(event) =>
                    setSelectedFile(event.target.files?.[0] ?? null)
                  }
                />
              </label>
              <fieldset className={styles.destinations}>
                <legend>{copy.uploadDestination}</legend>
                <label
                  className={
                    uploadSource === "platform"
                      ? styles.destinationSelected
                      : ""
                  }
                >
                  <input
                    type="radio"
                    name="demo-upload-source"
                    value="platform"
                    checked={uploadSource === "platform"}
                    onChange={() => setUploadSource("platform")}
                  />
                  <BookOpen size={20} />
                  <span>{copy.platformSource}</span>
                </label>
                <label
                  className={
                    uploadSource === "folder" ? styles.destinationSelected : ""
                  }
                >
                  <input
                    type="radio"
                    name="demo-upload-source"
                    value="folder"
                    checked={uploadSource === "folder"}
                    onChange={() => setUploadSource("folder")}
                  />
                  <FolderOpen size={20} />
                  <span>{copy.sourceFolder}</span>
                </label>
              </fieldset>
              <label className={styles.scopeField}>
                <span>{copy.permissionScope}</span>
                <select
                  aria-label={copy.permissionScope}
                  value={uploadScope}
                  onChange={(event) => {
                    const scope = event.target.value as PreviewScope;
                    setUploadScope(scope);
                    if (scope === "personal") setAllowDelegation(false);
                  }}
                >
                  <option value="enterprise">{copy.enterpriseScope}</option>
                  <option value="department">{copy.departmentScope}</option>
                  <option value="personal">{copy.personalScope}</option>
                </select>
              </label>
              <p className={styles.modalHint}>{copy.maxPermissionHint}</p>
              <label className={styles.delegation}>
                <input
                  type="checkbox"
                  disabled={uploadScope === "personal"}
                  checked={allowDelegation}
                  onChange={(event) => setAllowDelegation(event.target.checked)}
                />
                {copy.allowDelegation}
              </label>
              <div className={styles.modalFooter}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => setDialog(null)}
                >
                  {copy.cancel}
                </button>
                <button
                  className={styles.primaryButton}
                  disabled={!selectedFile}
                  onClick={addDemoDocument}
                >
                  {copy.addDemoDocument}
                  <ArrowRight size={15} />
                </button>
              </div>
            </>
          )}
          {dialog?.kind === "guide" && (
            <>
              <p className={styles.intro}>{copy.guideDescription}</p>
              <div className={styles.schemeCards}>
                {[
                  ["/knowledge-bases", "flowKnowledge"],
                  ["/features", "flowFeatures"],
                  ["/chat", "flowChat"],
                  ["/sharing", "flowGovernance"],
                ].map(([path, key], index) => (
                  <button key={path} onClick={() => openDemoPage(path)}>
                    <span>{index + 1}</span>
                    <div>
                      <strong>{t(`interfacePreview.${key}`)}</strong>
                    </div>
                    <ArrowUpRight size={17} />
                  </button>
                ))}
              </div>
              <div className={styles.infoBanner}>
                <ShieldCheck size={17} />
                <span>{copy.demoSafety}</span>
              </div>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
}

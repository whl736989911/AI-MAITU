/**
 * 输出 — what a run handed back.
 *
 * A run's files are collected per thread by the platform (``threads.artifacts``)
 * and read back through the history endpoint, so this card reads the same list the
 * workspace panel does and invents no second source for "what did it produce".
 *
 * **Read under the definition's own names.** When the definition declares
 * ``outputs``, a produced file is shown under the name its declaration gives it —
 * 报价单 next to the file that answers to ``outputs/quote.md`` — because that is
 * what the caller asked for and it makes a run's result checkable against its
 * declaration. A file nothing declared keeps its own name; a declaration without a
 * ``path`` labels nothing, since which file it is cannot be known from here.
 *
 * Preview and download are the chat's own: the same workspace preview panel and
 * the same authenticated download the attachment cards use.
 */

import { useCallback, useMemo, useState } from "react";
import { Button, Tooltip } from "antd";
import { ChevronDown, Download, Eye, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { WorkflowOutput } from "../../../api/modules/featureWorkflow";
import { downloadAuthFile } from "../../../components/AuthFileDownloadLink";
import { message as antMessage } from "../../../utils/antdMessage";
import { pickLocale } from "../../../utils/localizedText";
import type { UiLocale } from "../../../utils/locale";
import {
  agentAttachmentAccessUrl,
  isDataUrl,
  needsAuthBlobFetch,
} from "../../../utils/toolMediaBlocks";
import { useChatFilePreview } from "../ChatFilePreviewContext";
import { labelRunOutputs, type RunOutputFile } from "../utils/featureRun";
import styles from "./WorkflowOutputCard.module.less";

export interface WorkflowOutputCardProps {
  /** The feature's own agent: previews and downloads are agent-scoped. */
  agentId: string;
  /** The workspace paths the run produced, as the thread's artifacts list them. */
  files: readonly string[];
  /** The definition's declared deliverables, when it declares any. */
  outputs?: readonly WorkflowOutput[];
}

/** One produced file: what it is called, where it is, and what can be done with it. */
function RunFileRow({
  entry,
  agentId,
  locale,
}: {
  entry: RunOutputFile;
  agentId: string;
  locale: UiLocale;
}) {
  const { t } = useTranslation();
  const filePreview = useChatFilePreview();
  const [downloading, setDownloading] = useState(false);
  const url = agentAttachmentAccessUrl(agentId, entry.path);
  const description = entry.declared?.description
    ? pickLocale(entry.declared.description, locale)
    : "";

  const download = useCallback(async () => {
    if (!needsAuthBlobFetch(url) && !isDataUrl(url)) {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = entry.filename;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      return;
    }
    setDownloading(true);
    try {
      await downloadAuthFile(url, { filename: entry.filename });
    } catch {
      antMessage.error(t("chat.downloadFailed", "下载失败，请重试"));
    } finally {
      setDownloading(false);
    }
  }, [url, entry.filename, t]);

  return (
    <li className={styles.fileRow}>
      <FileText
        size={16}
        strokeWidth={2}
        className={styles.fileIcon}
        aria-hidden
      />
      <span className={styles.fileText}>
        <span className={styles.fileLabel}>
          {entry.declared?.name || entry.filename}
        </span>
        {/* The file's own path is shown whenever the label is not the filename —
            the caller has to be able to find it in the workspace. */}
        {entry.declared?.name && entry.declared.name !== entry.filename ? (
          <span className={styles.filePath} title={entry.path}>
            {entry.path}
          </span>
        ) : null}
        {description ? (
          <span className={styles.fileDescription}>{description}</span>
        ) : null}
      </span>
      {filePreview ? (
        <Tooltip title={t("common.preview")}>
          <Button
            type="text"
            size="small"
            icon={<Eye size={15} strokeWidth={2} />}
            aria-label={t("common.preview")}
            onClick={() => filePreview.openFilePreview(entry.path)}
          />
        </Tooltip>
      ) : null}
      <Tooltip title={t("common.download")}>
        <Button
          type="text"
          size="small"
          icon={<Download size={15} strokeWidth={2} />}
          loading={downloading}
          aria-label={t("common.download")}
          onClick={() => void download()}
        />
      </Tooltip>
    </li>
  );
}

export default function WorkflowOutputCard({
  agentId,
  files,
  outputs,
}: WorkflowOutputCardProps) {
  const { t, i18n } = useTranslation();
  const locale: UiLocale = i18n.language?.startsWith("zh") ? "zh" : "en";
  const [expanded, setExpanded] = useState(false);
  const entries = useMemo(
    () => labelRunOutputs(files, outputs),
    [files, outputs],
  );

  if (entries.length === 0) return null;

  return (
    <section
      className={`${styles.card} ${expanded ? "" : styles.collapsed}`}
      aria-label={t("chat.workflow.outputTitle")}
    >
      <div className={styles.header}>
        <span className={styles.title}>{t("chat.workflow.outputTitle")}</span>
        <span className={styles.count}>
          {t("chat.workflow.outputCount", { count: entries.length })}
        </span>
        <button
          type="button"
          className={styles.toggle}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {t(
            expanded
              ? "chat.workflow.collapseOutputs"
              : "chat.workflow.expandOutputs",
          )}
          <ChevronDown
            className={expanded ? styles.chevronExpanded : ""}
            size={14}
            aria-hidden="true"
          />
        </button>
      </div>
      {expanded ? (
        <ul className={styles.fileList}>
          {entries.map((entry) => (
            <RunFileRow
              key={entry.path}
              entry={entry}
              agentId={agentId}
              locale={locale}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

import { Download, FilePen, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { agentAttachmentAccessUrl } from "../../../utils/toolMediaBlocks";
import { dockFileBasename } from "../utils/dockFilePath";
import { MessageFileCard } from "./MessageFileCard";
import styles from "./ArtifactFilesCard.module.less";

interface Props {
  agentId: string;
  files: readonly string[];
  onOpenFiles?: () => void;
}

/**
 * Files written by the turn (including via shell/scripts) are collected by the
 * backend into thread artifacts. Show them in the transcript so an answer that
 * only printed a path still gives the user a direct download.
 */
export default function ArtifactFilesCard({
  agentId,
  files,
  onOpenFiles,
}: Props) {
  const { t } = useTranslation();
  const visible = files.filter((path) => path.trim());
  if (!agentId || visible.length === 0) return null;

  return (
    <section className={styles.card} data-testid="turn-artifact-files">
      <div className={styles.header}>
        <span className={styles.title}>
          <FilePen size={16} strokeWidth={2} aria-hidden />
          {t("chat.artifactFilesTitle", {
            count: visible.length,
            defaultValue: "本会话生成/修改了 {{count}} 个文件",
          })}
        </span>
        {onOpenFiles ? (
          <button
            type="button"
            className={styles.openAll}
            onClick={onOpenFiles}
          >
            <FolderOpen size={14} aria-hidden />
            {t("chat.artifactFilesOpenAll", "在文件变更中查看")}
          </button>
        ) : null}
      </div>
      <div className={styles.files}>
        {visible.map((path) => (
          <MessageFileCard
            key={path}
            url={agentAttachmentAccessUrl(agentId, path)}
            filename={dockFileBasename(path)}
            agentId={agentId}
            workspacePath={path}
          />
        ))}
      </div>
      <span className={styles.hint}>
        <Download size={12} aria-hidden />
        {t("chat.artifactFilesDownloadHint", "点击文件右侧按钮下载到本机")}
      </span>
    </section>
  );
}

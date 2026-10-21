import type { ThreadArtifact } from "../../../api/modules/octopThreads";
import { Download, FilePen, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import { agentAttachmentAccessUrl } from "../../../utils/toolMediaBlocks";
import { dockFileBasename } from "../utils/dockFilePath";
import { MessageFileCard } from "./MessageFileCard";
import styles from "./ArtifactFilesCard.module.less";

interface Props {
  agentId: string;
  files: readonly ThreadArtifact[];
  onOpenFiles?: () => void;
}

export default function ArtifactFilesCard({
  agentId,
  files,
  onOpenFiles,
}: Props) {
  const { t } = useTranslation();
  const visible = files.flatMap((artifact) => {
    if (typeof artifact === "string") {
      return artifact.trim()
        ? [{ path: artifact, producerAgentId: agentId }]
        : [];
    }
    if (
      artifact &&
      typeof artifact.producer_agent_id === "string" &&
      artifact.producer_agent_id.trim() &&
      typeof artifact.path === "string" &&
      artifact.path.trim()
    ) {
      return [
        {
          path: artifact.path,
          producerAgentId: artifact.producer_agent_id,
        },
      ];
    }
    return [];
  });
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
        {visible.map(({ path, producerAgentId }) => (
          <MessageFileCard
            key={`${producerAgentId}:${path}`}
            url={agentAttachmentAccessUrl(producerAgentId, path)}
            filename={dockFileBasename(path)}
            agentId={producerAgentId}
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

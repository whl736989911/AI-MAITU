import { CornerUpRight, Paperclip, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { QueuedChatItem } from "../hooks/useChatMessageQueue";
import styles from "../index.module.less";

interface ChatQueuedMessagesProps {
  items: QueuedChatItem[];
  onRemove: (id: string) => void;
  onReclaim: (id: string) => void;
  onSteer?: (item: QueuedChatItem) => boolean;
  onFollowUp?: (item: QueuedChatItem) => boolean;
  onWithdraw?: (item: QueuedChatItem) => boolean;
}

function previewText(item: QueuedChatItem): string {
  return item.text.trim().replace(/\s+/g, " ");
}

export default function ChatQueuedMessages({
  items,
  onRemove,
  onReclaim,
  onSteer,
  onFollowUp,
  onWithdraw,
}: ChatQueuedMessagesProps) {
  const { t } = useTranslation();

  if (items.length === 0) return null;

  return (
    <div
      className={styles.queuedMessages}
      role="list"
      aria-label={t("chat.queue.label")}
    >
      {items.map((item, index) => {
        const text = previewText(item);
        const attachmentCount = item.attachments?.length ?? 0;
        const label =
          text ||
          (attachmentCount > 0
            ? t("chat.queue.attachmentsOnly", {
                count: attachmentCount,
              })
            : t("chat.queue.emptyItem"));

        return (
          <div
            key={item.id}
            className={styles.queuedMessageItem}
            role="listitem"
          >
            <span className={styles.queuedMessageIndex}>{index + 1}</span>
            <button
              type="button"
              className={styles.queuedMessageBody}
              onClick={() =>
                item.remotePending ? onWithdraw?.(item) : onReclaim(item.id)
              }
              title={
                item.remotePending
                  ? t("chat.steer.withdraw")
                  : t("chat.queue.reclaim")
              }
            >
              <span className={styles.queuedMessageText}>{label}</span>
              {attachmentCount > 0 && text && (
                <span
                  className={styles.queuedMessageAttach}
                  title={t("chat.queue.attachmentCount", {
                    count: attachmentCount,
                  })}
                >
                  <Paperclip size={12} />
                  {attachmentCount}
                </span>
              )}
            </button>
            {item.remotePending ? (
              <button
                type="button"
                className={styles.queuedMessageRemove}
                onClick={() => onWithdraw?.(item)}
                aria-label={t("chat.steer.withdraw")}
                title={t("chat.steer.withdraw")}
              >
                <X size={14} />
              </button>
            ) : (
              <>
                {onSteer && (
                  <button
                    type="button"
                    className={styles.queuedMessageRemove}
                    onClick={() => onSteer(item)}
                    aria-label={t("chat.steer.send")}
                    title={t("chat.steer.send")}
                  >
                    <CornerUpRight size={14} />
                  </button>
                )}
                {onFollowUp && (
                  <button
                    type="button"
                    className={styles.queuedMessageRemove}
                    onClick={() => onFollowUp(item)}
                    aria-label={t("chat.steer.followUp")}
                    title={t("chat.steer.followUp")}
                  >
                    <CornerUpRight size={14} />
                  </button>
                )}
              </>
            )}
            {item.remoteStatus && (
              <span className={styles.queuedMessageText}>
                {t(`chat.steer.status.${item.remoteStatus}`)}
              </span>
            )}
            {!item.remotePending && (
              <button
                type="button"
                className={styles.queuedMessageRemove}
                onClick={() => onRemove(item.id)}
                aria-label={t("chat.queue.remove")}
                title={t("chat.queue.remove")}
              >
                <X size={14} />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

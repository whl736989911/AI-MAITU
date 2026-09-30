import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Hammer, ListTodo, MessageCircleQuestion } from "lucide-react";
import { Popover, Tooltip } from "antd";
import type { ConversationMode } from "../utils/conversationMode";
import styles from "../index.module.less";

const OPTIONS: ReadonlyArray<{
  mode: ConversationMode;
  Icon: typeof Hammer;
}> = [
  { mode: "craft", Icon: Hammer },
  { mode: "plan", Icon: ListTodo },
  { mode: "ask", Icon: MessageCircleQuestion },
];

interface Props {
  compact?: boolean;
  conversationMode: ConversationMode;
  onChange: (mode: ConversationMode) => void;
}

export default function ConversationModePicker({
  compact = false,
  conversationMode,
  onChange,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const selected =
    OPTIONS.find((item) => item.mode === conversationMode) ?? OPTIONS[0];
  const Icon = selected.Icon;
  const trigger = (
    <button
      className={`${styles.secondaryBtn} ${
        compact ? "" : styles.modelPickerBtn
      } ${conversationMode !== "craft" ? styles.secondaryBtnModelActive : ""}`}
      type="button"
      data-testid="conversation-mode-picker"
      aria-label={t(`chat.conversationMode.${conversationMode}`)}
      aria-haspopup="menu"
      aria-expanded={open}
    >
      <Icon size={16} />
      {!compact && (
        <span className={styles.modelPickerLabel}>
          {t(`chat.conversationMode.${conversationMode}`)}
        </span>
      )}
    </button>
  );
  return (
    <Popover
      trigger="click"
      placement="topLeft"
      open={open}
      onOpenChange={setOpen}
      overlayClassName={styles.modelPopover}
      content={
        <div className={styles.modeMenu} role="menu">
          {OPTIONS.map(({ mode, Icon: ModeIcon }) => (
            <button
              key={mode}
              type="button"
              role="menuitemradio"
              aria-checked={conversationMode === mode}
              className={`${styles.modeMenuItem} ${
                conversationMode === mode ? styles.modeMenuItemActive : ""
              }`}
              onClick={() => {
                onChange(mode);
                setOpen(false);
              }}
            >
              <span className={styles.modeMenuTitle}>
                <ModeIcon size={16} />
                {t(`chat.conversationMode.${mode}`)}
              </span>
              <span className={styles.modeMenuHint}>
                {t(`chat.conversationMode.${mode}Hint`)}
              </span>
            </button>
          ))}
        </div>
      }
    >
      {compact ? (
        trigger
      ) : (
        <Tooltip title={t("chat.conversationMode.picker")}>{trigger}</Tooltip>
      )}
    </Popover>
  );
}

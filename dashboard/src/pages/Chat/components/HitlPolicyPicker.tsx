import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Button, Input, Popover } from "antd";
import { useTranslation } from "react-i18next";
import type { HitlSessionPolicy } from "../../../api/modules/octopThreads";
import styles from "../index.module.less";

interface Props {
  policy: HitlSessionPolicy;
  onChange: (policy: HitlSessionPolicy) => void;
}

export default function HitlPolicyPicker({ policy, onChange }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [toolsText, setToolsText] = useState((policy.tools ?? []).join(", "));
  useEffect(() => {
    setToolsText((policy.tools ?? []).join(", "));
  }, [policy]);
  const selectedTools = [
    ...new Set(
      toolsText
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean),
    ),
  ].slice(0, 64);
  const saveTools = () => {
    onChange({ mode: "allow_tools", tools: selectedTools });
    setOpen(false);
  };
  return (
    <Popover
      trigger="click"
      placement="topLeft"
      open={open}
      onOpenChange={setOpen}
      overlayClassName={styles.modelPopover}
      content={
        <div className={styles.modeMenu}>
          <button
            className={styles.modeMenuItem}
            type="button"
            onClick={() => {
              onChange({ mode: "ask" });
              setOpen(false);
            }}
          >
            {t("chat.hitl.policy.ask")}
          </button>
          <button
            className={styles.modeMenuItem}
            type="button"
            onClick={() => {
              onChange({ mode: "allow_all" });
              setOpen(false);
            }}
          >
            {t("chat.hitl.policy.allowAll")}
          </button>
          <div className={styles.modeMenuItem}>
            <label htmlFor="hitl-policy-tools">
              {t("chat.hitl.policy.allowTools")}
            </label>
            <Input
              id="hitl-policy-tools"
              value={toolsText}
              onChange={(event) => setToolsText(event.target.value)}
              placeholder="tool_a, tool_b"
              aria-label={t("chat.hitl.policy.toolNames")}
            />
            <Button
              size="small"
              disabled={!selectedTools.length}
              onClick={saveTools}
            >
              {t("common.save", "Save")}
            </Button>
          </div>
        </div>
      }
    >
      <button
        type="button"
        className={styles.secondaryBtn}
        data-testid="hitl-policy-picker"
        aria-label={t("chat.hitl.policy.picker")}
      >
        <ShieldCheck size={16} />
      </button>
    </Popover>
  );
}

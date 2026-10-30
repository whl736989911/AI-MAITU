import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Button, Typography } from "antd";
import { Wand2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  textAssistApi,
  type TextAssistKind,
} from "../../api/modules/textAssist";
import { apiErrorMessage } from "../../utils/apiError";
import { message } from "@/utils/antdMessage";

const { Paragraph } = Typography;
export type TextAssistBarProps = {
  kind: TextAssistKind;
  /** Stable identity of the entity whose field is being edited. */
  scopeKey?: string | number | null;
  /** Current field draft (may be empty to generate from the entity name). */
  getText: () => string;
  /** Entity name used as the prompt context. */
  getName: () => string;
  /** Optional existing-agent scope for access and model selection. */
  agentId?: string | null;
  /** Optional model selected in the surrounding form. */
  getDefaultModel?: () => string | null | undefined;
  /** Called only after the user explicitly accepts the preview. */
  onApply: (text: string) => void;
  disabled?: boolean;
  className?: string;
};

type TextAssistScope = Readonly<{
  kind: TextAssistKind;
  agentId: string | null;
  scopeKey: string | number | null;
}>;

export function TextAssistBar({
  kind,
  scopeKey,
  getText,
  getName,
  agentId,
  getDefaultModel,
  onApply,
  disabled = false,
  className,
}: TextAssistBarProps) {
  const { t, i18n } = useTranslation();
  const [activeRequest, setActiveRequest] = useState<{
    id: number;
    scope: TextAssistScope;
  } | null>(null);
  const [preview, setPreview] = useState<{
    scope: TextAssistScope;
    text: string;
  } | null>(null);
  const nextRequestId = useRef(0);
  const scopeAgentId = agentId ?? null;
  const scopeEntityKey = scopeKey ?? null;
  const scope = useMemo<TextAssistScope>(
    () => ({ kind, agentId: scopeAgentId, scopeKey: scopeEntityKey }),
    [kind, scopeAgentId, scopeEntityKey],
  );
  const currentScope = useRef<TextAssistScope | null>(scope);
  useLayoutEffect(() => {
    currentScope.current = scope;
    return () => {
      if (currentScope.current === scope) currentScope.current = null;
    };
  }, [scope]);
  const loading = activeRequest?.scope === scope;
  const visiblePreview = preview?.scope === scope ? preview.text : null;

  const handleGenerate = useCallback(async () => {
    if (activeRequest?.scope === scope || disabled) return;
    const name = (getName() || "").trim();
    if (!name) {
      message.warning(t("textAssist.nameRequired"));
      return;
    }
    const requestId = ++nextRequestId.current;
    setActiveRequest({ id: requestId, scope });
    setPreview(null);
    try {
      const result = await textAssistApi.assist({
        kind,
        text: (getText() || "").trim(),
        context: { name },
        locale: i18n.language?.startsWith("zh") ? "zh" : "en",
        default_model: getDefaultModel?.(),
        agent_id: agentId,
      });
      const next = (result.text || "").trim();
      if (currentScope.current !== scope) return;
      if (!next) {
        message.error(t("textAssist.emptyResult"));
        return;
      }
      setPreview({ scope, text: next });
    } catch (err: unknown) {
      if (currentScope.current === scope) {
        message.error(apiErrorMessage(err, t("textAssist.failed"), t));
      }
    } finally {
      if (currentScope.current === scope) {
        setActiveRequest((active) =>
          active?.id === requestId ? null : active,
        );
      }
    }
  }, [
    activeRequest?.scope,
    agentId,
    disabled,
    getDefaultModel,
    getName,
    getText,
    i18n.language,
    kind,
    scope,
    t,
  ]);

  const handleApply = useCallback(() => {
    if (visiblePreview === null || disabled) return;
    onApply(visiblePreview);
    setPreview(null);
  }, [disabled, onApply, visiblePreview]);

  return (
    <div className={className}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: "var(--fn-text-tertiary)", fontSize: 12 }}>
          {t("textAssist.hint")}
        </span>
        <Button
          type="link"
          size="small"
          icon={<Wand2 size={14} />}
          loading={loading}
          disabled={disabled || loading}
          onClick={() => void handleGenerate()}
        >
          {t("textAssist.action")}
        </Button>
      </div>
      {visiblePreview !== null ? (
        <div
          style={{
            marginTop: 8,
            padding: 10,
            border: "1px solid var(--fn-border-color, #d9d9d9)",
            borderRadius: 6,
          }}
        >
          <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 8 }}>
            {visiblePreview}
          </Paragraph>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button size="small" onClick={() => setPreview(null)}>
              {t("textAssist.discard")}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={handleApply}
              disabled={disabled}
            >
              {t("textAssist.apply")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default TextAssistBar;

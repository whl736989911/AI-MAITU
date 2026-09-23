import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox, Form, Select } from "antd";

import { connectorsApi } from "../../../api/modules/connectors";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { useCurrentUser } from "../../../hooks/useCurrentUser";

/** One selectable knowledge base, as both the expert and feature forms need it. */
export interface ComposerKnowledgeBaseOption {
  id: string;
  name: string;
}

/** One selectable connector instance, keyed by its MCP server name. */
export interface ComposerConnectorOption {
  value: string;
  label: string;
}

export interface ExpertComposerDefaultsFieldsProps {
  /**
   * Knowledge bases to offer. Omit to load the caller's own list, which is what
   * the expert forms do; the feature capability editor passes the list the
   * server resolved for this caller, so the editor offers exactly what a run
   * would be allowed to use.
   */
  knowledgeBases?: ComposerKnowledgeBaseOption[];
  /** Connectors to offer; omit to load the caller's own instances. */
  connectors?: ComposerConnectorOption[];
  /** True while the caller of a pre-resolved list is still loading it. */
  optionsLoading?: boolean;
  /**
   * Offer "inherit" next to each list.
   *
   * An expert always stores both lists (empty means none), so its forms leave
   * this off. A feature's capability layer distinguishes "declares nothing"
   * from "declares none" — omitting the field from ``feature.json`` — so its
   * editor needs a way to express the first without losing the second.
   */
  inheritable?: boolean;
  /** Label of the inherit checkbox; the feature editor supplies its own copy. */
  inheritLabel?: string;
}

/**
 * One list that may either be declared or inherited.
 *
 * The distinction is the whole point of the toggle: a cleared select writes
 * ``undefined`` (declares nothing — the run keeps whatever the agent has) while
 * an emptied one writes ``[]`` (declares none). Collapsing them would silently
 * widen or narrow every run of the definition.
 */
function ScopeField({
  name,
  label,
  hint,
  placeholder,
  options,
  loading,
  inheritable,
  inheritLabel,
}: {
  name: string;
  label: string;
  hint: string;
  placeholder: string;
  options: { value: string; label: string }[];
  loading: boolean;
  inheritable: boolean;
  inheritLabel: string;
}) {
  const form = Form.useFormInstance<Record<string, unknown>>();
  const value = Form.useWatch(name, form);
  const inherited = inheritable && value === undefined;

  const select = (
    <Select
      mode="multiple"
      allowClear
      showSearch
      optionFilterProp="label"
      loading={loading}
      disabled={inherited}
      options={options}
      placeholder={placeholder}
    />
  );

  if (!inheritable) {
    return (
      <Form.Item name={name} label={label} extra={hint}>
        {select}
      </Form.Item>
    );
  }

  return (
    <Form.Item label={label} extra={hint}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Form.Item name={name} noStyle>
          {select}
        </Form.Item>
        <Checkbox
          checked={inherited}
          onChange={(event) =>
            form.setFieldValue(name, event.target.checked ? undefined : [])
          }
        >
          {inheritLabel}
        </Checkbox>
      </div>
    </Form.Item>
  );
}

/**
 * Knowledge bases and connectors of one composer / one feature's agent.
 *
 * Shared by the expert create/edit drawers and the feature capability editor:
 * both pick from the caller's own readable knowledge bases and own connector
 * instances, which is the design rule for data (5.2) — the configuration is
 * authored once, the data follows whoever runs it.
 */
export default function ExpertComposerDefaultsFields({
  knowledgeBases: providedKnowledgeBases,
  connectors: providedConnectors,
  optionsLoading = false,
  inheritable = false,
  inheritLabel = "",
}: ExpertComposerDefaultsFieldsProps = {}) {
  const { t } = useTranslation();
  const currentUserId = useCurrentUser()?.id ?? null;
  const [loadedKnowledgeBases, setLoadedKnowledgeBases] = useState<
    ComposerKnowledgeBaseOption[]
  >([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [loadedConnectors, setLoadedConnectors] = useState<
    ComposerConnectorOption[]
  >([]);
  const [connectorsLoading, setConnectorsLoading] = useState(false);

  const loadKnowledgeBases = providedKnowledgeBases === undefined;
  const loadConnectors = providedConnectors === undefined;

  useEffect(() => {
    if (!loadKnowledgeBases) return;
    let cancelled = false;
    setKnowledgeLoading(true);
    knowledgeBasesApi
      .list()
      .then((bases) => {
        if (!cancelled) {
          setLoadedKnowledgeBases(
            bases.map((base) => ({ id: base.id, name: base.name })),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setLoadedKnowledgeBases([]);
      })
      .finally(() => {
        if (!cancelled) setKnowledgeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadKnowledgeBases]);

  useEffect(() => {
    if (!loadConnectors) return;
    let cancelled = false;
    setConnectorsLoading(true);
    connectorsApi
      .listInstances()
      .then((instances) => {
        if (cancelled) return;
        setLoadedConnectors(
          (instances ?? [])
            .filter((item) => item.status === "active" && item.has_credentials)
            .map((item) => ({
              value: item.mcp_server_name,
              label:
                currentUserId !== null && item.owner_user_id !== currentUserId
                  ? `${item.display_name} · ${
                      item.owner_display_name ||
                      item.owner_username ||
                      item.owner_user_id
                    }`
                  : item.display_name || item.mcp_server_name,
            })),
        );
      })
      .catch(() => {
        if (!cancelled) setLoadedConnectors([]);
      })
      .finally(() => {
        if (!cancelled) setConnectorsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentUserId, loadConnectors]);

  const knowledgeBases = providedKnowledgeBases ?? loadedKnowledgeBases;
  const connectors = providedConnectors ?? loadedConnectors;

  return (
    <>
      <ScopeField
        name="knowledge_base_ids"
        label={t("experts.knowledgeBasesLabel")}
        hint={t("experts.knowledgeBasesHint")}
        placeholder={t("experts.knowledgeBasesPlaceholder")}
        loading={loadKnowledgeBases ? knowledgeLoading : optionsLoading}
        options={knowledgeBases.map((base) => ({
          value: base.id,
          label: base.name,
        }))}
        inheritable={inheritable}
        inheritLabel={inheritLabel}
      />
      <ScopeField
        name="mcp_servers"
        label={t("experts.connectorsLabel")}
        hint={t("experts.connectorsHint")}
        placeholder={t("experts.connectorsPlaceholder")}
        loading={loadConnectors ? connectorsLoading : optionsLoading}
        options={connectors}
        inheritable={inheritable}
        inheritLabel={inheritLabel}
      />
    </>
  );
}

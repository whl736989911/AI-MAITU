import { useEffect, useMemo, useState } from "react";
import { Form, Input, Select } from "antd";
import RootDirSelect from "./RootDirSelect";
import { MinusCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  BUILTIN_BACKENDS,
  DEFAULT_BACKEND,
  fetchFilesystemDefaults,
  type BackendOption,
  type FilesystemDefaults,
  type PathMapping,
  builtinDesc,
  builtinLabel,
  isValidCompositePath,
} from "./agentBackendForm";
import { HOST_FS_ROOT } from "./rootDirTree";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { isSystemAdmin } from "../../../utils/permissions";
import styles from "../index.module.less";

interface AgentBackendFieldsProps {
  backends: BackendOption[];
  backendsLoading: boolean;
  backendChoice: string;
  pathMappings: PathMapping[];
  /** Hide host backend controls when excluded by the effective server profile. */
  hostExecutionRestricted?: boolean;
  /** In unrestricted create mode, fills an empty root_dir from filesystem defaults. */
  rootDirMode?: "create" | "edit";
  disabled?: boolean;
  onAddPathMapping: () => void;
  onRemovePathMapping: (index: number) => void;
  onUpdatePathMapping: (
    index: number,
    field: keyof PathMapping,
    value: string,
  ) => void;
}

export default function AgentBackendFields({
  backends,
  backendsLoading,
  backendChoice,
  pathMappings,
  rootDirMode = "create",
  hostExecutionRestricted = false,
  disabled = false,
  onAddPathMapping,
  onRemovePathMapping,
  onUpdatePathMapping,
}: AgentBackendFieldsProps) {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const user = useCurrentUser();
  const [fsDefaults, setFsDefaults] = useState<FilesystemDefaults | null>(null);
  const watchedRootDir = Form.useWatch("root_dir", form) as string | undefined;
  const admin = isSystemAdmin(user) && !hostExecutionRestricted;

  useEffect(() => {
    // ``/filesystem/defaults`` requires the admin role — plain users type the
    // root dir themselves, so skip the request instead of eating a 403.
    if (!admin) {
      setFsDefaults(null);
      return;
    }
    let cancelled = false;
    fetchFilesystemDefaults()
      .then((defaults) => {
        if (!cancelled) setFsDefaults(defaults);
      })
      .catch(() => {
        if (!cancelled) setFsDefaults(null);
      });
    return () => {
      cancelled = true;
    };
  }, [admin]);

  useEffect(() => {
    if (!fsDefaults || rootDirMode !== "create") return;
    const current = watchedRootDir;
    if (!current) {
      form.setFieldValue("root_dir", fsDefaults.default_root_dir);
    }
  }, [fsDefaults, form, watchedRootDir, rootDirMode]);

  const treeRoot = fsDefaults?.tree_root ?? HOST_FS_ROOT;
  const configuredBackends = backends.filter((b) => b.enabled);
  const routeBackendOptions = useMemo(() => {
    const builtins = BUILTIN_BACKENDS.map((mode) => ({
      value: mode,
      label: builtinLabel(mode, t),
    }));
    const configured = configuredBackends.map((b) => ({
      value: `named:${b.name}` as const,
      label: `${b.name} (${b.kind})`,
    }));
    return [...builtins, ...configured];
  }, [configuredBackends, t]);

  const backendSelectOptions = useMemo(() => {
    const configured = configuredBackends.map((b) => ({
      value: `named:${b.name}`,
      label: `${b.name} (${b.kind})`,
    }));
    if (hostExecutionRestricted) {
      return configured.length
        ? [
            {
              label: t("experts.backendGroups.configured"),
              options: configured,
            },
          ]
        : [];
    }
    return [
      {
        label: t("experts.backendGroups.builtin"),
        options: [
          ...BUILTIN_BACKENDS.map((mode) => ({
            value: mode,
            label: builtinLabel(mode, t),
          })),
          {
            value: "composite",
            label: t("experts.backendModes.composite"),
          },
        ],
      },
      ...(configured.length
        ? [
            {
              label: t("experts.backendGroups.configured"),
              options: configured,
            },
          ]
        : []),
    ];
  }, [configuredBackends, hostExecutionRestricted, t]);

  const desc = builtinDesc(backendChoice, t);

  const pathHasError = (path: string) =>
    path.trim() === "/" ||
    (path.trim().length > 0 && !isValidCompositePath(path));

  if (hostExecutionRestricted && configuredBackends.length === 0) return null;
  return (
    <>
      <Form.Item
        name="backend_choice"
        label={t("experts.backendLabel")}
        initialValue={hostExecutionRestricted ? undefined : DEFAULT_BACKEND}
      >
        <Select
          disabled={disabled}
          loading={backendsLoading}
          options={backendSelectOptions}
          placeholder={t("experts.backendPlaceholder")}
        />
      </Form.Item>

      {desc ? (
        <div style={{ margin: "-8px 0 12px" }}>
          <p
            style={{
              fontSize: 12,
              color: "var(--fn-text-tertiary)",
              margin: 0,
            }}
          >
            {desc.text}
          </p>
          {desc.warning ? (
            <p style={{ fontSize: 12, color: "#d48806", margin: "4px 0 0" }}>
              {desc.warning}
            </p>
          ) : null}
        </div>
      ) : null}

      {!hostExecutionRestricted &&
        (backendChoice === "local_shell" || backendChoice === "filesystem") && (
          <>
            <Form.Item
              name="root_dir"
              label={t("experts.backendRootDir")}
              initialValue={
                rootDirMode === "create"
                  ? fsDefaults?.default_root_dir
                  : undefined
              }
            >
              <RootDirSelect
                treeRoot={treeRoot}
                disabled={disabled || rootDirMode === "edit"}
              />
            </Form.Item>
            <div style={{ margin: "-8px 0 12px" }}>
              {rootDirMode === "edit" ? (
                <p
                  style={{
                    fontSize: 12,
                    color: "var(--fn-text-tertiary)",
                    margin: 0,
                  }}
                >
                  {t("experts.backendRootDirImmutableHint")}
                </p>
              ) : (
                <>
                  {/* Browsing is admin-only; without the defaults probe the
                    "you may browse" promise is false for plain users. */}
                  {admin ? (
                    <p
                      style={{
                        fontSize: 12,
                        color: "var(--fn-text-tertiary)",
                        margin: 0,
                      }}
                    >
                      {fsDefaults?.in_container
                        ? t("experts.backendRootDirDescContainer")
                        : t("experts.backendRootDirDesc", {
                            home: fsDefaults?.home ?? "~",
                          })}
                    </p>
                  ) : null}
                  <p
                    style={{
                      fontSize: 12,
                      color: "var(--fn-text-tertiary)",
                      margin: "4px 0 0",
                    }}
                  >
                    {t("experts.backendRootDirJailHint")}
                  </p>
                </>
              )}
            </div>
          </>
        )}

      {!hostExecutionRestricted && backendChoice === "composite" && (
        <>
          <Form.Item
            name="composite_default"
            label={t("experts.backendModes.compositeDefault")}
            initialValue={DEFAULT_BACKEND}
          >
            <Select disabled={disabled} options={routeBackendOptions} />
          </Form.Item>

          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>
              {t("experts.backendPathMappings")}
            </div>
            <p
              style={{
                fontSize: 12,
                color: "var(--fn-text-tertiary)",
                margin: "0 0 8px",
              }}
            >
              {t("experts.backendModes.pathMappingHint")}
            </p>
            {pathMappings.map((mapping, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 8,
                  marginBottom: 8,
                  alignItems: "center",
                }}
              >
                <Input
                  disabled={disabled}
                  placeholder="/data"
                  value={mapping.path}
                  status={pathHasError(mapping.path) ? "error" : undefined}
                  onChange={(e) =>
                    onUpdatePathMapping(i, "path", e.target.value)
                  }
                  style={{ flex: 1 }}
                />
                <Select
                  disabled={disabled}
                  placeholder={t("experts.backendModes.routeBackend")}
                  options={routeBackendOptions}
                  value={mapping.backend || undefined}
                  onChange={(v: string) => onUpdatePathMapping(i, "backend", v)}
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onRemovePathMapping(i)}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--fn-text-tertiary)",
                    padding: 0,
                  }}
                >
                  <MinusCircle size={16} />
                </button>
              </div>
            ))}
            <button
              type="button"
              className={styles.cardBtn}
              disabled={disabled}
              onClick={onAddPathMapping}
            >
              {t("experts.addPathMapping")}
            </button>
          </div>
        </>
      )}
    </>
  );
}

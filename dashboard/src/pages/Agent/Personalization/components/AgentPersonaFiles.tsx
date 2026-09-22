/**
 * The persona files of a *feature's* own agent — the workspace ``.md`` documents
 * a feature's behaviour and voice are written in, edited on the Personalization
 * page while that feature is the page's scope.
 *
 * Four files, and the rest are absences with reasons (design 2.2 / 7.1):
 *   - ``IDENTITY.md`` / ``SOUL.md`` / ``AGENTS.md`` are the persona proper, and
 *     the same three an expert configures under "配置文件".
 *   - ``USER.md`` — "the agent's owner" has no referent here: a definition runs
 *     for every caller, so there is no one user for the file to describe.
 *   - ``HEARTBEAT.md`` — a heartbeat needs an agent that is resident and a cron
 *     job that fires as somebody; neither exists for this one.
 *   - ``MEMORY.md`` — one file for the agent means one memory shared by every
 *     caller, which contradicts design 5.2 (data follows the caller).
 *   - ``PROMPT.md`` is not here either, and belongs to the definition editor: it
 *     is the *task* prompt, versioned with ``feature.json`` and carried into the
 *     run snapshot, while these are the agent's own living documents.
 *
 * ``BOOTSTRAP.md`` is available but folded away by default: it describes a first
 * conversation, and a feature run is a short task with no first conversation to
 * open — worth editing, not worth the space.
 *
 * The list is *fixed* rather than a workspace listing, because an author has to
 * be able to start a file that does not exist yet (a fresh agent's workspace is
 * empty). ``fetchConfigMdFiles`` — the experts' own lister — is asked only which
 * of them are already there, so a card never claims a file exists when it does
 * not. Editing is the experts' own ``FileEditModal``: the same markdown editor,
 * the same workspace endpoints, and the same reload after saving, which is what
 * makes an edit take effect rather than merely land on disk.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Collapse, Spin } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { apiErrorMessage } from "../../../../utils/apiError";
import FileEditModal from "../../../Experts/components/FileEditModal";
import { fetchConfigMdFiles } from "../../../Experts/components/expertFileGroups";
import { metaForFile } from "../../../Experts/components/iconForName";
import styles from "./AgentPersonaFiles.module.less";

interface PersonaFile {
  /** Workspace path, as the file endpoints take it. */
  path: string;
  /** Index into ``personalization.cards.*`` — the experts' own labels. */
  key: string;
}

/** The cards, in reading order. */
const PERSONA_FILES: readonly PersonaFile[] = [
  { path: "/IDENTITY.md", key: "identity" },
  { path: "/SOUL.md", key: "soul" },
  { path: "/AGENTS.md", key: "agents" },
];

/** Editable, but folded away until asked for — see the module docstring. */
const BOOTSTRAP_FILE: PersonaFile = { path: "/BOOTSTRAP.md", key: "bootstrap" };

function PersonaFileCard({
  file,
  existing,
  onEdit,
}: {
  file: PersonaFile;
  /** The workspace listing, or ``null`` while it has not answered. */
  existing: Set<string> | null;
  onEdit: (path: string) => void;
}) {
  const { t } = useTranslation();
  const filename = file.path.replace(/^\//, "");
  // ``metaForFile`` keys on the bare filename and is the experts' own map, so a
  // card is recognisable as the same file the expert drawer shows.
  const meta = metaForFile(filename, t);
  const exists = existing?.has(file.path) ?? null;

  return (
    <button
      type="button"
      className={styles.personaCard}
      onClick={() => onEdit(file.path)}
    >
      <span
        className={styles.personaCardIcon}
        style={{ color: meta.color, background: `${meta.color}1a` }}
      >
        {meta.icon}
      </span>
      <span className={styles.personaCardBody}>
        <span className={styles.personaCardLabel}>
          {t(`personalization.cards.${file.key}`)}
        </span>
        <span className={styles.personaCardDesc}>
          {t(`personalization.cards.${file.key}Desc`)}
        </span>
        <span className={styles.personaCardFoot}>
          <span className={styles.personaCardPath}>{filename}</span>
          <span className={styles.personaCardState}>
            {exists === false ? t("personalization.filesMissing") : ""}
          </span>
        </span>
      </span>
    </button>
  );
}

export default function AgentPersonaFiles({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [existing, setExisting] = useState<Set<string> | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<unknown>(null);
  const [editing, setEditing] = useState<string | null>(null);
  /** Bumped after a save: the workspace listing is what the cards report. */
  const [revision, setRevision] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setExisting(new Set(await fetchConfigMdFiles(agentId)));
      setFailure(null);
    } catch (err) {
      // Never answered with an empty set: "this workspace has no files" and
      // "the listing failed" would otherwise be the same screen.
      setExisting(null);
      setFailure(err);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  if (loading && existing === null) {
    return (
      <div className={styles.status}>
        <Spin size="small" />
      </div>
    );
  }

  if (failure !== null) {
    // The server's own words where it gave any; the title alone is better than
    // the same sentence twice.
    const title = t("personalization.filesFailed");
    const reason = apiErrorMessage(failure, title, t);
    return (
      <Alert
        type="error"
        showIcon
        message={title}
        description={reason === title ? undefined : reason}
        action={
          <Button
            size="small"
            icon={<RefreshCw size={13} />}
            onClick={() => void load()}
          >
            {t("features.settingsCapabilityRetry")}
          </Button>
        }
      />
    );
  }

  return (
    <div className={styles.personaFiles}>
      <div className={styles.personaGrid}>
        {PERSONA_FILES.map((file) => (
          <PersonaFileCard
            key={file.path}
            file={file}
            existing={existing}
            onEdit={setEditing}
          />
        ))}
      </div>

      <Collapse
        ghost
        className={styles.personaBootstrap}
        items={[
          {
            key: BOOTSTRAP_FILE.key,
            label: (
              <span className={styles.personaBootstrapLabel}>
                <span>{t(`personalization.cards.${BOOTSTRAP_FILE.key}`)}</span>
                <span className={styles.personaBootstrapDesc}>
                  {t(`personalization.cards.${BOOTSTRAP_FILE.key}Desc`)}
                </span>
              </span>
            ),
            children: (
              <PersonaFileCard
                file={BOOTSTRAP_FILE}
                existing={existing}
                onEdit={setEditing}
              />
            ),
          },
        ]}
      />

      <FileEditModal
        open={editing !== null}
        agentId={agentId}
        filePath={editing}
        onClose={() => setEditing(null)}
        onSaved={() => setRevision((n) => n + 1)}
      />
    </div>
  );
}

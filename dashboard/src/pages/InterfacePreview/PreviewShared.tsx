import { useState } from "react";
import {
  File,
  FileSpreadsheet,
  FileText,
  DraftingCompass,
  SearchX,
  Search,
  Bot,
  ArrowUpRight,
  ArrowDownUp,
} from "lucide-react";
import { BRAND } from "../../brand.generated";
import type {
  PreviewCopy,
  PreviewDocument,
  PreviewDocumentStatus,
  PreviewScope,
  PreviewRecord,
  PreviewVariantProps,
} from "./model";
import styles from "./PreviewShared.module.less";

export function PreviewLogo({
  compact = false,
  inverse = false,
}: {
  compact?: boolean;
  inverse?: boolean;
}) {
  return (
    <div className={`${styles.logo} ${inverse ? styles.inverse : ""}`}>
      <img src={BRAND.logo.mark} alt="" width={34} height={34} />
      {!compact && (
        <div>
          <strong>{BRAND.shortName.en}</strong>
          <span>{BRAND.shortName.zh}</span>
        </div>
      )}
    </div>
  );
}

export function DocumentTypeIcon({
  kind,
  size = 18,
}: {
  kind: PreviewDocument["kind"];
  size?: number;
}) {
  const Icon =
    kind === "XLSX"
      ? FileSpreadsheet
      : kind === "DWG"
      ? DraftingCompass
      : kind === "BIN"
      ? File
      : FileText;
  return (
    <span
      className={`${styles.fileIcon} ${
        styles[kind.toLowerCase()] || styles.bin
      }`}
      aria-hidden="true"
    >
      <Icon size={size} strokeWidth={1.7} />
    </span>
  );
}

export function StatusBadge({
  status,
  copy,
}: {
  status: PreviewDocumentStatus;
  copy: PreviewCopy;
}) {
  const label =
    status === "ready"
      ? copy.ready
      : status === "archive_only"
      ? copy.archived
      : copy.processing;
  return (
    <span className={`${styles.status} ${styles[status]}`}>
      <i aria-hidden="true" />
      {label}
    </span>
  );
}

export function EmptyState({ copy }: { copy: PreviewCopy }) {
  return (
    <div className={styles.empty} role="status">
      <SearchX size={26} strokeWidth={1.5} aria-hidden="true" />
      <span>{copy.noResults}</span>
    </div>
  );
}

export function scopeLabel(scope: PreviewScope, copy: PreviewCopy) {
  return scope === "personal"
    ? copy.personalScope
    : scope === "department"
    ? copy.departmentScope
    : copy.enterpriseScope;
}

export function DueBadge({
  record,
  copy,
}: {
  record: PreviewRecord;
  copy: PreviewCopy;
}) {
  const tone =
    record.remainingDays < 0
      ? "overdue"
      : record.remainingDays <= 7
      ? "soon"
      : "normal";
  return (
    <span className={`${styles.dueBadge} ${styles[tone]}`}>
      {record.remainingDays < 0
        ? `${copy.overdue} ${Math.abs(record.remainingDays)} ${copy.days}`
        : `${record.remainingDays} ${copy.days}`}
    </span>
  );
}

export function DocumentBrowser({
  data,
  copy,
  actions,
  compact = false,
  query: controlledQuery,
  onQueryChange,
  hideSearch = false,
}: PreviewVariantProps & {
  compact?: boolean;
  query?: string;
  onQueryChange?: (query: string) => void;
  hideSearch?: boolean;
}) {
  const [localQuery, setLocalQuery] = useState("");
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");
  const query = controlledQuery ?? localQuery;
  const changeQuery = onQueryChange ?? setLocalQuery;
  const needle = query.trim().toLowerCase();
  const documents = data.documents.filter(
    (document) =>
      (!needle ||
        `${document.name} ${document.kind} ${document.owner}`
          .toLowerCase()
          .includes(needle)) &&
      (source === "all" || document.source === source) &&
      (status === "all" || document.status === status),
  );
  return (
    <div className={`${styles.browser} ${compact ? styles.compact : ""}`}>
      <div className={styles.tools}>
        {!hideSearch && (
          <label className={styles.search}>
            <Search size={16} aria-hidden="true" />
            <input
              aria-label={copy.searchKnowledge}
              placeholder={copy.searchKnowledge}
              value={query}
              onChange={(event) => changeQuery(event.target.value)}
            />
          </label>
        )}
        <select
          aria-label={copy.allSources}
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          <option value="all">{copy.allSources}</option>
          <option value="platform">{copy.platformSource}</option>
          <option value="folder">{copy.folderSource}</option>
        </select>
        <select
          aria-label={copy.filterStatus}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">{copy.allDocuments}</option>
          <option value="ready">{copy.ready}</option>
          <option value="archive_only">{copy.archived}</option>
          <option value="processing">{copy.processing}</option>
        </select>
        <span className={styles.resultCount}>
          {documents.length} {copy.documents}
        </span>
      </div>
      {documents.length ? (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{copy.knowledge}</th>
                <th>{copy.sources}</th>
                <th>{copy.status}</th>
                <th>{copy.permissionScope}</th>
                <th>{copy.owner}</th>
                <th>{copy.updated}</th>
                <th>
                  <span className={styles.srOnly}>{copy.actions}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {documents.map((document) => (
                <tr key={document.id}>
                  <td>
                    <button
                      className={styles.documentName}
                      onClick={() => actions.openDocument(document)}
                    >
                      <DocumentTypeIcon kind={document.kind} />
                      <span>
                        <strong>{document.name}</strong>
                        <small>
                          {document.kind} · {document.sizeLabel}
                        </small>
                      </span>
                    </button>
                  </td>
                  <td>
                    {document.source === "platform"
                      ? copy.platformSource
                      : copy.folderSource}
                  </td>
                  <td>
                    <StatusBadge status={document.status} copy={copy} />
                  </td>
                  <td>
                    <span className={styles.scope}>
                      {scopeLabel(document.scope, copy)}
                    </span>
                  </td>
                  <td>{document.owner}</td>
                  <td className={styles.date}>{document.updatedLabel}</td>
                  <td>
                    <button
                      className={styles.iconButton}
                      aria-label={`${copy.preview} ${document.name}`}
                      onClick={() => actions.openDocument(document)}
                    >
                      <ArrowUpRight size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState copy={copy} />
      )}
    </div>
  );
}

export function RecordBrowser({
  data,
  copy,
  actions,
  compact = false,
}: PreviewVariantProps & { compact?: boolean }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState("asc");
  const needle = query.trim().toLowerCase();
  const records = data.records
    .filter((record) => {
      const matching =
        `${record.name} ${record.code} ${record.department} ${record.responsible}`
          .toLowerCase()
          .includes(needle);
      const tone =
        record.remainingDays < 0
          ? "overdue"
          : record.remainingDays <= 7
          ? "soon"
          : "normal";
      return matching && (status === "all" || status === tone);
    })
    .slice()
    .sort((left, right) =>
      sort === "asc"
        ? left.remainingDays - right.remainingDays
        : right.remainingDays - left.remainingDays,
    );
  return (
    <div className={`${styles.browser} ${compact ? styles.compact : ""}`}>
      <div className={styles.tools}>
        <label className={styles.search}>
          <Search size={16} aria-hidden="true" />
          <input
            aria-label={copy.searchRecords}
            placeholder={copy.searchRecords}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label={copy.filterStatus}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">{copy.viewAll}</option>
          <option value="overdue">{copy.overdue}</option>
          <option value="soon">{copy.dueWithinWeek}</option>
          <option value="normal">{copy.normal}</option>
        </select>
        <label className={styles.sort}>
          <ArrowDownUp size={14} aria-hidden="true" />
          <select
            aria-label={copy.sortDue}
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="asc">{copy.ascending}</option>
            <option value="desc">{copy.descending}</option>
          </select>
        </label>
        <span className={styles.resultCount}>
          {records.length} {copy.rowCount}
        </span>
      </div>
      {records.length ? (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{copy.recordCode}</th>
                <th>{copy.recordName}</th>
                <th>{copy.department}</th>
                <th>{copy.responsible}</th>
                <th>{copy.nextDue}</th>
                <th>{copy.daysLeft}</th>
                <th>{copy.recordSource}</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id}>
                  <td>
                    <button
                      className={styles.code}
                      onClick={() => actions.openRecord(record)}
                    >
                      {record.code}
                    </button>
                  </td>
                  <td>
                    <button
                      className={styles.rowName}
                      onClick={() => actions.openRecord(record)}
                    >
                      {record.name}
                    </button>
                  </td>
                  <td>{record.department}</td>
                  <td>
                    <span className={styles.ownerDot} aria-hidden="true">
                      {record.responsible[0]}
                    </span>
                    {record.responsible}
                  </td>
                  <td className={styles.date}>{record.nextDue}</td>
                  <td>
                    <DueBadge record={record} copy={copy} />
                  </td>
                  <td>
                    <button
                      className={styles.textButton}
                      onClick={() => actions.openRecord(record)}
                    >
                      {record.documentIds.length} {copy.documents}
                      <ArrowUpRight size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState copy={copy} />
      )}
    </div>
  );
}

export function AgentGrid({ data, copy, actions }: PreviewVariantProps) {
  const [query, setQuery] = useState("");
  const agents = data.agents.filter((agent) =>
    `${agent.name} ${agent.description}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return (
    <div className={styles.agentBrowser}>
      <label className={styles.search}>
        <Search size={16} aria-hidden="true" />
        <input
          aria-label={copy.searchAgents}
          placeholder={copy.searchAgents}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {agents.length ? (
        <div className={styles.agentGrid}>
          {agents.map((agent) => (
            <button
              key={agent.id}
              className={styles.agentCard}
              onClick={() => actions.openAgent(agent)}
            >
              <span className={`${styles.agentMark} ${styles[agent.kind]}`}>
                <Bot size={24} strokeWidth={1.6} />
              </span>
              <strong>{agent.name}</strong>
              <p>{agent.description}</p>
              <span className={styles.agentFoot}>
                {agent.documentIds.length} {copy.documents}
                <ArrowUpRight size={15} />
              </span>
            </button>
          ))}
        </div>
      ) : (
        <EmptyState copy={copy} />
      )}
    </div>
  );
}

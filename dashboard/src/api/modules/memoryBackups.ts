import { request, requestBlob } from "../request";

export type BackupScope = "shared" | "private";
export interface MemoryBackup {
  backup_id: string;
  created_at: number;
  size_bytes: number;
  backend: string;
  total_rows: number;
}
export interface MemoryRestoreResult {
  restored_backup_id: string;
  safety_backup_id: string;
  restored_rows: number;
  checkpoint_threads: number;
}
const base = (agentId: string) =>
  `/agents/${encodeURIComponent(agentId)}/memory`;
const scopeQuery = (scope: BackupScope) => `scope=${scope}`;

export const memoryBackupsApi = {
  list(agentId: string, scope: BackupScope) {
    return request<MemoryBackup[]>(
      `${base(agentId)}/backups?${scopeQuery(scope)}`,
    );
  },
  create(agentId: string, scope: BackupScope) {
    return request<MemoryBackup>(`${base(agentId)}/backups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, confirmed: true }),
    });
  },
  async download(agentId: string, scope: BackupScope, backupId: string) {
    return requestBlob(
      `${base(agentId)}/backups/${encodeURIComponent(
        backupId,
      )}/download?${scopeQuery(scope)}`,
    );
  },
  remove(agentId: string, scope: BackupScope, backupId: string) {
    return request<void>(
      `${base(agentId)}/backups/${encodeURIComponent(backupId)}?${scopeQuery(
        scope,
      )}&confirmed=true`,
      { method: "DELETE" },
    );
  },
  restore(agentId: string, scope: BackupScope, backupId: string) {
    return request<MemoryRestoreResult>(`${base(agentId)}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, backup_id: backupId, confirmed: true }),
    });
  },
};

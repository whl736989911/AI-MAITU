import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Popconfirm,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { Download, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { message } from "@/utils/antdMessage";
import {
  memoryBackupsApi,
  type BackupScope,
  type MemoryBackup,
  type MemoryRestoreResult,
} from "../../../api/modules/memoryBackups";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

interface Props {
  agentId: string;
  scope?: BackupScope;
  readonly?: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MemoryBackups({
  agentId,
  scope = "shared",
  readonly = false,
}: Props) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [rows, setRows] = useState<MemoryBackup[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<MemoryRestoreResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await memoryBackupsApi.list(agentId, scope));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("memory.backups.loadFailed", "备份列表加载失败"),
      );
    } finally {
      setLoading(false);
    }
  }, [agentId, scope, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await memoryBackupsApi.create(agentId, scope);
      message.success(t("memory.backups.created", "备份已创建"));
      await reload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("memory.backups.operationFailed", "操作失败"),
      );
    } finally {
      setBusy(false);
    }
  };

  const download = async (row: MemoryBackup) => {
    try {
      const blob = await memoryBackupsApi.download(
        agentId,
        scope,
        row.backup_id,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = row.backup_id;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("memory.backups.operationFailed", "操作失败"),
      );
    }
  };

  const remove = async (row: MemoryBackup) => {
    setBusy(true);
    try {
      await memoryBackupsApi.remove(agentId, scope, row.backup_id);
      message.success(t("memory.backups.deleted", "备份已删除"));
      await reload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("memory.backups.operationFailed", "操作失败"),
      );
    } finally {
      setBusy(false);
    }
  };

  const restore = async (row: MemoryBackup) => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      setOutcome(await memoryBackupsApi.restore(agentId, scope, row.backup_id));
      message.success(
        t("memory.backups.restored", "记忆已恢复，恢复前安全备份已保留"),
      );
      await reload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("memory.backups.restoreFailed", "恢复失败；请检查安全备份"),
      );
    } finally {
      setBusy(false);
    }
  };

  const columns: ColumnsType<MemoryBackup> = [
    {
      title: t("memory.backups.createdAt", "创建时间"),
      dataIndex: "created_at",
      render: (value: number) => formatServerDateTime(value, timeZone),
    },
    { title: t("memory.backups.rows", "记录数"), dataIndex: "total_rows" },
    {
      title: t("memory.backups.size", "大小"),
      dataIndex: "size_bytes",
      render: formatBytes,
    },
    { title: t("memory.backups.backend", "存储"), dataIndex: "backend" },
    {
      title: t("memory.backups.actions", "操作"),
      key: "actions",
      render: (_, row) => (
        <Space>
          <Button
            size="small"
            icon={<Download size={14} />}
            onClick={() => void download(row)}
          >
            {t("memory.backups.download", "下载")}
          </Button>
          {!readonly && (
            <Popconfirm
              title={t(
                "memory.backups.restoreConfirm",
                "恢复此备份？系统会先创建当前状态的安全备份。",
              )}
              onConfirm={() => void restore(row)}
              okText={t("memory.backups.restore", "恢复")}
              cancelText={t("common.cancel", "取消")}
            >
              <Button
                size="small"
                danger
                icon={<RotateCcw size={14} />}
                loading={busy}
              >
                {t("memory.backups.restore", "恢复")}
              </Button>
            </Popconfirm>
          )}
          <Popconfirm
            title={t("memory.backups.deleteConfirm", "永久删除此备份？")}
            onConfirm={() => void remove(row)}
            okText={t("common.delete", "删除")}
            cancelText={t("common.cancel", "取消")}
          >
            <Button
              size="small"
              danger
              icon={<Trash2 size={14} />}
              loading={busy}
            >
              {t("common.delete", "删除")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={t("memory.backups.title", "记忆备份")}
      extra={
        <Space>
          <Button
            icon={<RefreshCw size={14} />}
            onClick={() => void reload()}
            loading={loading}
          >
            {t("common.refresh", "刷新")}
          </Button>
          <Button type="primary" onClick={() => void create()} loading={busy}>
            {t("memory.backups.create", "创建备份")}
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          {t(
            "memory.backups.scope",
            "当前范围：{{scope}}。备份仅包含此记忆范围与可验证的会话检查点。",
            { scope },
          )}
        </Typography.Text>
        {error && <Alert type="error" showIcon message={error} />}
        {outcome && (
          <Alert
            type="success"
            showIcon
            message={t(
              "memory.backups.restoreOutcome",
              "已恢复备份 {{backup}}；安全备份：{{safety}}；检查点会话：{{threads}}。",
              {
                backup: outcome.restored_backup_id,
                safety: outcome.safety_backup_id,
                threads: outcome.checkpoint_threads,
              },
            )}
          />
        )}
        <Table
          rowKey="backup_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 8 }}
        />
      </Space>
    </Card>
  );
}

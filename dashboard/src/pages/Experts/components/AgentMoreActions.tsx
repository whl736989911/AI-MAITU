import { Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";
import {
  Bot,
  Brain,
  FileText,
  MoreHorizontal,
  Notebook,
  Puzzle,
  Sparkles,
  Waypoints,
  Workflow,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";

interface AgentMoreActionsProps {
  buttonClassName: string;
  onWorkflow?: () => void;
  onSkills: () => void;
  onSubagents: () => void;
  onTools: () => void;
  onPlugins: () => void;
  onMbti: () => void;
  onMemory?: () => void;
  onChannels: () => void;
  /** A feature's persona files; experts edit them in their profile instead. */
  onFiles?: () => void;
}

/** Catalog actions shared by experts and features; workflow belongs to features only. */
export default function AgentMoreActions({
  buttonClassName,
  onWorkflow,
  onSkills,
  onSubagents,
  onTools,
  onPlugins,
  onMbti,
  onMemory,
  onChannels,
  onFiles,
}: AgentMoreActionsProps) {
  const { t } = useTranslation();

  const items: MenuProps["items"] = [
    ...(onWorkflow
      ? [
          {
            key: "workflow",
            icon: <Workflow size={14} />,
            label: t("features.tabWorkflow"),
            onClick: onWorkflow,
          },
        ]
      : []),
    {
      key: "skills",
      icon: <Sparkles size={14} />,
      label: t("personalization.tabs.skills"),
      onClick: onSkills,
    },
    {
      key: "subagents",
      icon: <Bot size={14} />,
      label: t("personalization.tabs.subagents"),
      onClick: onSubagents,
    },
    {
      key: "tools",
      icon: <Wrench size={14} />,
      label: t("personalization.tabs.tools"),
      onClick: onTools,
    },
    {
      key: "plugins",
      icon: <Puzzle size={14} />,
      label: t("personalization.tabs.plugins"),
      onClick: onPlugins,
    },
    {
      key: "mbti",
      icon: <Brain size={14} />,
      label: t("personalization.tabs.mbti"),
      onClick: onMbti,
    },
    ...(onMemory
      ? [
          {
            key: "memory",
            icon: <Notebook size={14} />,
            label: t("personalization.tabs.memory"),
            onClick: onMemory,
          },
        ]
      : []),
    {
      key: "channels",
      icon: <Waypoints size={14} />,
      label: t("personalization.tabs.channels"),
      onClick: onChannels,
    },
    ...(onFiles
      ? [
          {
            key: "files",
            icon: <FileText size={14} />,
            label: t("personalization.tabs.files"),
            onClick: onFiles,
          },
        ]
      : []),
  ];

  return (
    <Dropdown menu={{ items }} trigger={["click"]} placement="bottomRight">
      <Tooltip title={t("common.more")} mouseEnterDelay={0.5}>
        <button
          type="button"
          className={buttonClassName}
          aria-label={t("common.more")}
        >
          <MoreHorizontal size={13} />
        </button>
      </Tooltip>
    </Dropdown>
  );
}

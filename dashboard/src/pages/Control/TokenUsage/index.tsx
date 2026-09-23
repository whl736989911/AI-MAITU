/**
 * Token Usage — account-level analytics.
 *
 * Views (peer Segmented): summary | by day | by expert | by feature | by model.
 * Summary composes totals + donut/pie charts from existing
 * ``GET /api/usage/summary`` granularities. Dimension views: stacked bar +
 * sortable table.
 *
 * **By expert and by feature are two views of the same rows, one kind each.**
 * The server groups them apart by ``agents.kind`` (``by_expert`` / ``by_feature``
 * in ``infra/db/repos/usage.py``), so neither has to mix the other in, and the
 * pair appears only where its kind does: a deployment with no features has no
 * "by feature" to offer, and one with no ordinary agents has no "by expert". The
 * question is asked once, of the agents this deployment holds — never of the
 * rows currently in scope, which move with the user and date filters
 * (``hooks/useAgentKindPresence.ts``).
 *
 * The roll-up above every view stays the whole scope either way: these pick what
 * is listed, not what is counted.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Empty,
  Select,
  Segmented,
  Spin,
  Table,
  Tag,
} from "antd";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import dayjs, { type Dayjs } from "dayjs";
import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { UsageStats, type UsageStatItem } from "./UsageStats";
import { useUserRole } from "../../../hooks/useUserRole";
import { useAgentKindPresence } from "../../../hooks/useAgentKindPresence";
import type { AgentKindPresence } from "../../../utils/agentKindCounts";
import { request, requestBlob } from "../../../api/request";
import { useAgent } from "../../../context/AgentContext";
import { useTheme } from "../../../context/ThemeContext";
import { brandPrimary } from "../../../styles/themePalettes";
import { message } from "../../../utils/antdMessage";
import styles from "./index.module.less";

interface UsageUserOption {
  id: number;
  username: string;
  display_name: string | null;
}

interface UsageBucket {
  key: string;
  label: string;
  input_tokens: number;
  uncached_input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  model_calls: number;
  turns: number;
}

interface UsageSummary {
  window: string;
  granularity: string;
  range_start: number;
  range_end: number;
  input_tokens: number;
  uncached_input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  model_calls: number;
  cache_hit_percent: number;
  turns: number;
  avg_per_turn: number;
  buckets: UsageBucket[];
}

type ViewMode = "summary" | "by_day" | "by_expert" | "by_feature" | "by_model";
type DimGranularity = Exclude<ViewMode, "summary">;

const CHART_COLORS = [
  "#4f6ef7",
  "#a06ef7",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#ec4899",
  "#84cc16",
  "#64748b",
  "#8b5cf6",
];

const IO_COLORS = {
  input: "#4f6ef7",
  output: "#a06ef7",
};

const CHART_TOOLTIP_STYLE = {
  background: "var(--fn-bg-elevated)",
  border: "1px solid var(--fn-border-secondary)",
  borderRadius: 6,
  fontSize: 12,
};

/** Shared axis / legend styling so ticks + legend never collide or clip. */
const AXIS_TICK = { fontSize: 11, fill: "var(--fn-text-tertiary)" } as const;
const AXIS_LINE = { stroke: "var(--fn-border-primary)" } as const;
/** Plot insets: leave room for X ticks (bottom) and optional top legend. */
const BAR_MARGIN = { top: 12, right: 16, bottom: 8, left: 4 } as const;
const BAR_MARGIN_WITH_LEGEND = {
  top: 32,
  right: 16,
  bottom: 8,
  left: 4,
} as const;

const CHART_HEIGHT = {
  bar: 280,
} as const;

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatDayTick(label: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(label)) return label.slice(5);
  return label;
}

interface UsageBarSeries {
  dataKey: "input_tokens" | "output_tokens" | "turns" | "total_tokens";
  name: string;
  fill: string;
  stackId?: string;
  radius?: [number, number, number, number];
  maxBarSize?: number;
}

/**
 * Cartesian bar chart with safe margins: axes + legend always fully visible.
 * Parent must give a definite height via CSS class on the frame.
 */
function UsageBarChart({
  data,
  series,
  height,
  fillParent = false,
  showLegend = false,
  tickFormatter,
}: {
  data: UsageBucket[];
  series: UsageBarSeries[];
  height?: number;
  /** Stretch to fill flex card body (summary daily trend). */
  fillParent?: boolean;
  showLegend?: boolean;
  tickFormatter?: (label: string) => string;
}) {
  return (
    <div
      className={
        fillParent
          ? `${styles.chartFrame} ${styles.chartFrameFill}`
          : styles.chartFrame
      }
      style={fillParent ? undefined : { height }}
    >
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <BarChart
          data={data}
          margin={showLegend ? BAR_MARGIN_WITH_LEGEND : BAR_MARGIN}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="var(--fn-border-secondary)"
            vertical={false}
          />
          <XAxis
            dataKey="label"
            tickFormatter={tickFormatter}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={AXIS_LINE}
            height={30}
            interval="preserveStartEnd"
            minTickGap={14}
            dy={4}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={AXIS_LINE}
            width={44}
            allowDecimals={false}
          />
          <RechartsTooltip contentStyle={CHART_TOOLTIP_STYLE} />
          {showLegend ? (
            <Legend
              verticalAlign="top"
              align="right"
              height={24}
              iconSize={10}
              wrapperStyle={{
                fontSize: 12,
                top: 0,
                right: 0,
                lineHeight: "20px",
              }}
            />
          ) : null}
          {series.map((s) => (
            <Bar
              key={s.dataKey}
              dataKey={s.dataKey}
              name={s.name}
              fill={s.fill}
              stackId={s.stackId}
              radius={s.radius}
              maxBarSize={s.maxBarSize}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function usageStatItems(
  totals: UsageSummary,
  t: (key: string) => string,
): UsageStatItem[] {
  return [
    {
      key: "total",
      label: t("tokenUsage.totalTokens"),
      value: formatNumber(totals.total_tokens),
    },
    {
      key: "input",
      label: t("tokenUsage.input"),
      value: formatNumber(totals.input_tokens),
    },
    {
      key: "output",
      label: t("tokenUsage.output"),
      value: formatNumber(totals.output_tokens),
    },
    {
      key: "cacheRead",
      label: t("tokenUsage.cacheRead"),
      value: formatNumber(totals.cache_read_tokens),
    },
    {
      key: "cacheHit",
      label: t("tokenUsage.cacheHit"),
      value: `${totals.cache_hit_percent.toFixed(1)}%`,
    },
    { key: "turns", label: t("tokenUsage.turns"), value: totals.turns },
    {
      key: "avg",
      label: t("tokenUsage.avgPerTurn"),
      value: formatNumber(totals.avg_per_turn),
    },
  ];
}

function bucketColumnTitle(
  granularity: DimGranularity,
  t: (key: string) => string,
): string {
  if (granularity === "by_day") return t("tokenUsage.date");
  if (granularity === "by_expert") return t("tokenUsage.expert");
  if (granularity === "by_feature") return t("tokenUsage.feature");
  return t("tokenUsage.model");
}

function resolveAgentLabels(
  buckets: UsageBucket[],
  agentNameById: Map<string, string>,
): UsageBucket[] {
  return buckets.map((b) => ({
    ...b,
    label: agentNameById.get(b.key) ?? b.label ?? b.key,
  }));
}

/** By-day view: token stacked bars + turns bars side-by-side. */
function DailyTrendCharts({
  data,
  emptyText,
  tokensTitle,
  turnsTitle,
  inputLabel,
  outputLabel,
  turnsLabel,
}: {
  data: UsageBucket[];
  emptyText: string;
  tokensTitle: string;
  turnsTitle: string;
  inputLabel: string;
  outputLabel: string;
  turnsLabel: string;
}) {
  const { palette, isDark, customColor } = useTheme();
  const turnsColor = brandPrimary(palette, isDark, customColor);
  const empty = data.length === 0;

  return (
    <div className={styles.trendGrid}>
      <Card
        size="small"
        title={tokensTitle}
        className={`${styles.chartCard} ${styles.trendCard}`}
      >
        {empty ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={emptyText}
            style={{ padding: "24px 0" }}
          />
        ) : (
          <UsageBarChart
            data={data}
            height={CHART_HEIGHT.bar}
            showLegend
            tickFormatter={formatDayTick}
            series={[
              {
                dataKey: "input_tokens",
                name: inputLabel,
                fill: IO_COLORS.input,
                stackId: "a",
              },
              {
                dataKey: "output_tokens",
                name: outputLabel,
                fill: IO_COLORS.output,
                stackId: "a",
                radius: [3, 3, 0, 0],
              },
            ]}
          />
        )}
      </Card>

      <Card
        size="small"
        title={turnsTitle}
        className={`${styles.chartCard} ${styles.trendCard}`}
      >
        {empty ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={emptyText}
            style={{ padding: "24px 0" }}
          />
        ) : (
          <UsageBarChart
            data={data}
            height={CHART_HEIGHT.bar}
            tickFormatter={formatDayTick}
            series={[
              {
                dataKey: "turns",
                name: turnsLabel,
                fill: turnsColor,
                radius: [3, 3, 0, 0],
                maxBarSize: 36,
              },
            ]}
          />
        )}
      </Card>
    </div>
  );
}

function toPieData(buckets: UsageBucket[], limit = 8) {
  const sorted = [...buckets].sort((a, b) => b.total_tokens - a.total_tokens);
  if (sorted.length <= limit) {
    return sorted.map((b) => ({
      name: b.label,
      value: b.total_tokens,
      turns: b.turns,
    }));
  }
  const head = sorted.slice(0, limit - 1);
  const rest = sorted.slice(limit - 1);
  const otherValue = rest.reduce((s, b) => s + b.total_tokens, 0);
  const otherTurns = rest.reduce((s, b) => s + b.turns, 0);
  return [
    ...head.map((b) => ({
      name: b.label,
      value: b.total_tokens,
      turns: b.turns,
    })),
    { name: "…", value: otherValue, turns: otherTurns },
  ];
}

function fetchSummary(
  windowKey: string,
  granularity: string,
  agentFilter: string | "all",
  userFilter: number | "all" | null,
): Promise<UsageSummary> {
  const params = new URLSearchParams({ window: windowKey, granularity });
  if (agentFilter !== "all") {
    params.set("agent_id", agentFilter);
  }
  if (userFilter === "all") {
    return request<UsageSummary>(`/admin/usage/summary?${params}`);
  }
  if (typeof userFilter === "number") {
    params.set("user_id", String(userFilter));
    return request<UsageSummary>(`/admin/usage/summary?${params}`);
  }
  return request<UsageSummary>(`/usage/summary?${params}`);
}

function usageExportPath(
  windowKey: string,
  agentFilter: string | "all",
  userFilter: number | "all" | null,
): string {
  const params = new URLSearchParams({ window: windowKey });
  if (agentFilter !== "all") {
    params.set("agent_id", agentFilter);
  }
  if (userFilter === "all") {
    return `/admin/usage/export.xlsx?${params}`;
  }
  if (typeof userFilter === "number") {
    params.set("user_id", String(userFilter));
    return `/admin/usage/export.xlsx?${params}`;
  }
  return `/usage/export.xlsx?${params}`;
}

/** ``{title}_{YYYY-MM-DD-YYYY-MM-DD}.xlsx`` — strip ``range:`` / ``day:`` kind tags. */
function usageExportFilename(prefix: string, windowKey: string): string {
  const rangeMatch = /^range:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/.exec(
    windowKey,
  );
  if (rangeMatch) {
    return `${prefix}_${rangeMatch[1]}-${rangeMatch[2]}.xlsx`;
  }
  const dayMatch = /^day:(\d{4}-\d{2}-\d{2})$/.exec(windowKey);
  if (dayMatch) {
    return `${prefix}_${dayMatch[1]}.xlsx`;
  }
  const monthMatch = /^month:(\d{4}-\d{2})$/.exec(windowKey);
  if (monthMatch) {
    return `${prefix}_${monthMatch[1]}.xlsx`;
  }
  const period =
    windowKey.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_|_$/g, "") ||
    "usage";
  return `${prefix}_${period}.xlsx`;
}

const { RangePicker } = DatePicker;

function rangeToWindowKey(range: [Dayjs, Dayjs] | null): string {
  if (range === null) return "all";
  const [start, end] = range;
  return `range:${start.format("YYYY-MM-DD")}:${end.format("YYYY-MM-DD")}`;
}

function defaultLast30dRange(): [Dayjs, Dayjs] {
  return [dayjs().add(-29, "day").startOf("day"), dayjs().endOf("day")];
}

function DonutCard({
  title,
  data,
  emptyText,
  colors = CHART_COLORS,
}: {
  title: string;
  data: { name: string; value: number }[];
  emptyText: string;
  colors?: string[] | Record<string, string>;
}) {
  const colorFor = (name: string, index: number) => {
    if (Array.isArray(colors)) return colors[index % colors.length];
    return colors[name] ?? CHART_COLORS[index % CHART_COLORS.length];
  };

  const empty = data.length === 0 || data.every((d) => d.value === 0);

  return (
    <Card
      size="small"
      title={title}
      className={`${styles.chartCard} ${styles.donutCard}`}
    >
      {empty ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
      ) : (
        <div className={styles.donutBody}>
          {/* Plot flex-fills the card (same height as sibling charts); legend is fixed below. */}
          <div className={styles.donutPlot}>
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <PieChart margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius="50%"
                  outerRadius="90%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {data.map((entry, i) => (
                    <Cell key={entry.name} fill={colorFor(entry.name, i)} />
                  ))}
                </Pie>
                <RechartsTooltip
                  formatter={(value) => formatNumber(Number(value ?? 0))}
                  contentStyle={CHART_TOOLTIP_STYLE}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className={styles.donutLegend}>
            {data.map((entry, i) => (
              <li key={entry.name} className={styles.donutLegendItem}>
                <span
                  className={styles.donutLegendSwatch}
                  style={{ background: colorFor(entry.name, i) }}
                  aria-hidden
                />
                <span className={styles.donutLegendLabel}>{entry.name}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function SummaryView({
  totals,
  byExpert,
  byFeature,
  byModel,
  byDay,
  kinds,
  isMobile,
}: {
  totals: UsageSummary;
  byExpert: UsageBucket[];
  byFeature: UsageBucket[];
  byModel: UsageBucket[];
  byDay: UsageBucket[];
  /** Which kinds this deployment holds — which breakdowns there are to draw. */
  kinds: AgentKindPresence;
  isMobile: boolean;
}) {
  const { t } = useTranslation();

  const ioData = useMemo(
    () => [
      { name: t("tokenUsage.input"), value: totals.input_tokens },
      { name: t("tokenUsage.output"), value: totals.output_tokens },
    ],
    [totals.input_tokens, totals.output_tokens, t],
  );

  const expertPie = useMemo(() => toPieData(byExpert), [byExpert]);
  const featurePie = useMemo(() => toPieData(byFeature), [byFeature]);
  const modelPie = useMemo(() => toPieData(byModel), [byModel]);
  const dayBars = useMemo(() => [...byDay].reverse().slice(-14), [byDay]);
  /** Input/output, then one donut per kind that exists, then models. */
  const donutCount = 2 + (kinds.experts ? 1 : 0) + (kinds.features ? 1 : 0);

  return (
    <div className={styles.summaryBody}>
      <UsageStats
        items={usageStatItems(totals, t)}
        overflowEnabled={!isMobile}
      />

      <div
        className={styles.pieGrid}
        style={{
          gridTemplateColumns: isMobile
            ? "1fr"
            : `repeat(${donutCount}, minmax(0, 1fr))`,
        }}
      >
        <DonutCard
          title={t("tokenUsage.ioBreakdown")}
          data={ioData}
          emptyText={t("tokenUsage.noRecordsInRange")}
          colors={{
            [t("tokenUsage.input")]: IO_COLORS.input,
            [t("tokenUsage.output")]: IO_COLORS.output,
          }}
        />
        {kinds.experts && (
          <DonutCard
            title={t("tokenUsage.expertBreakdown")}
            data={expertPie}
            emptyText={t("tokenUsage.noRecordsInRange")}
          />
        )}
        {kinds.features && (
          <DonutCard
            title={t("tokenUsage.featureBreakdown")}
            data={featurePie}
            emptyText={t("tokenUsage.noRecordsInRange")}
          />
        )}
        <DonutCard
          title={t("tokenUsage.modelBreakdown")}
          data={modelPie}
          emptyText={t("tokenUsage.noRecordsInRange")}
        />
      </div>

      <Card
        size="small"
        title={t("tokenUsage.dailyTrend")}
        className={`${styles.chartCard} ${styles.summaryTrendCard}`}
      >
        {dayBars.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t("tokenUsage.noRecordsInRange")}
            style={{ padding: "24px 0" }}
          />
        ) : (
          <UsageBarChart
            data={dayBars}
            fillParent
            showLegend
            series={[
              {
                dataKey: "input_tokens",
                name: t("tokenUsage.input"),
                fill: IO_COLORS.input,
                stackId: "a",
              },
              {
                dataKey: "output_tokens",
                name: t("tokenUsage.output"),
                fill: IO_COLORS.output,
                stackId: "a",
                radius: [3, 3, 0, 0],
              },
            ]}
          />
        )}
      </Card>
    </div>
  );
}

function DimensionView({
  granularity,
  buckets,
  totals,
  isMobile,
}: {
  granularity: DimGranularity;
  buckets: UsageBucket[];
  totals: UsageSummary;
  isMobile: boolean;
}) {
  const { t } = useTranslation();

  const chartBuckets = useMemo(() => {
    if (granularity === "by_day") return [...buckets].reverse();
    return buckets.slice(0, 20);
  }, [buckets, granularity]);

  const pieData = useMemo(
    () =>
      granularity === "by_day" ? [] : toPieData(buckets, isMobile ? 6 : 10),
    [buckets, granularity, isMobile],
  );

  return (
    <div className={styles.summaryBody}>
      <UsageStats
        items={usageStatItems(totals, t)}
        overflowEnabled={!isMobile}
      />

      {granularity !== "by_day" && pieData.length > 0 && (
        <div
          className={styles.dimChartRow}
          style={{
            gridTemplateColumns: isMobile ? "1fr" : "1fr 1.4fr",
          }}
        >
          <DonutCard
            title={
              granularity === "by_expert"
                ? t("tokenUsage.expertBreakdown")
                : granularity === "by_feature"
                ? t("tokenUsage.featureBreakdown")
                : t("tokenUsage.modelBreakdown")
            }
            data={pieData}
            emptyText={t("tokenUsage.noRecordsInRange")}
          />
          <Card
            size="small"
            title={t("tokenUsage.ioBarChart")}
            className={`${styles.chartCard} ${styles.dimBarCard}`}
          >
            <UsageBarChart
              data={chartBuckets}
              height={CHART_HEIGHT.bar}
              showLegend
              series={[
                {
                  dataKey: "input_tokens",
                  name: t("tokenUsage.input"),
                  fill: IO_COLORS.input,
                  stackId: "a",
                },
                {
                  dataKey: "output_tokens",
                  name: t("tokenUsage.output"),
                  fill: IO_COLORS.output,
                  stackId: "a",
                  radius: [3, 3, 0, 0],
                },
              ]}
            />
          </Card>
        </div>
      )}

      {granularity === "by_day" && (
        <DailyTrendCharts
          data={chartBuckets}
          emptyText={t("tokenUsage.noRecordsInRange")}
          tokensTitle={t("tokenUsage.dailyTrend")}
          turnsTitle={t("tokenUsage.turnsDailyTrend")}
          inputLabel={t("tokenUsage.input")}
          outputLabel={t("tokenUsage.output")}
          turnsLabel={t("tokenUsage.turns")}
        />
      )}

      <Card
        size="small"
        title={t("tokenUsage.detail")}
        className={`${styles.chartCard} ${styles.dimTableCard}`}
        styles={{ body: { padding: isMobile ? 0 : 16 } }}
      >
        {buckets.length === 0 ? (
          <Empty
            description={t("tokenUsage.noRecordsInRange")}
            style={{ padding: isMobile ? "20px 0" : undefined }}
          />
        ) : isMobile ? (
          <div>
            {buckets.map((b) => (
              <div key={b.key} className={styles.mobileRow}>
                <div className={styles.mobileRowTop}>
                  <Tag style={{ margin: 0 }}>{b.label}</Tag>
                  <span className={styles.mobileTotal}>
                    {formatNumber(b.total_tokens)}
                  </span>
                </div>
                <div className={styles.mobileMeta}>
                  <span>
                    {t("tokenUsage.input")} {formatNumber(b.input_tokens)}
                  </span>
                  <span>
                    {t("tokenUsage.output")} {formatNumber(b.output_tokens)}
                  </span>
                  <span>{t("tokenUsage.turnsCount", { count: b.turns })}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Table<UsageBucket>
            rowKey="key"
            size="small"
            pagination={false}
            dataSource={buckets}
            scroll={{ x: "max-content" }}
            columns={[
              {
                title: bucketColumnTitle(granularity, t),
                dataIndex: "label",
                fixed: "left",
                width: 160,
                render: (v) => <Tag>{v}</Tag>,
              },
              {
                title: t("tokenUsage.input"),
                dataIndex: "input_tokens",
                render: formatNumber,
                sorter: (a, b) => a.input_tokens - b.input_tokens,
                align: "right",
              },
              {
                title: t("tokenUsage.output"),
                dataIndex: "output_tokens",
                render: formatNumber,
                sorter: (a, b) => a.output_tokens - b.output_tokens,
                align: "right",
              },
              {
                title: t("tokenUsage.total"),
                dataIndex: "total_tokens",
                render: formatNumber,
                sorter: (a, b) => a.total_tokens - b.total_tokens,
                defaultSortOrder: "descend",
                align: "right",
              },
              {
                title: t("tokenUsage.turns"),
                dataIndex: "turns",
                sorter: (a, b) => a.turns - b.turns,
                align: "right",
              },
            ]}
          />
        )}
      </Card>
    </div>
  );
}

export default function TokenUsagePage() {
  const { t } = useTranslation();
  const { agents } = useAgent();
  const role = useUserRole();
  const isAdmin = role === "admin";
  const isMobile = useIsMobile();
  /** Which kinds this deployment holds — which views there are to offer. */
  const kinds = useAgentKindPresence();
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(() =>
    defaultLast30dRange(),
  );
  const [view, setView] = useState<ViewMode>("summary");
  const [agentFilter, setAgentFilter] = useState<string | "all">("all");
  const [userFilter, setUserFilter] = useState<number | "all">("all");
  const [users, setUsers] = useState<UsageUserOption[]>([]);
  const [exporting, setExporting] = useState(false);

  const [totals, setTotals] = useState<UsageSummary | null>(null);
  const [dimBuckets, setDimBuckets] = useState<UsageBucket[]>([]);
  const [summaryExpert, setSummaryExpert] = useState<UsageBucket[]>([]);
  const [summaryFeature, setSummaryFeature] = useState<UsageBucket[]>([]);
  const [summaryModel, setSummaryModel] = useState<UsageBucket[]>([]);
  const [summaryDay, setSummaryDay] = useState<UsageBucket[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agentNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of agents) {
      map.set(a.agent_id, a.name);
    }
    return map;
  }, [agents]);

  // Admin-only: null while role unknown so we don't hit /admin before ready.
  const adminUserFilter: number | "all" | null = isAdmin ? userFilter : null;

  const windowKey = useMemo(() => rangeToWindowKey(dateRange), [dateRange]);

  const rangePresets = useMemo(
    () => [
      {
        label: t("tokenUsage.today"),
        value: [dayjs().startOf("day"), dayjs().endOf("day")] as [Dayjs, Dayjs],
      },
      {
        label: t("tokenUsage.yesterday"),
        value: [
          dayjs().add(-1, "day").startOf("day"),
          dayjs().add(-1, "day").endOf("day"),
        ] as [Dayjs, Dayjs],
      },
      {
        label: t("tokenUsage.last7d"),
        value: [
          dayjs().add(-6, "day").startOf("day"),
          dayjs().endOf("day"),
        ] as [Dayjs, Dayjs],
      },
      {
        label: t("tokenUsage.last30d"),
        value: defaultLast30dRange(),
      },
      {
        label: t("tokenUsage.thisMonth"),
        value: [dayjs().startOf("month"), dayjs().endOf("day")] as [
          Dayjs,
          Dayjs,
        ],
      },
      {
        label: t("tokenUsage.lastMonth"),
        value: [
          dayjs().add(-1, "month").startOf("month"),
          dayjs().add(-1, "month").endOf("month"),
        ] as [Dayjs, Dayjs],
      },
    ],
    [t],
  );

  const userOptions = useMemo(
    () => [
      { value: "all" as const, label: t("tokenUsage.allUsers") },
      ...users.map((u) => ({
        value: u.id,
        label: u.display_name?.trim() || u.username,
      })),
    ],
    [users, t],
  );

  /**
   * The kind-neutral half of the filter. "All experts" names the whole list a
   * deployment with no features has; once features exist the list holds both
   * kinds and the one label has to say so rather than name half of it.
   */
  const allAgentsLabel = kinds.experts
    ? kinds.features
      ? t("tokenUsage.allAgents")
      : t("tokenUsage.allExperts")
    : t("tokenUsage.allFeatures");

  const agentOptions = useMemo(
    () => [
      { value: "all", label: allAgentsLabel },
      ...agents.map((a) => ({ value: a.agent_id, label: a.name })),
    ],
    [agents, allAgentsLabel],
  );

  /**
   * The two kind views, each offered only where its kind is. A deployment with
   * no features has no "by feature" to break down, and drawing the option anyway
   * would promise a view that is empty by construction — the presence is read
   * from the deployment's agents, so which options exist does not move with the
   * date or user filter.
   */
  const viewOptions = useMemo(
    () => [
      { value: "summary", label: t("tokenUsage.summary") },
      { value: "by_day", label: t("tokenUsage.byDay") },
      ...(kinds.experts
        ? [{ value: "by_expert", label: t("tokenUsage.byExpert") }]
        : []),
      ...(kinds.features
        ? [{ value: "by_feature", label: t("tokenUsage.byFeature") }]
        : []),
      { value: "by_model", label: t("tokenUsage.byModel") },
    ],
    [t, kinds.experts, kinds.features],
  );

  // A view whose kind is gone is not left selected behind a missing option: the
  // pairing of the two is the one thing that must not disagree.
  useEffect(() => {
    if (view === "by_expert" && !kinds.experts) setView("summary");
    if (view === "by_feature" && !kinds.features) setView("summary");
  }, [view, kinds.experts, kinds.features]);

  useEffect(() => {
    if (!isAdmin) {
      setUsers([]);
      return;
    }
    let cancelled = false;
    void request<UsageUserOption[]>("/users")
      .then((rows) => {
        if (!cancelled) setUsers(rows);
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  const refresh = useCallback(async () => {
    // Wait for role resolution before the first admin-scoped fetch so we
    // don't briefly show the caller's private totals then jump to global.
    if (role === null) return;
    setLoading(true);
    setError(null);
    try {
      if (view === "summary") {
        // One request per kind that exists, plus the kind-free views. The
        // roll-up is the same number whichever granularity carries it, so it is
        // read off whichever one was asked for — and a kind that does not exist
        // is not asked about at all.
        const [expertRes, featureRes, modelRes, dayRes] = await Promise.all([
          kinds.experts
            ? fetchSummary(windowKey, "by_expert", agentFilter, adminUserFilter)
            : Promise.resolve(null),
          kinds.features
            ? fetchSummary(
                windowKey,
                "by_feature",
                agentFilter,
                adminUserFilter,
              )
            : Promise.resolve(null),
          fetchSummary(windowKey, "by_model", agentFilter, adminUserFilter),
          fetchSummary(windowKey, "by_day", agentFilter, adminUserFilter),
        ]);
        setTotals(expertRes ?? featureRes ?? dayRes);
        setSummaryExpert(
          expertRes ? resolveAgentLabels(expertRes.buckets, agentNameById) : [],
        );
        setSummaryFeature(
          featureRes
            ? resolveAgentLabels(featureRes.buckets, agentNameById)
            : [],
        );
        setSummaryModel(modelRes.buckets);
        setSummaryDay(dayRes.buckets);
        setDimBuckets([]);
      } else {
        const res = await fetchSummary(
          windowKey,
          view,
          agentFilter,
          adminUserFilter,
        );
        setTotals(res);
        // Both per-agent views bucket by ``agent_id``, and the server has
        // already narrowed each to its own kind.
        const buckets =
          view === "by_expert" || view === "by_feature"
            ? resolveAgentLabels(res.buckets, agentNameById)
            : res.buckets;
        setDimBuckets(buckets);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [
    view,
    windowKey,
    agentFilter,
    adminUserFilter,
    agentNameById,
    role,
    kinds.experts,
    kinds.features,
  ]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onExportExcel = useCallback(async () => {
    if (role === null) return;
    setExporting(true);
    try {
      const blob = await requestBlob(
        usageExportPath(windowKey, agentFilter, adminUserFilter),
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = usageExportFilename(
        t("tokenUsage.exportFilenamePrefix"),
        windowKey,
      );
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error(t("tokenUsage.exportFailed"));
    } finally {
      setExporting(false);
    }
  }, [windowKey, agentFilter, adminUserFilter, role, t]);

  return (
    <PageShell
      title={t("pageShell.tokenUsage.title")}
      subtitle={t("pageShell.tokenUsage.subtitle")}
      fill
    >
      <div className={styles.page}>
        <Alert
          type="info"
          showIcon
          message={t("tokenUsage.dataDisclaimer")}
          className={styles.disclaimer}
        />
        <div className={styles.toolbar}>
          <div className={styles.toolbarViews}>
            <Segmented
              value={view}
              onChange={(v) => setView(v as ViewMode)}
              options={viewOptions}
              size={isMobile ? "small" : "middle"}
            />
          </div>
          <div className={styles.toolbarFilters}>
            {isAdmin && (
              <Select
                value={userFilter}
                onChange={(v) => setUserFilter(v)}
                className={styles.toolbarFilterSelect}
                style={{ width: isMobile ? undefined : 160 }}
                options={userOptions}
                showSearch
                optionFilterProp="label"
              />
            )}
            <RangePicker
              value={dateRange}
              onChange={(values) => {
                if (values?.[0] && values[1]) {
                  setDateRange([values[0], values[1]]);
                } else {
                  setDateRange(null);
                }
              }}
              disabledDate={(current) =>
                current != null && current.isAfter(dayjs().endOf("day"))
              }
              presets={rangePresets}
              allowClear
              className={styles.toolbarFilterSelect}
              style={{ width: isMobile ? undefined : 280 }}
              placeholder={[
                t("tokenUsage.rangeStart"),
                t("tokenUsage.rangeEnd"),
              ]}
            />
            <Select
              value={agentFilter}
              onChange={(v) => setAgentFilter(v)}
              className={styles.toolbarFilterSelect}
              style={{ width: isMobile ? undefined : 200 }}
              options={agentOptions}
            />
            <Button
              icon={<Download size={14} />}
              loading={exporting}
              disabled={role === null}
              onClick={() => void onExportExcel()}
              aria-label={t("tokenUsage.exportExcel")}
            >
              {isMobile ? null : t("tokenUsage.exportExcel")}
            </Button>
          </div>
        </div>

        <div className={styles.content}>
          {error && (
            <Card
              size="small"
              style={{ borderColor: "var(--fn-color-error)", marginBottom: 12 }}
            >
              <span style={{ color: "var(--fn-color-error)" }}>{error}</span>
            </Card>
          )}

          {(loading || role === null) && !totals ? (
            <div className={styles.loadingWrap}>
              <Spin />
            </div>
          ) : totals ? (
            view === "summary" ? (
              <SummaryView
                totals={totals}
                byExpert={summaryExpert}
                byFeature={summaryFeature}
                byModel={summaryModel}
                byDay={summaryDay}
                kinds={kinds}
                isMobile={isMobile}
              />
            ) : (
              <DimensionView
                granularity={view}
                buckets={dimBuckets}
                totals={totals}
                isMobile={isMobile}
              />
            )
          ) : null}
        </div>
      </div>
    </PageShell>
  );
}

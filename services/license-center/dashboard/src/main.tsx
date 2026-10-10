import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import {
  Activity,
  BadgeCheck,
  Building2,
  ChevronDown,
  CircleAlert,
  Clock3,
  KeyRound,
  Languages,
  LayoutDashboard,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Shield,
  ShieldCheck,
  Ticket,
  Users,
  X,
} from "lucide-react";
import {
  api,
  type Activation,
  type AuditEntry,
  type Customer,
  type Enrollment,
  type Instance,
  type Operator,
  type Overview,
  type Role,
  type Session,
  type SystemReadout,
} from "./api";
import "./style.css";

const en = {
  app: "Maitu License Center",
  signIn: "Sign in",
  subtitle: "Secure license operations",
  user: "Username",
  password: "Password",
  totp: "Authenticator code",
  login: "Continue securely",
  overview: "Overview",
  customers: "Customers",
  instances: "Instances & licenses",
  activations: "Activation codes",
  audit: "Audit trail",
  operators: "Operators",
  system: "System & keys",
  signout: "Sign out",
  search: "Search",
  refresh: "Refresh",
  welcome: "License overview",
  overviewText: "Here is your licensing operation at a glance.",
  active: "Active",
  expiring: "Expiring soon",
  expired: "Expired",
  revoked: "Revoked",
  recent: "Recently seen instances",
  latest: "Latest audit activity",
  create: "Create",
  edit: "Edit",
  save: "Save changes",
  cancel: "Cancel",
  name: "Name",
  contact: "Contact",
  notes: "Notes",
  status: "Status",
  actions: "Actions",
  activeState: "Active",
  disabled: "Disabled",
  id: "ID",
  customer: "Customer",
  expires: "License expires",
  lastSeen: "Last seen",
  fingerprint: "Public key fingerprint",
  revision: "Revision",
  license: "License",
  extend: "Extend",
  revoke: "Revoke",
  restore: "Restore",
  replace: "Replace host",
  reason: "Reason (required)",
  confirm: "I understand this operation affects a customer instance",
  confirmTitle: "Confirm sensitive operation",
  confirmAction: "Confirm and continue",
  reauthTitle: "Verify your identity",
  reauthHelp:
    "Enter your current authenticator code to authorize this sensitive change.",
  continue: "Verify & continue",
  code: "Activation code",
  showOnce: "This secret is shown only once. Copy and store it securely now.",
  copied: "Copied",
  createCode: "Generate activation code",
  ttl: "Code lifetime (hours)",
  createCustomer: "Create customer",
  saveCustomer: "Save customer",
  expiryNew: "New expiry date",
  previous: "Previous expiry",
  ownerOnly: "Owner only",
  role: "Role",
  operator: "Operator",
  reader: "Read-only",
  owner: "Owner",
  createOperator: "Create operator",
  totpEnroll: "Authenticator setup",
  secret: "Secret",
  provisioning: "Provisioning URI",
  verifyTotp: "Verify authenticator code",
  disable: "Disable account",
  enable: "Enable account",
  systemStatus: "Service status",
  timezone: "Server timezone",
  signing: "Signing key rotation",
  activeKey: "Active key ID",
  stagedKey: "Staged key ID",
  rotationStatus: "Rotation status",
  tls: "TLS certificate expiry",
  cli: "Key rotation is privileged server-side work. Run the deployment CLI on the license center host; do not rotate keys from this browser.",
  empty: "No records found",
  reasonRequired: "A reason is required.",
  confirmRequired: "Confirm the impact before continuing.",
  failed: "Request failed",
  signedOut: "You have signed out.",
  updated: "Changes saved.",
  language: "Language",
  all: "All",
  used: "Used",
  unused: "Unused",
  revokedState: "Revoked",
  validUntil: "Valid until",
  createTitle: "New activation code",
  overviewFailure: "Could not load overview data.",
  signinError:
    "Unable to sign in. Check your credentials and authenticator code.",
  roleOp: "License operator",
  roleReader: "Read-only",
  state: "State",
  actor: "Actor",
  target: "Target",
  date: "Date",
  auditReason: "Reason",
  createPassword: "Temporary password",
  expiringHint: "Expires within 30 days",
  loading: "Loading…",
  languageToggle: "中文",
  none: "Not available",
  activationExpires: "Code expires",
  expiration: "Expiration",
  year: "Year",
  logoutConfirm: "Signed out successfully.",
  noPermission: "Your role cannot perform this action.",
  filter: "Filter records",
  viewDetails: "View details",
  close: "Close",
  instanceDetail: "Instance details",
  licenseStatus: "License status",
  activeLabel: "Online",
  inactiveLabel: "Offline",
  actionsRecorded: "All management actions are recorded in the audit trail.",
};
const zh = {
  app: "Maitu 授权中心",
  signIn: "登录",
  subtitle: "安全的授权运营平台",
  user: "用户名",
  password: "密码",
  totp: "身份验证器验证码",
  login: "安全登录",
  overview: "总览",
  customers: "客户管理",
  instances: "实例与授权",
  activations: "激活码",
  audit: "审计记录",
  operators: "操作员",
  system: "系统与密钥",
  signout: "退出登录",
  search: "搜索",
  refresh: "刷新",
  welcome: "授权总览",
  overviewText: "一览授权运营状态。",
  active: "有效",
  expiring: "即将到期",
  expired: "已到期",
  revoked: "已撤销",
  recent: "最近联系的实例",
  latest: "最新审计动态",
  create: "新建",
  edit: "编辑",
  save: "保存更改",
  cancel: "取消",
  name: "名称",
  contact: "联系人",
  notes: "备注",
  status: "状态",
  actions: "操作",
  activeState: "启用",
  disabled: "已停用",
  id: "编号",
  customer: "客户",
  expires: "授权截止",
  lastSeen: "最近联系",
  fingerprint: "公钥指纹",
  revision: "版本",
  license: "授权",
  extend: "延期",
  revoke: "撤销",
  restore: "恢复",
  replace: "更换主机",
  reason: "原因（必填）",
  confirm: "我了解此操作会影响客户实例",
  confirmTitle: "确认敏感操作",
  confirmAction: "确认并继续",
  reauthTitle: "验证身份",
  reauthHelp: "输入当前身份验证器验证码以授权此敏感更改。",
  continue: "验证并继续",
  code: "激活码",
  showOnce: "此密钥仅显示一次。请立即复制并妥善保存。",
  copied: "已复制",
  createCode: "生成激活码",
  ttl: "激活码有效期（小时）",
  createCustomer: "新建客户",
  saveCustomer: "保存客户",
  expiryNew: "新的授权截止时间",
  previous: "原截止时间",
  ownerOnly: "仅所有者",
  role: "角色",
  operator: "操作员",
  reader: "只读",
  owner: "所有者",
  createOperator: "新建操作员",
  totpEnroll: "验证器设置",
  secret: "密钥",
  provisioning: "配置 URI",
  verifyTotp: "验证器验证码",
  disable: "停用账户",
  enable: "启用账户",
  systemStatus: "服务状态",
  timezone: "服务器时区",
  signing: "签名密钥轮换",
  activeKey: "当前密钥 ID",
  stagedKey: "待启用密钥 ID",
  rotationStatus: "轮换状态",
  tls: "TLS 证书到期",
  cli: "密钥轮换属于高权限服务器操作。请在授权中心主机运行部署 CLI；不要在浏览器中执行轮换。",
  empty: "没有找到记录",
  reasonRequired: "必须填写操作原因。",
  confirmRequired: "请确认操作影响后继续。",
  failed: "请求失败",
  signedOut: "已安全退出。",
  updated: "更改已保存。",
  language: "语言",
  all: "全部",
  used: "已使用",
  unused: "未使用",
  revokedState: "已作废",
  validUntil: "有效期至",
  createTitle: "新建激活码",
  overviewFailure: "无法加载总览数据。",
  signinError: "登录失败，请检查账号、密码和验证码。",
  roleOp: "授权操作员",
  roleReader: "只读人员",
  state: "状态",
  actor: "操作人",
  target: "目标",
  date: "时间",
  auditReason: "原因",
  createPassword: "临时密码",
  expiringHint: "将在 30 天内到期",
  loading: "加载中…",
  languageToggle: "English",
  none: "暂无",
  activationExpires: "激活码到期",
  expiration: "到期时间",
  year: "年",
  logoutConfirm: "已成功退出。",
  noPermission: "当前角色无权执行此操作。",
  filter: "筛选记录",
  viewDetails: "查看详情",
  close: "关闭",
  instanceDetail: "实例详情",
  licenseStatus: "授权状态",
  activeLabel: "在线",
  inactiveLabel: "离线",
  actionsRecorded: "所有管理操作都会记录在审计日志中。",
};
Object.assign(en, {
  secureManagement: "SECURE MANAGEMENT",
  loginHeadline: "Licensing, made",
  loginHeadlineEmphasis: "clear and accountable.",
  loginDescription:
    "Manage customers, instances and access with a complete audit trail.",
  secureSession: "Secure cookie session · authenticator verified",
  controlPlane: "CONTROL PLANE",
  productionCenter: "Production center",
  recentHeartbeat: "Latest customer heartbeat",
  auditTrace: "Traceable changes across your center",
  instanceCount: "Instances",
  created: "Created",
  bound: "Bound",
  changeDetails: "Changes",
  action: "Action",
  ownerAccounts:
    "Owner accounts can create and manage operators. Each new operator must enroll TOTP before access.",
  reachable: "API reachable",
  enrollmentState: "Enrolled",
  enrollmentPending: "Enrollment pending or disabled",
  replaceWarning:
    "The replaced host is revoked at its next sync, but an offline host can continue using its cached license until that cache expires.",
  expiryMustIncrease:
    "Choose an expiry later than the current license deadline.",
  loadMore: "Load more",
  licenseEnded: "License ended",
  accessWithdrawn: "Access withdrawn",
  licenseOperations: "LICENSE OPERATIONS",
  configuredTimeZone: "Configured license-center time zone",
  ttl: "Code lifetime (minutes)",
  cli: "Key rotation must run on the license-center host. Stage a key with “uv run license-center stage-key”, add its public PEM to each client trust set, then activate the staged key with “uv run license-center activate-key <kid> --confirm”. Never copy or expose the private key.",
});
Object.assign(zh, {
  secureManagement: "安全管理",
  loginHeadline: "让授权管理",
  loginHeadlineEmphasis: "清晰且可追溯。",
  loginDescription: "统一管理客户、实例及访问权限，并保留完整审计记录。",
  secureSession: "安全 Cookie 会话 · 身份验证器已验证",
  controlPlane: "管理控制台",
  productionCenter: "生产授权中心",
  recentHeartbeat: "最近联系记录",
  auditTrace: "授权中心操作记录",
  instanceCount: "实例数",
  created: "创建时间",
  bound: "绑定时间",
  changeDetails: "变更详情",
  action: "操作",
  ownerAccounts:
    "所有者可创建和管理操作员。新操作员必须完成 TOTP 注册后才能访问。",
  reachable: "API 可访问",
  enrollmentState: "已注册",
  enrollmentPending: "待注册或已停用",
  replaceWarning:
    "旧主机下次同步时会收到撤销结果；若其处于离线状态，仍可能在本地缓存授权到期前继续运行。",
  expiryMustIncrease: "请选择晚于当前授权截止时间的新日期。",
  loadMore: "加载更多",
  licenseEnded: "授权已到期",
  accessWithdrawn: "访问已撤销",
  licenseOperations: "授权运营",
  configuredTimeZone: "授权中心配置的时区",
  ttl: "激活码有效期（分钟）",
  cli: "密钥轮换必须在授权中心主机执行：运行“uv run license-center stage-key”生成待启用密钥，将公钥 PEM 安全写入每台客户主机的可信密钥集合后，再运行“uv run license-center activate-key <kid> --confirm”。严禁复制或暴露私钥。",
});
Object.assign(en, {
  serviceRunning: "Running",
  tlsCertificate: "TLS certificate",
  tlsConfigured: "Configured in license center",
  tlsNotConfigured:
    "Not configured here (reverse-proxy certificates are not inspected)",
  tlsExpiry: "Certificate expires",
  rotationInstructions: "Privileged key rotation is CLI-only:",
  cliStage: "uv run license-center stage-key",
  cliActivate: "uv run license-center activate-key <kid> --confirm",
  trustExport: "uv run license-center export-trust [destination.json]",
  activeKeyState: "Active key available",
  stagedKeysState: "Staged keys available",
  noActiveKey: "No active key",
  noStagedKeys: "No staged keys",
  none: "None",
});
Object.assign(zh, {
  serviceRunning: "运行中",
  tlsCertificate: "TLS 证书",
  tlsConfigured: "已在授权中心配置",
  tlsNotConfigured: "此处未配置（不会读取反向代理证书）",
  tlsExpiry: "证书到期时间",
  rotationInstructions: "特权密钥轮换仅支持通过 CLI 操作：",
  cliStage: "uv run license-center stage-key",
  cliActivate: "uv run license-center activate-key <kid> --confirm",
  trustExport: "uv run license-center export-trust [destination.json]",
  activeKeyState: "活动密钥可用",
  stagedKeysState: "存在待启用密钥",
  noActiveKey: "无活动密钥",
  noStagedKeys: "无待启用密钥",
  none: "无",
});
Object.assign(en, {
  certConfigured: "Configured",
  certNotConfigured: "Not configured",
  certReadable: "Readable",
  certUnreadable: "Unreadable",
});
Object.assign(zh, {
  certConfigured: "已配置",
  certNotConfigured: "未配置",
  certReadable: "可读取",
  certUnreadable: "不可读取",
});
Object.assign(en, {
  targetTotpHelp:
    "Enter a current authenticator code from this operator to enable their account.",
});
Object.assign(zh, {
  targetTotpHelp: "输入该操作员当前有效的身份验证器代码以启用其账户。",
});
Object.assign(en, {
  invalidWallTime: "Enter a valid local date and time.",
  dstGap:
    "That local time does not exist in the configured time zone because of a daylight-saving transition.",
  dstAmbiguous:
    "That local time is ambiguous in the configured time zone because of a daylight-saving transition.",
});
Object.assign(zh, {
  invalidWallTime: "请输入有效的本地日期和时间。",
  dstGap: "由于夏令时切换，该本地时间在授权中心配置的时区中不存在。",
  dstAmbiguous: "由于夏令时切换，该本地时间在授权中心配置的时区中存在歧义。",
});
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, zh: { translation: zh } },
  lng: localStorage.getItem("license-locale") ?? "zh",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

type Page =
  | "overview"
  | "customers"
  | "instances"
  | "activations"
  | "audit"
  | "operators"
  | "system";
const nav: { id: Page; icon: typeof LayoutDashboard; owner?: boolean }[] = [
  { id: "overview", icon: LayoutDashboard },
  { id: "customers", icon: Building2 },
  { id: "instances", icon: ShieldCheck },
  { id: "activations", icon: Ticket },
  { id: "audit", icon: Activity },
  { id: "operators", icon: Users, owner: true },
  { id: "system", icon: KeyRound, owner: true },
];
function zoneParts(timestamp: number, zone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(timestamp));
  return Object.fromEntries(
    parts.map((part) => [part.type, Number(part.value)]),
  ) as Record<string, number>;
}
function localInZone(timestamp: number, zone: string) {
  const p = zoneParts(timestamp, zone);
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(
    p.day,
  ).padStart(2, "0")}T${String(p.hour).padStart(2, "0")}:${String(
    p.minute,
  ).padStart(2, "0")}`;
}
function zoneInputToEpoch(
  value: string,
  zone: string,
  t: (key: string) => string,
) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error(t("invalidWallTime"));
  const [, y, mo, d, h, mi] = match;
  const [year, month, day, hour, minute] = [y, mo, d, h, mi].map(Number);
  const target = Date.UTC(year, month - 1, day, hour, minute);
  const check = new Date(target);
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() + 1 !== month ||
    check.getUTCDate() !== day ||
    hour > 23 ||
    minute > 59
  )
    throw new Error(t("invalidWallTime"));
  const offsets = new Set<number>();
  for (let delta = -48; delta <= 48; delta += 6) {
    const sample = target + delta * 3600000;
    const p = zoneParts(sample, zone);
    offsets.add(
      Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) -
        Math.floor(sample / 60000) * 60000,
    );
  }
  const matches = new Set<number>();
  for (const offset of offsets) {
    const candidate = target - offset;
    const p = zoneParts(candidate, zone);
    if (
      p.year === year &&
      p.month === month &&
      p.day === day &&
      p.hour === hour &&
      p.minute === minute
    )
      matches.add(candidate);
  }
  if (matches.size === 0) throw new Error(t("dstGap"));
  if (matches.size > 1) throw new Error(t("dstAmbiguous"));
  return Math.floor([...matches][0] / 1000);
}
function dateFormat(
  value: number | null | undefined,
  zone: string,
  locale: string,
  style: "date" | "full" = "full",
) {
  if (value == null) return "—";
  return new Intl.DateTimeFormat(
    locale,
    style === "date"
      ? { timeZone: zone, year: "numeric", month: "short", day: "numeric" }
      : {
          timeZone: zone,
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        },
  ).format(new Date(value * 1000));
}
function App() {
  const { t, i18n: locale } = useTranslation();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState<Page>("overview");
  const sectionTitle = t(page);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [modal, setModal] = useState<{
    title: string;
    body: React.ReactNode;
  } | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [codes, setCodes] = useState<Activation[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [hasMoreAudit, setHasMoreAudit] = useState(false);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [system, setSystem] = useState<SystemReadout | null>(null);
  const isOwner = session?.user.role === "owner";
  const canManage =
    session?.user.role === "owner" || session?.user.role === "operator";
  const zone = session?.timezone ?? "Asia/Shanghai";
  const language = locale.language.startsWith("zh") ? "zh-CN" : "en-US";
  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 3500);
  };
  const loadPage = async (target = page) => {
    setError("");
    setBusy(true);
    try {
      if (target === "overview") {
        const [o, a, i, c] = await Promise.all([
          api.overview(),
          api.audit(),
          api.instances(),
          api.customers(),
        ]);
        setOverview(o);
        setAudit(a);
        setHasMoreAudit(a.length === 100);
        setInstances(i);
        setCustomers(c);
      }
      if (target === "customers") {
        const [c, i] = await Promise.all([api.customers(), api.instances()]);
        setCustomers(c);
        setInstances(i);
      }
      if (target === "instances") {
        const [i, c] = await Promise.all([api.instances(), api.customers()]);
        setInstances(i);
        setCustomers(c);
      }
      if (target === "activations") {
        const [a, c] = await Promise.all([api.activations(), api.customers()]);
        setCodes(a);
        setCustomers(c);
      }
      if (target === "audit") {
        const a = await api.audit();
        setAudit(a);
        setHasMoreAudit(a.length === 100);
      }
      if (target === "operators") setOperators(await api.operators());
      if (target === "system") setSystem(await api.system());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("failed"));
    } finally {
      setBusy(false);
    }
  };
  const loadMoreAudit = async () => {
    const beforeId = audit.at(-1)?.id;
    if (beforeId === undefined) return;
    setBusy(true);
    setError("");
    try {
      const older = await api.audit(beforeId);
      setAudit((current) => [...current, ...older]);
      setHasMoreAudit(older.length === 100);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("failed"));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (session) void loadPage(page);
  }, [session, page]);
  const signout = async () => {
    try {
      await api.logout();
      setSession(null);
      setModal(null);
      notify(t("logoutConfirm"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("failed"));
    }
  };
  const switchLanguage = () => {
    const next = locale.language.startsWith("zh") ? "en" : "zh";
    void locale.changeLanguage(next);
    localStorage.setItem("license-locale", next);
  };
  const requireSensitive = (
    title: string,
    reasoned: boolean,
    action: (reason: string) => Promise<unknown>,
    caution?: string,
  ) => {
    let reason = "";
    let confirmed = false;
    let totp = "";
    const render = (stage: "action" | "reauth") =>
      setModal({
        title: stage === "action" ? title : t("reauthTitle"),
        body: (
          <form
            className="modal-form"
            onSubmit={async (e) => {
              e.preventDefault();
              if (stage === "action") {
                if (reasoned && !reason.trim())
                  return setError(t("reasonRequired"));
                if (!confirmed) return setError(t("confirmRequired"));
                render("reauth");
                return;
              }
              setBusy(true);
              try {
                await api.reauth(totp);
                setModal(null);
                await action(reason.trim());
                setError("");
                notify(t("updated"));
                await loadPage();
              } catch (err) {
                setError(err instanceof Error ? err.message : t("failed"));
              } finally {
                setBusy(false);
              }
            }}
          >
            {stage === "action" ? (
              <>
                <p className="muted">{t("actionsRecorded")}</p>
                {caution && (
                  <div className="cli-note">
                    <CircleAlert size={17} />
                    {caution}
                  </div>
                )}
                {reasoned && (
                  <label>
                    {t("reason")}
                    <textarea
                      required
                      rows={3}
                      onChange={(e) => {
                        reason = e.target.value;
                      }}
                    />
                  </label>
                )}
                <label className="checkline">
                  <input
                    type="checkbox"
                    onChange={(e) => {
                      confirmed = e.target.checked;
                    }}
                  />
                  {t("confirm")}
                </label>
                <div className="modal-actions">
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() => setModal(null)}
                  >
                    {t("cancel")}
                  </button>
                  <button className="button primary">{t("continue")}</button>
                </div>
              </>
            ) : (
              <>
                <p className="muted">{t("reauthHelp")}</p>
                <label>
                  {t("totp")}
                  <input
                    autoFocus
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    required
                    minLength={6}
                    maxLength={8}
                    onChange={(e) => {
                      totp = e.target.value;
                    }}
                  />
                </label>
                <div className="modal-actions">
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() => setModal(null)}
                  >
                    {t("cancel")}
                  </button>
                  <button className="button primary" disabled={busy}>
                    {t("continue")}
                  </button>
                </div>
              </>
            )}
          </form>
        ),
      });
    render("action");
  };
  const createCustomer = () => {
    let name = "",
      contact = "",
      notes = "";
    setModal({
      title: t("createCustomer"),
      body: (
        <form
          className="modal-form"
          onSubmit={(e) => {
            e.preventDefault();
            requireSensitive(t("createCustomer"), false, async () => {
              await api.createCustomer({
                name: name.trim(),
                contact: contact.trim(),
                notes: notes.trim(),
              });
            });
          }}
        >
          <label>
            {t("name")}
            <input
              required
              maxLength={200}
              onChange={(e) => {
                name = e.target.value;
              }}
            />
          </label>
          <label>
            {t("contact")}
            <input
              maxLength={500}
              onChange={(e) => {
                contact = e.target.value;
              }}
            />
          </label>
          <label>
            {t("notes")}
            <textarea
              rows={3}
              onChange={(e) => {
                notes = e.target.value;
              }}
            />
          </label>
          <div className="modal-actions">
            <button
              type="button"
              className="button ghost"
              onClick={() => setModal(null)}
            >
              {t("cancel")}
            </button>
            <button className="button primary">{t("create")}</button>
          </div>
        </form>
      ),
    });
  };
  const editCustomer = (item: Customer) => {
    let name = item.name,
      contact = item.contact,
      notes = item.notes,
      active = item.active;
    setModal({
      title: `${t("edit")} · ${item.name}`,
      body: (
        <form
          className="modal-form"
          onSubmit={(e) => {
            e.preventDefault();
            const changingStatus = active !== item.active;
            requireSensitive(
              t("saveCustomer"),
              changingStatus,
              async (auditReason) => {
                await api.updateCustomer(item.id, {
                  name,
                  contact,
                  notes,
                  active,
                  version: item.version,
                  ...(changingStatus ? { reason: auditReason } : {}),
                });
              },
            );
          }}
        >
          <label>
            {t("name")}
            <input
              required
              defaultValue={name}
              onChange={(e) => {
                name = e.target.value;
              }}
            />
          </label>
          <label>
            {t("contact")}
            <input
              defaultValue={contact}
              onChange={(e) => {
                contact = e.target.value;
              }}
            />
          </label>
          <label>
            {t("notes")}
            <textarea
              defaultValue={notes}
              rows={3}
              onChange={(e) => {
                notes = e.target.value;
              }}
            />
          </label>
          <label>
            {t("status")}
            <select
              defaultValue={String(active)}
              onChange={(e) => {
                active = e.target.value === "true";
              }}
            >
              <option value="true">{t("activeState")}</option>
              <option value="false">{t("disabled")}</option>
            </select>
          </label>
          <div className="modal-actions">
            <button
              type="button"
              className="button ghost"
              onClick={() => setModal(null)}
            >
              {t("cancel")}
            </button>
            <button className="button primary">{t("save")}</button>
          </div>
        </form>
      ),
    });
  };
  const createActivation = () => {
    let customerId = customers[0]?.id ?? "",
      expiry = localInZone(Date.now() + 365 * 86400000, zone),
      ttl = 1440;
    setModal({
      title: t("createTitle"),
      body: (
        <form
          className="modal-form"
          onSubmit={(e) => {
            e.preventDefault();
            requireSensitive(t("createCode"), false, async () => {
              const created = await api.createActivation({
                customer_id: customerId,
                expires_at: zoneInputToEpoch(expiry, zone, t),
                ttl_seconds: ttl * 60,
              });
              setModal({
                title: t("code"),
                body: (
                  <div className="secret-box">
                    <p>{t("showOnce")}</p>
                    <code>{created.code}</code>
                    <button
                      type="button"
                      className="button primary"
                      onClick={() => {
                        void navigator.clipboard
                          .writeText(created.code)
                          .then(() => notify(t("copied")));
                      }}
                    >
                      {t("copied")}
                    </button>
                  </div>
                ),
              });
            });
          }}
        >
          <label>
            {t("customer")}
            <select
              defaultValue={customerId}
              required
              onChange={(e) => {
                customerId = e.target.value;
              }}
            >
              {customers
                .filter((c) => c.active)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            {t("expires")} <small>({zone})</small>
            <input
              type="datetime-local"
              min={localInZone(Date.now(), zone)}
              defaultValue={expiry}
              required
              onChange={(e) => {
                expiry = e.target.value;
              }}
            />
          </label>
          <label>
            {t("ttl")}
            <input
              type="number"
              min={1}
              max={43200}
              defaultValue={ttl}
              onChange={(e) => {
                ttl = Number(e.target.value);
              }}
            />
          </label>
          <div className="modal-actions">
            <button
              type="button"
              className="button ghost"
              onClick={() => setModal(null)}
            >
              {t("cancel")}
            </button>
            <button className="button primary" disabled={!customerId}>
              {t("createCode")}
            </button>
          </div>
        </form>
      ),
    });
  };
  const licenseAction = (
    instance: Instance,
    kind: "extend" | "revoke" | "restore" | "replace",
  ) => {
    const expired = instance.license_expires_at <= Date.now() / 1000;
    let expiry = localInZone(
      (kind === "restore" && expired
        ? Date.now()
        : Math.max(Date.now(), instance.license_expires_at * 1000)) +
        365 * 86400000,
      zone,
    );
    const perform = (reason: string) => {
      if (kind === "extend")
        return api.extend(instance.license_id, {
          expires_at: zoneInputToEpoch(expiry, zone, t),
          reason,
          version: instance.license_revision,
        });
      if (kind === "revoke")
        return api.revoke(instance.license_id, {
          reason,
          version: instance.license_revision,
        });
      if (kind === "restore")
        return api.restore(instance.license_id, {
          ...(instance.license_expires_at <= Date.now() / 1000
            ? { expires_at: zoneInputToEpoch(expiry, zone, t) }
            : {}),
          reason,
          version: instance.license_revision,
        });
      return api
        .replace(instance.id, {
          reason,
          expires_at: instance.license_expires_at,
          version: instance.license_revision,
        })
        .then((activation) => {
          if (!activation.code)
            throw new Error(
              "The server did not return the one-time replacement activation code.",
            );
          setModal({
            title: t("code"),
            body: (
              <div className="secret-box">
                <p>{t("showOnce")}</p>
                <code>{activation.code}</code>
                <button
                  type="button"
                  className="button primary"
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(activation.code!)
                      .then(() => notify(t("copied")));
                  }}
                >
                  {t("copied")}
                </button>
              </div>
            ),
          });
        });
    };
    if (
      kind === "extend" ||
      (kind === "restore" && instance.license_expires_at <= Date.now() / 1000)
    ) {
      const title = kind === "extend" ? t("extend") : t("restore");
      setModal({
        title: `${title} · ${instance.id}`,
        body: (
          <form
            className="modal-form"
            onSubmit={(e) => {
              e.preventDefault();
              let newExpiry: number;
              try {
                newExpiry = zoneInputToEpoch(expiry, zone, t);
              } catch (error) {
                return setError(
                  error instanceof Error ? error.message : t("failed"),
                );
              }
              if (
                newExpiry <= Date.now() / 1000 ||
                (kind === "extend" && newExpiry <= instance.license_expires_at)
              )
                return setError(t("expiryMustIncrease"));
              requireSensitive(title, true, perform);
            }}
          >
            <div className="expiry-compare">
              <span>
                {t("previous")}
                <b>{dateFormat(instance.license_expires_at, zone, language)}</b>
              </span>
              <span>
                {t("expiryNew")}
                <input
                  type="datetime-local"
                  min={localInZone(Date.now(), zone)}
                  defaultValue={expiry}
                  onChange={(e) => {
                    expiry = e.target.value;
                  }}
                />
              </span>
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="button ghost"
                onClick={() => setModal(null)}
              >
                {t("cancel")}
              </button>
              <button className="button primary">{title}</button>
            </div>
          </form>
        ),
      });
      return;
    }
    requireSensitive(
      t(kind),
      true,
      perform,
      kind === "replace" ? t("replaceWarning") : undefined,
    );
  };
  const createOperator = () => {
    let username = "",
      password = "",
      role: "operator" | "reader" = "operator";
    setModal({
      title: t("createOperator"),
      body: (
        <form
          className="modal-form"
          onSubmit={(e) => {
            e.preventDefault();
            requireSensitive(t("createOperator"), false, async () => {
              const enrollment = await api.createOperator({
                username,
                password,
                role,
              });
              setModal({
                title: t("totpEnroll"),
                body: (
                  <EnrollmentCard
                    enrollment={enrollment}
                    onConfirm={async (code) => {
                      await api.confirmTotp(enrollment.id, code);
                      setModal(null);
                      notify(t("updated"));
                      await loadPage();
                    }}
                  />
                ),
              });
            });
          }}
        >
          <label>
            {t("user")}
            <input
              required
              autoComplete="off"
              onChange={(e) => {
                username = e.target.value;
              }}
            />
          </label>
          <label>
            {t("createPassword")}
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              onChange={(e) => {
                password = e.target.value;
              }}
            />
          </label>
          <label>
            {t("role")}
            <select
              onChange={(e) => {
                role = e.target.value as "operator" | "reader";
              }}
            >
              <option value="operator">{t("roleOp")}</option>
              <option value="reader">{t("roleReader")}</option>
            </select>
          </label>
          <div className="modal-actions">
            <button
              type="button"
              className="button ghost"
              onClick={() => setModal(null)}
            >
              {t("cancel")}
            </button>
            <button className="button primary">{t("create")}</button>
          </div>
        </form>
      ),
    });
  };
  const filtered = <T extends { id: string | number }>(rows: T[]) =>
    rows.filter((row) =>
      JSON.stringify(row).toLowerCase().includes(search.toLowerCase()),
    );
  const customerMap = useMemo(
    () => new Map(customers.map((x) => [x.id, x.name])),
    [customers],
  );
  if (checking)
    return (
      <main className="login-screen" aria-busy="true">
        <p>{t("loading")}</p>
      </main>
    );
  if (!session)
    return (
      <main className="login-screen">
        <div className="login-visual">
          <div className="brand">
            <span className="brand-mark">
              <Shield size={23} />
            </span>
            <strong>{t("app")}</strong>
          </div>
          <div className="visual-copy">
            <p className="eyebrow">{t("secureManagement")}</p>
            <h1>
              {t("loginHeadline")}
              <br />
              <em>{t("loginHeadlineEmphasis")}</em>
            </h1>
            <p>{t("loginDescription")}</p>
          </div>
          <div className="login-foot">© Maitu · License operations</div>
        </div>
        <section className="login-form-wrap">
          <button className="language-button" onClick={switchLanguage}>
            <Languages size={16} />
            {t("languageToggle")}
          </button>
          <form
            className="login-card"
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              setBusy(true);
              setError("");
              try {
                setSession(
                  await api.login(
                    String(form.get("username")),
                    String(form.get("password")),
                    String(form.get("totp")),
                  ),
                );
              } catch {
                setError(t("signinError"));
              } finally {
                setBusy(false);
              }
            }}
          >
            <span className="overline">{t("subtitle")}</span>
            <h2>{t("signIn")}</h2>
            <p className="muted">{t("loginDescription")}</p>
            <label>
              {t("user")}
              <input
                autoFocus
                name="username"
                autoComplete="username"
                required
              />
            </label>
            <label>
              {t("password")}
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </label>
            <label>
              {t("totp")}
              <input
                name="totp"
                inputMode="numeric"
                autoComplete="one-time-code"
                minLength={6}
                maxLength={8}
                required
              />
            </label>
            {error && (
              <div className="inline-error">
                <CircleAlert size={16} />
                {error}
              </div>
            )}
            <button className="button primary login-submit" disabled={busy}>
              {busy ? t("loading") : t("login")}
            </button>
            <div className="login-note">
              <ShieldCheck size={15} />
              {t("secureSession")}
            </div>
          </form>
        </section>
      </main>
    );
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Shield size={21} />
          </span>
          <div>
            <strong>{t("app")}</strong>
            <small>{t("controlPlane")}</small>
          </div>
        </div>
        <div className="workspace">
          <span className="workspace-icon">LC</span>
          <div>
            <b>{t("app")}</b>
            <small>{t("productionCenter")}</small>
          </div>
          <ChevronDown size={15} />
        </div>
        <nav>
          {nav
            .filter((item) => !item.owner || isOwner)
            .map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  className={`nav-link ${page === item.id ? "selected" : ""}`}
                  onClick={() => {
                    setPage(item.id);
                    setSearch("");
                    setError("");
                  }}
                >
                  <Icon size={17} />
                  <span>{t(item.id)}</span>
                  {item.id === "audit" && <i className="nav-dot" />}
                </button>
              );
            })}
        </nav>
        <div className="sidebar-bottom">
          <div className="secure-label">
            <span className="status-pulse" />
            {t("systemStatus")}: <b>{system ? t("serviceRunning") : "—"}</b>
          </div>
          <div className="profile">
            <div className="avatar">
              {session.user.username.slice(0, 1).toUpperCase()}
            </div>
            <div className="profile-copy">
              <b>{session.user.username}</b>
              <small>{t(session.user.role)}</small>
            </div>
            <button
              className="icon-button"
              title={t("signout")}
              onClick={() => void signout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>{t("app")}</span>
            <b>/</b>
            <strong>{sectionTitle}</strong>
          </div>
          <div className="top-actions">
            <div className="timezone-chip">
              <Clock3 size={14} />
              {zone}
            </div>
            <button
              className="icon-button language-control"
              onClick={switchLanguage}
            >
              <Languages size={17} />
              <span>{t("languageToggle")}</span>
            </button>
            <span className="top-separator" />
            <button className="user-chip" onClick={() => void signout()}>
              <span className="avatar small">
                {session.user.username.slice(0, 1).toUpperCase()}
              </span>
              <span>{session.user.username}</span>
              <LogOut size={15} />
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <span className="overline">
                {page === "overview" ? t("licenseOperations") : t(page)}
              </span>
              <h1>{page === "overview" ? t("welcome") : sectionTitle}</h1>
              <p>
                {page === "overview" ? t("overviewText") : t("actionsRecorded")}
              </p>
            </div>
            <div className="heading-actions">
              {(page === "customers" || page === "activations") &&
                canManage && (
                  <button
                    className="button primary"
                    onClick={() =>
                      page === "customers"
                        ? createCustomer()
                        : createActivation()
                    }
                  >
                    <Plus size={16} />
                    {page === "customers"
                      ? t("createCustomer")
                      : t("createCode")}
                  </button>
                )}
              {page === "operators" && isOwner && (
                <button className="button primary" onClick={createOperator}>
                  <Plus size={16} />
                  {t("createOperator")}
                </button>
              )}
              <button
                className="button ghost refresh"
                onClick={() => void loadPage()}
                disabled={busy}
              >
                <RefreshCw size={15} className={busy ? "spin" : ""} />
                {t("refresh")}
              </button>
            </div>
          </div>
          {error && (
            <div className="error-banner">
              <CircleAlert size={17} />
              {error}
              <button onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {page !== "overview" && page !== "system" && (
            <div className="toolbar">
              <div className="searchbox">
                <Search size={17} />
                <input
                  placeholder={`${t("search")} ${sectionTitle.toLowerCase()}…`}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <span>{t("filter")}</span>
            </div>
          )}
          {page === "overview" && (
            <>
              {overview ? (
                <div className="stat-grid">
                  <StatCard
                    label={t("active")}
                    value={overview.active_licenses}
                    icon={<BadgeCheck />}
                    color="teal"
                    helper={t("activeLabel")}
                  />
                  <StatCard
                    label={t("expiring")}
                    value={overview.expiring_soon}
                    icon={<Clock3 />}
                    color="amber"
                    helper={t("expiringHint")}
                  />
                  <StatCard
                    label={t("expired")}
                    value={overview.expired_licenses}
                    icon={<CircleAlert />}
                    color="rose"
                    helper={t("licenseEnded")}
                  />
                  <StatCard
                    label={t("revoked")}
                    value={overview.revoked_licenses}
                    icon={<Shield />}
                    color="slate"
                    helper={t("accessWithdrawn")}
                  />
                </div>
              ) : (
                <div className="panel placeholder">{t("overviewFailure")}</div>
              )}
              <div className="overview-grid">
                <section className="panel">
                  <div className="panel-head">
                    <div>
                      <h3>{t("recent")}</h3>
                      <p>{t("recentHeartbeat")}</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => setPage("instances")}
                    >
                      {t("viewDetails")} →
                    </button>
                  </div>
                  <InstancesTable
                    items={(overview?.recent_contacts ?? [])
                      .map((contact) =>
                        instances.find(
                          (instance) => instance.id === contact.id,
                        ),
                      )
                      .filter((instance): instance is Instance =>
                        Boolean(instance),
                      )
                      .sort(
                        (a, b) => (b.last_seen_at ?? 0) - (a.last_seen_at ?? 0),
                      )}
                    zone={zone}
                    language={language}
                    customerMap={customerMap}
                    t={t}
                    onAction={licenseAction}
                    manage={canManage}
                  />
                </section>
                <section className="panel">
                  <div className="panel-head">
                    <div>
                      <h3>{t("latest")}</h3>
                      <p>{t("auditTrace")}</p>
                    </div>
                  </div>
                  <AuditList
                    items={audit.slice(0, 6)}
                    zone={zone}
                    language={language}
                    t={t}
                  />
                </section>
              </div>
            </>
          )}
          {page === "customers" && (
            <section className="panel">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("customer")}</th>
                      <th>{t("contact")}</th>
                      <th>{t("status")}</th>
                      <th>{t("instanceCount")}</th>
                      <th>{t("created")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {filtered(customers).map((c) => (
                      <tr key={c.id}>
                        <td>
                          <div className="entity">
                            <span className="entity-icon">
                              <Building2 size={16} />
                            </span>
                            <span>
                              <b>{c.name}</b>
                              <small>{c.id}</small>
                            </span>
                          </div>
                        </td>
                        <td>{c.contact || "—"}</td>
                        <td>
                          <Status
                            active={c.active}
                            label={c.active ? t("activeState") : t("disabled")}
                          />
                        </td>
                        <td>
                          {instances.filter((i) => i.customer_id === c.id)
                            .length || "—"}
                        </td>
                        <td>
                          {dateFormat(c.created_at, zone, language, "date")}
                        </td>
                        <td>
                          <button
                            className="row-action"
                            disabled={!canManage}
                            onClick={() => editCustomer(c)}
                          >
                            {t("edit")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!customers.length && <Empty t={t} />}
              </div>
            </section>
          )}
          {page === "instances" && (
            <section className="panel">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("instanceDetail")}</th>
                      <th>{t("customer")}</th>
                      <th>{t("licenseStatus")}</th>
                      <th>{t("expires")}</th>
                      <th>{t("lastSeen")}</th>
                      <th>{t("revision")}</th>
                      <th>{t("actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered(instances).map((i) => (
                      <tr key={i.id}>
                        <td>
                          <div className="entity">
                            <span className="entity-icon purple">
                              <ShieldCheck size={16} />
                            </span>
                            <span>
                              <b className="mono">{i.id}</b>
                              <small>
                                {t("fingerprint")}:{" "}
                                {i.public_key_fingerprint.slice(0, 16)}…
                              </small>
                            </span>
                          </div>
                        </td>
                        <td>
                          {customerMap.get(i.customer_id) ?? i.customer_id}
                        </td>
                        <td>
                          <Status
                            active={i.license_status === "active"}
                            label={i.license_status}
                          />
                        </td>
                        <td>
                          {dateFormat(i.license_expires_at, zone, language)}
                        </td>
                        <td>{dateFormat(i.last_seen_at, zone, language)}</td>
                        <td>
                          <span className="revision">
                            v{i.license_revision}
                          </span>
                        </td>
                        <td>
                          {canManage && (
                            <div className="action-menu">
                              <button
                                className="row-action"
                                onClick={() => licenseAction(i, "extend")}
                              >
                                {t("extend")}
                              </button>
                              {i.license_status === "revoked" ? (
                                <button
                                  className="row-action positive"
                                  onClick={() => licenseAction(i, "restore")}
                                >
                                  {t("restore")}
                                </button>
                              ) : (
                                <button
                                  className="row-action danger"
                                  onClick={() => licenseAction(i, "revoke")}
                                >
                                  {t("revoke")}
                                </button>
                              )}
                              <button
                                className="row-action"
                                onClick={() => licenseAction(i, "replace")}
                              >
                                {t("replace")}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!instances.length && <Empty t={t} />}
              </div>
            </section>
          )}
          {page === "audit" && (
            <>
              <section className="panel">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("date")}</th>
                        <th>{t("actor")}</th>
                        <th>{t("action")}</th>
                        <th>{t("target")}</th>
                        <th>{t("auditReason")}</th>
                        <th>{t("changeDetails")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered(audit).map((a) => (
                        <tr key={a.id}>
                          <td>{dateFormat(a.created_at, zone, language)}</td>
                          <td>{a.actor}</td>
                          <td>
                            <span className="action-tag">{a.action}</span>
                          </td>
                          <td>
                            {a.object_type}{" "}
                            <span className="mono">{a.object_id}</span>
                          </td>
                          <td className="reason-cell">{a.reason || "—"}</td>
                          <td>
                            <button
                              className="row-action"
                              onClick={() =>
                                setModal({
                                  title: `${t("audit")} #${a.id}`,
                                  body: (
                                    <pre className="json-view">
                                      {JSON.stringify(
                                        {
                                          before: parseJson(a.before_json),
                                          after: parseJson(a.after_json),
                                        },
                                        null,
                                        2,
                                      )}
                                    </pre>
                                  ),
                                })
                              }
                            >
                              {t("viewDetails")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!audit.length && <Empty t={t} />}
                </div>
              </section>
              {hasMoreAudit && (
                <div className="load-more">
                  <button
                    className="button ghost"
                    onClick={() => void loadMoreAudit()}
                    disabled={busy}
                  >
                    {busy ? t("loading") : t("loadMore")}
                  </button>
                </div>
              )}
            </>
          )}
          {page === "activations" && (
            <section className="panel">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("code")}</th>
                      <th>{t("customer")}</th>
                      <th>{t("activationExpires")}</th>
                      <th>{t("status")}</th>
                      <th>{t("bound")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {filtered(codes).map((c) => (
                      <tr key={c.id}>
                        <td className="mono">{c.id}</td>
                        <td>
                          {customerMap.get(c.customer_id) ?? c.customer_id}
                        </td>
                        <td>{dateFormat(c.expires_at, zone, language)}</td>
                        <td>
                          <Status
                            active={
                              !c.revoked_at &&
                              !c.used_at &&
                              c.expires_at > Date.now() / 1000
                            }
                            label={
                              c.revoked_at
                                ? t("revokedState")
                                : c.used_at
                                ? t("used")
                                : c.expires_at < Date.now() / 1000
                                ? t("expired")
                                : t("unused")
                            }
                          />
                        </td>
                        <td>
                          {c.used_at
                            ? dateFormat(c.used_at, zone, language)
                            : "—"}
                        </td>
                        <td>
                          {canManage && !c.revoked_at && !c.used_at && (
                            <button
                              className="row-action danger"
                              onClick={() =>
                                requireSensitive(
                                  t("revoke"),
                                  true,
                                  async (reason) =>
                                    api.revokeActivation(c.id, reason),
                                )
                              }
                            >
                              {t("revoke")}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!codes.length && <Empty t={t} />}
              </div>
            </section>
          )}
          {page === "operators" && (
            <section className="panel">
              <div className="owner-banner">
                <ShieldCheck size={19} />
                <span>
                  <b>{t("ownerOnly")}</b>
                  <small>{t("ownerAccounts")}</small>
                </span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("user")}</th>
                      <th>{t("role")}</th>
                      <th>{t("totpEnroll")}</th>
                      <th>{t("status")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {filtered(operators).map((o) => (
                      <tr key={o.id}>
                        <td>
                          <div className="entity">
                            <span className="avatar">
                              {o.username.slice(0, 1).toUpperCase()}
                            </span>
                            <b>{o.username}</b>
                          </div>
                        </td>
                        <td>
                          <select
                            className="inline-select"
                            disabled={o.id === session.user.id}
                            value={o.role}
                            onChange={(e) => {
                              const role = e.target.value as Role;
                              requireSensitive(t("role"), false, async () =>
                                api.updateOperator(o.id, {
                                  role,
                                  active: o.active,
                                }),
                              );
                            }}
                          >
                            <option value="owner">{t("owner")}</option>
                            <option value="operator">{t("operator")}</option>
                            <option value="reader">{t("reader")}</option>
                          </select>
                        </td>
                        <td>
                          <Status
                            active={o.active}
                            label={
                              o.active
                                ? t("enrollmentState")
                                : t("enrollmentPending")
                            }
                          />
                        </td>
                        <td>
                          <Status
                            active={o.active}
                            label={o.active ? t("activeState") : t("disabled")}
                          />
                        </td>
                        <td>
                          <button
                            className={`row-action ${
                              o.active ? "danger" : "positive"
                            }`}
                            disabled={o.id === session.user.id}
                            onClick={() => {
                              if (o.active) {
                                requireSensitive(
                                  t("disable"),
                                  false,
                                  async () =>
                                    api.updateOperator(o.id, {
                                      role: o.role,
                                      active: false,
                                    }),
                                );
                                return;
                              }
                              let totp = "";
                              setModal({
                                title: t("enable"),
                                body: (
                                  <form
                                    className="modal-form"
                                    onSubmit={(e) => {
                                      e.preventDefault();
                                      requireSensitive(
                                        t("enable"),
                                        false,
                                        async () =>
                                          api.updateOperator(o.id, {
                                            role: o.role,
                                            active: true,
                                            totp,
                                          }),
                                      );
                                    }}
                                  >
                                    <p className="muted">
                                      {t("targetTotpHelp")}
                                    </p>
                                    <label>
                                      {t("totp")}
                                      <input
                                        required
                                        minLength={6}
                                        maxLength={8}
                                        inputMode="numeric"
                                        autoComplete="one-time-code"
                                        onChange={(e) => {
                                          totp = e.target.value;
                                        }}
                                      />
                                    </label>
                                    <div className="modal-actions">
                                      <button
                                        type="button"
                                        className="button ghost"
                                        onClick={() => setModal(null)}
                                      >
                                        {t("cancel")}
                                      </button>
                                      <button className="button primary">
                                        {t("continue")}
                                      </button>
                                    </div>
                                  </form>
                                ),
                              });
                            }}
                          >
                            {o.active ? t("disable") : t("enable")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!operators.length && <Empty t={t} />}
              </div>
            </section>
          )}
          {page === "system" && (
            <>
              {system ? (
                <div className="system-grid">
                  <section className="panel system-card">
                    <div className="system-icon green">
                      <Activity />
                    </div>
                    <div className="overline">{t("serviceStatus")}</div>
                    <h2>{t("serviceRunning")}</h2>
                    <p>
                      {t("timezone")}: {system.timezone}
                    </p>
                    <div className="system-meta">
                      <span>{t("tlsCertificate")}</span>
                      <b>
                        {system.tls_certificate_configured
                          ? t("certConfigured")
                          : t("certNotConfigured")}
                      </b>
                    </div>
                    {system.tls_certificate_configured && (
                      <>
                        <div className="system-meta">
                          <span>{t("status")}</span>
                          <b>
                            {system.tls_certificate_readable
                              ? t("certReadable")
                              : t("certUnreadable")}
                          </b>
                        </div>
                        <div className="system-meta">
                          <span>{t("tlsExpiry")}</span>
                          <b>
                            {system.tls_certificate_expires_at
                              ? dateFormat(
                                  system.tls_certificate_expires_at,
                                  zone,
                                  language,
                                )
                              : "—"}
                          </b>
                        </div>
                      </>
                    )}
                    <p>
                      {system.tls_certificate_configured
                        ? t("tlsConfigured")
                        : t("tlsNotConfigured")}
                    </p>
                  </section>
                  <section className="panel key-card">
                    <div className="key-top">
                      <span className="system-icon purple">
                        <KeyRound />
                      </span>
                      <span className="key-badge">
                        {system.staged_signing_kids.length
                          ? t("stagedKeysState")
                          : t("activeKeyState")}
                      </span>
                    </div>
                    <div className="overline">{t("signing")}</div>
                    <h2>Ed25519</h2>
                    <div className="system-meta">
                      <span>{t("activeKey")}</span>
                      <b className="mono">
                        {system.active_signing_kid ?? t("noActiveKey")}
                      </b>
                    </div>
                    <div className="system-meta">
                      <span>{t("stagedKey")}</span>
                      <b className="mono">
                        {system.staged_signing_kids.length
                          ? system.staged_signing_kids.join(", ")
                          : t("noStagedKeys")}
                      </b>
                    </div>
                    <div className="cli-note">
                      <Shield size={17} />
                      <span>
                        {t("rotationInstructions")}
                        <br />
                        <code>{t("trustExport")}</code>
                        <br />
                        <code>{t("cliStage")}</code>
                        <br />
                        <code>{t("cliActivate")}</code>
                        <br />
                        {t("cli")}
                      </span>
                    </div>
                  </section>
                </div>
              ) : (
                <div className="panel placeholder">{t("loading")}</div>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>MAITU LICENSE CENTER</span>
            <span>
              <ShieldCheck size={14} /> {t("actionsRecorded")}
            </span>
          </footer>
        </div>
      </main>
      {toast && (
        <div className="toast">
          <BadgeCheck size={17} />
          {toast}
        </div>
      )}
      {modal && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setModal(null);
          }}
        >
          <section className="modal">
            <header>
              <h3>{modal.title}</h3>
              <button className="icon-button" onClick={() => setModal(null)}>
                <X size={18} />
              </button>
            </header>
            {modal.body}
          </section>
        </div>
      )}
    </div>
  );
}
function parseJson(text: string) {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}
function StatCard({
  label,
  value,
  icon,
  color,
  helper,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  color: string;
  helper: string;
}) {
  return (
    <div className="panel stat-card">
      <div className={`stat-icon ${color}`}>{icon}</div>
      <span className="stat-label">{label}</span>
      <strong>{value.toLocaleString()}</strong>
      <small>{helper}</small>
    </div>
  );
}
function Status({ active, label }: { active: boolean; label: string }) {
  return (
    <span className={`status-badge ${active ? "good" : "muted-status"}`}>
      <i />
      {label}
    </span>
  );
}
function Empty({ t }: { t: (key: string) => string }) {
  return (
    <div className="empty">
      <span>
        <Search size={19} />
      </span>
      <b>{t("empty")}</b>
    </div>
  );
}
function InstancesTable({
  items,
  zone,
  language,
  customerMap,
  t,
  onAction,
  manage,
}: {
  items: Instance[];
  zone: string;
  language: string;
  customerMap: Map<string, string>;
  t: (key: string) => string;
  onAction: (
    i: Instance,
    kind: "extend" | "revoke" | "restore" | "replace",
  ) => void;
  manage: boolean;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t("instanceDetail")}</th>
            <th>{t("customer")}</th>
            <th>{t("expires")}</th>
            <th>{t("lastSeen")}</th>
            {manage && <th />}
          </tr>
        </thead>
        <tbody>
          {items.slice(0, 7).map((i) => (
            <tr key={i.id}>
              <td>
                <b className="mono">{i.id}</b>
                <small className="table-sub">
                  {i.public_key_fingerprint.slice(0, 12)}…
                </small>
              </td>
              <td>{customerMap.get(i.customer_id) ?? i.customer_id}</td>
              <td>
                {dateFormat(i.license_expires_at, zone, language, "date")}
              </td>
              <td>{dateFormat(i.last_seen_at, zone, language, "date")}</td>
              {manage && (
                <td>
                  <button
                    className="row-action"
                    onClick={() => onAction(i, "extend")}
                  >
                    {t("extend")}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <Empty t={t} />}
    </div>
  );
}
function AuditList({
  items,
  zone,
  language,
  t,
}: {
  items: AuditEntry[];
  zone: string;
  language: string;
  t: (key: string) => string;
}) {
  return (
    <div className="audit-list">
      {items.map((a) => (
        <div className="audit-item" key={a.id}>
          <span className="audit-symbol">
            <Activity size={15} />
          </span>
          <div>
            <b>{a.action}</b>
            <span>
              {a.actor} · {a.object_type} {a.object_id}
            </span>
            {a.reason && <p>{a.reason}</p>}
            <small>{dateFormat(a.created_at, zone, language)}</small>
          </div>
        </div>
      ))}
      {!items.length && <Empty t={t} />}
    </div>
  );
}
function EnrollmentCard({
  enrollment,
  onConfirm,
}: {
  enrollment: Enrollment;
  onConfirm: (code: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <form
      className="modal-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr("");
        try {
          await onConfirm(code);
        } catch (x) {
          setErr(x instanceof Error ? x.message : t("failed"));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="owner-banner">
        <ShieldCheck size={19} />
        <span>
          <b>{t("showOnce")}</b>
          <small>{enrollment.username}</small>
        </span>
      </div>
      <label>
        {t("secret")}
        <code className="secret-inline">{enrollment.totp_secret}</code>
      </label>
      <label>
        {t("provisioning")}
        <textarea readOnly rows={3} value={enrollment.provisioning_uri} />
      </label>
      <label>
        {t("verifyTotp")}
        <input
          required
          minLength={6}
          maxLength={8}
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      {err && <div className="inline-error">{err}</div>}
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          {t("continue")}
        </button>
      </div>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

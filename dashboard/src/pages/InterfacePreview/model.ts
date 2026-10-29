export type PreviewDocumentStatus = "ready" | "archive_only" | "processing";
export type PreviewSource = "platform" | "folder";
export type PreviewScope = "personal" | "department" | "enterprise";

export interface PreviewDocument {
  id: string;
  baseId: string;
  name: string;
  kind: "PDF" | "DOCX" | "XLSX" | "DWG" | "BIN" | "TXT" | "MD";
  source: PreviewSource;
  status: PreviewDocumentStatus;
  scope: PreviewScope;
  owner: string;
  sizeLabel: string;
  updatedLabel: string;
  fingerprint: string;
  excerpt: string;
}

export interface PreviewRecord {
  id: string;
  code: string;
  name: string;
  department: string;
  responsible: string;
  issuedOn: string;
  cycleMonths: number;
  nextDue: string;
  remainingDays: number;
  documentIds: readonly string[];
}

export interface PreviewAgent {
  id: string;
  name: string;
  description: string;
  kind: "knowledge" | "contract" | "inspection";
  documentIds: readonly string[];
}

export interface PreviewData {
  documents: readonly PreviewDocument[];
  records: readonly PreviewRecord[];
  agents: readonly PreviewAgent[];
}

export const PREVIEW_COPY_KEYS = [
  "overview",
  "knowledge",
  "agents",
  "ledger",
  "reminders",
  "help",
  "preferences",
  "company",
  "member",
  "administrator",
  "sampleUser",
  "workspaceTagline",
  "referenceDate",
  "searchKnowledge",
  "searchRecords",
  "searchAgents",
  "allDocuments",
  "allSources",
  "platformSource",
  "folderSource",
  "sourceFolder",
  "documents",
  "sources",
  "aiHelpers",
  "ledgerRecords",
  "dueSoon",
  "attentionRequired",
  "ready",
  "archived",
  "processing",
  "personalScope",
  "departmentScope",
  "enterpriseScope",
  "readPermission",
  "writePermission",
  "owner",
  "updated",
  "type",
  "status",
  "actions",
  "open",
  "details",
  "preview",
  "viewAll",
  "importDocuments",
  "exportList",
  "back",
  "close",
  "cancel",
  "confirmDemo",
  "saveDemo",
  "recent",
  "recentDocuments",
  "featuredAgents",
  "quickActions",
  "pendingTasks",
  "sourceHealth",
  "sourceConnected",
  "noResults",
  "clearSearch",
  "viewSource",
  "version",
  "linkedDocuments",
  "workbenchTitle",
  "workbenchSubtitle",
  "goodMorning",
  "askQuestion",
  "askPlaceholder",
  "knowledgePanel",
  "contextFiles",
  "addContext",
  "chooseDocument",
  "contextHint",
  "removeContext",
  "expandContext",
  "collapseContext",
  "chatTitle",
  "chatSubtitle",
  "newConversation",
  "conversationHistory",
  "today",
  "conversationWelcome",
  "conversationHint",
  "presetAnswersLabel",
  "demoResponseLabel",
  "composerPlaceholder",
  "sendDemo",
  "draftHint",
  "citations",
  "suggestedQuestions",
  "questionInspection",
  "questionContract",
  "questionSource",
  "answerInspection",
  "answerContract",
  "answerSource",
  "industrialTitle",
  "industrialSubtitle",
  "inspectionLedger",
  "deadlineBoard",
  "operationsOverview",
  "knowledgeCoverage",
  "quickLookup",
  "operationsLabel",
  "maintenanceDocuments",
  "recordName",
  "recordCode",
  "department",
  "nextDue",
  "responsible",
  "daysLeft",
  "days",
  "overdue",
  "dueWithinWeek",
  "normal",
  "recordSource",
  "rowCount",
  "selectedRows",
  "selectAll",
  "selectRecord",
  "filterStatus",
  "sortDue",
  "ascending",
  "descending",
  "filters",
  "reset",
  "scheme",
  "originalScheme",
  "guideOriginal",
  "originalKnowledgeSubtitle",
  "originalKnowledgeChange",
  "originalAgentsSubtitle",
  "originalAgentsChange",
  "originalFeatureChange",
  "originalNavigation",
  "originalCollapseNav",
  "originalExpandNav",
  "originalUnchanged",
  "originalSourceHint",
  "originalFileName",
  "originalFiles",
  "originalSourceManagement",
  "originalViewDocuments",
  "originalPermissionNotes",
  "originalPrivateHint",
  "originalTryUpload",
  "originalArchiveHint",
  "originalPlatformHint",
  "originalFolderHint",
  "originalRecordHint",
  "originalChat",
  "originalOpenLedger",
  "originalOpenFeature",
  "demoOnly",
  "demoSafety",
  "demoShort",
  "guideTitle",
  "guideDescription",
  "schemeSwitch",
  "hideToolbar",
  "showToolbar",
  "resetDemo",
  "uploadDestination",
  "selectFile",
  "fileSelected",
  "noFileSelected",
  "permissionScope",
  "maxPermissionHint",
  "allowDelegation",
  "importLocalOnly",
  "addDemoDocument",
  "uploadDemoDone",
  "exportDemoDone",
  "viewRecord",
  "recordDetail",
  "docPreviewTitle",
  "docPreviewHint",
  "agentPreviewTitle",
  "agentPreviewHint",
  "startDemoConversation",
  "reminderTitle",
  "reminderHint",
  "permissionNote",
  "dateRule",
  "issuedOn",
  "cycleMonths",
  "computedDue",
  "exportFilename",
  "browserLocalOnly",
] as const;

export type PreviewCopyKey = (typeof PREVIEW_COPY_KEYS)[number];
export type PreviewCopy = Record<PreviewCopyKey, string>;

export interface PreviewActions {
  openDocument: (document: PreviewDocument) => void;
  openRecord: (record: PreviewRecord) => void;
  openAgent: (agent: PreviewAgent) => void;
  openImport: (baseId?: string) => void;
  openReminders: () => void;
  exportDocuments: (documents?: readonly PreviewDocument[]) => void;
  openGuide: () => void;
}

export interface PreviewVariantProps {
  data: PreviewData;
  copy: PreviewCopy;
  actions: PreviewActions;
}

import { request, requestBlob, requestUpload } from "../request";

export interface KnowledgeLimits {
  max_docs_per_kb: number;
  max_document_bytes: number;
}

export interface KnowledgeCapability {
  feature_enabled: boolean;
  backend: "onnx" | "remote";
  selected_model: string;
  provider_id: string;
  prerequisites_ok: boolean;
  usable: boolean;
  checks: {
    model_selected: boolean;
    model_downloaded: boolean;
    deps_available: boolean;
    provider_ready: boolean;
  };
  ocr: {
    enabled: boolean;
    backend: "onnx" | "remote";
    model: string;
    provider_id: string;
    prerequisites_ok: boolean;
    usable: boolean;
    checks: {
      deps_available: boolean;
      provider_ready: boolean;
    };
  };
  limits?: KnowledgeLimits;
}

export interface KnowledgeProcessorCapability {
  id: string;
  available: boolean;
  extensions: string[];
  reason?: string | null;
}

export interface KnowledgeProcessors {
  archive_only: boolean;
  processors: KnowledgeProcessorCapability[];
}

export interface KnowledgeBase {
  id: string;
  knowledge_base_id?: string;
  pk?: number;
  /** null for the enterprise space, which is system-owned. */
  owner_user_id: number | null;
  owner_username?: string | null;
  owner_display_name?: string | null;
  name: string;
  description: string;
  default_open: boolean;
  shared: boolean;
  /** The deployment's one logical knowledge base (design §1). */
  is_enterprise: boolean;
  icon_name: string;
  embedding_model: string;
  embedding_dim: number;
  doc_count: number;
  max_documents: number;
  created_at: number;
  updated_at: number;
}

export interface KnowledgeDocument {
  id: string;
  document_id?: string;
  kb_id: string;
  path?: string;
  filename: string;
  is_dir?: boolean;
  content_type: string;
  byte_size: number;
  content_hash: string;
  /**
   * Lifecycle of a knowledge document. The last three are the scan's own
   * states and appear on files inside a data source (design §8.2): a file the
   * scan has seen but not yet settled, a type this build cannot read, and one
   * that is locked (design §6.1).
   */
  status:
    | "pending"
    | "processing"
    | "ready"
    | "failed"
    | "discovered"
    | "unsupported"
    | "archive_only"
    | "password_required";
  error_message: string;
  archive_reason?:
    | "no_processor"
    | "cad_unavailable"
    | "ocr_disabled"
    | "legacy_office_unavailable"
    | null;
  chunk_count: number;
  /**
   * What the document calls itself (design §6.2), from the structure the
   * parser stored. Empty until the file has been indexed.
   */
  title?: string;
  created_at: number;
  updated_at: number;
  /** Folder-backed source provenance; only source-relative paths are returned. */
  data_source_id?: string;
  source_path?: string;
  has_original?: boolean;
}
export interface KnowledgeOnnxModel {
  id: string;
  name: string;
  downloaded: boolean;
  recommended?: boolean;
  size_gb?: number | null;
}

export interface KnowledgeEmbeddingOptions {
  onnx: KnowledgeOnnxModel[];
  remote: {
    provider_id: string;
    provider_name: string;
    models: { id: string; name: string }[];
  }[];
}

export interface KnowledgeOcrOptions {
  local: { id: "rapidocr"; name: string };
  remote: {
    provider_id: string;
    provider_name: string;
    models: { id: string; name: string }[];
  }[];
}

export interface KnowledgeOnnxDownloadState {
  status: "idle" | "downloading" | "loading" | "done" | "failed";
  progress: number;
  error?: string | null;
  model_name: string;
  task_id?: string;
}

export const DEFAULT_KNOWLEDGE_LIMITS: KnowledgeLimits = {
  max_docs_per_kb: 100,
  max_document_bytes: 100 * 1024 * 1024,
};

export interface KnowledgeSearchHit {
  kb_id: string;
  base_name: string;
  document_id: string;
  filename: string;
  /** The document's path in the base — what a citation points at. */
  path: string;
  /** Where the file sits inside its source, when it came from one. */
  source_path: string;
  /** What the document calls itself (design §6.2), else its file name. */
  title: string;
  /** Which chunk of the document matched; the position half of a citation. */
  ordinal: number;
  snippet: string;
  score: number;
  match_kind: "content" | "filename";
  /** Empty for metadata-only file candidates without verified text evidence. */
  segment_id: string;
  locator: Record<string, unknown>;
  version: string;
}

export interface KnowledgeCoverage {
  total: number;
  searchable: number;
  pending: number;
  indexing: number;
  failed: number;
  unsupported: number;
}
export interface KnowledgeCollection {
  id: string;
  kb_id: string;
  target_document_id: string;
  target_path: string;
  target_kind: "file" | "folder";
  name: string;
  prompt: string;
  created_by: number;
  status: "ready" | "partial" | "pending" | "error";
  coverage: {
    total: number;
    read: number;
    generated: number;
    failed: number;
    pending: number;
  };
  generation: {
    state: "idle" | "running" | "ready" | "partial" | "error";
    error: string | null;
    generated_at: number | null;
    model_ref: string | null;
  };
  created_at: number;
  updated_at: number;
}
export interface KnowledgeCollectionDocuments {
  collection: KnowledgeCollection;
  topic_document: string;
  reading_document: string;
  storage_path: string;
}

export interface KnowledgeEvidence {
  document_id: string;
  segment_id: string;
  filename: string;
  text: string;
  locator: Record<string, unknown>;
  version: string;
  verified: boolean;
  reason: "stale" | "source_unavailable" | null;
}
export interface KnowledgeUploadTarget {
  id: string;
  name: string;
  /** Authorized source-relative indexed directories; "" denotes the root. */
  folders: string[];
}

export type KnowledgeUploadAccessScope =
  | "company"
  | "department"
  | "personal"
  | "authorized";

export interface KnowledgeUploadAccess {
  scope: KnowledgeUploadAccessScope;
  permission: "read" | "write";
  unit_key: string | null;
  department_name: string | null;
}

export interface KnowledgeUploadPermissions {
  options: KnowledgeUploadAccess[];
  default: KnowledgeUploadAccess;
}
export const knowledgeBasesApi = {
  getCapability: () =>
    request<KnowledgeCapability>("/knowledge-bases/capability"),
  getProcessors: () =>
    request<KnowledgeProcessors>("/knowledge-bases/processors"),

  setFeature: (body: {
    enabled: boolean;
    backend?: "onnx" | "remote";
    model?: string;
    provider_id?: string;
    ocr_enabled?: boolean;
    ocr_backend?: "onnx" | "remote";
    ocr_model?: string;
    ocr_provider_id?: string;
  }) =>
    request<KnowledgeCapability>("/knowledge-bases/feature", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  list: () => request<KnowledgeBase[]>("/knowledge-bases"),
  getUploadTargets: (id: string) =>
    request<KnowledgeUploadTarget[]>(`/knowledge-bases/${id}/upload-targets`),
  getUploadPermissions: (id: string, folder = "", dataSourceId?: string) => {
    const params = new URLSearchParams({ folder });
    if (dataSourceId) params.set("data_source_id", dataSourceId);
    return request<KnowledgeUploadPermissions>(
      `/knowledge-bases/${id}/upload-permissions?${params.toString()}`,
    );
  },

  getEmbeddingOptions: (opts?: { allOnnx?: boolean }) =>
    request<KnowledgeEmbeddingOptions>(
      opts?.allOnnx
        ? "/knowledge-bases/embedding-options?all_onnx=true"
        : "/knowledge-bases/embedding-options",
    ),

  getOcrOptions: () =>
    request<KnowledgeOcrOptions>("/knowledge-bases/ocr-options"),

  downloadOnnx: (model: string) =>
    request<KnowledgeOnnxDownloadState>("/knowledge-bases/onnx-download", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),

  getOnnxDownloadStatus: () =>
    request<KnowledgeOnnxDownloadState>(
      "/knowledge-bases/onnx-download-status",
    ),

  testOnnx: (model: string) =>
    request<{
      ok: boolean;
      latency_ms?: number | null;
      dim?: number | null;
      error?: string | null;
    }>("/knowledge-bases/onnx-test", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),

  activateOnnx: (model: string) =>
    request<{ enabled: boolean; model: string; ready: boolean }>(
      "/knowledge-bases/onnx-activate",
      {
        method: "POST",
        body: JSON.stringify({ model }),
      },
    ),

  get: (id: string) => request<KnowledgeBase>(`/knowledge-bases/${id}`),

  /**
   * The deployment's one enterprise knowledge space.
   *
   * There is no `create` beside this on purpose: a user creating a knowledge
   * base is the model the design replaced.
   */
  getEnterprise: () => request<KnowledgeBase>("/knowledge-bases/enterprise"),
  listCollections: (id: string) =>
    request<KnowledgeCollection[]>(`/knowledge-bases/${id}/collections`),

  createCollections: (
    id: string,
    documentIds: string[],
    prompt: string,
    name?: string,
  ) =>
    request<KnowledgeCollection[]>(`/knowledge-bases/${id}/collections`, {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds, prompt, name }),
    }),

  updateCollection: (
    id: string,
    collectionId: string,
    body: { prompt?: string; name?: string },
  ) =>
    request<KnowledgeCollection>(
      `/knowledge-bases/${id}/collections/${collectionId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),

  generateCollection: (id: string, collectionId: string) =>
    request<KnowledgeCollection>(
      `/knowledge-bases/${id}/collections/${collectionId}/generate`,
      { method: "POST" },
    ),

  getCollectionDocuments: (id: string, collectionId: string) =>
    request<KnowledgeCollectionDocuments>(
      `/knowledge-bases/${id}/collections/${collectionId}/documents`,
    ),

  deleteCollection: (id: string, collectionId: string) =>
    request<void>(`/knowledge-bases/${id}/collections/${collectionId}`, {
      method: "DELETE",
    }),

  update: (
    id: string,
    body: {
      name?: string;
      description?: string;
      default_open?: boolean;
      shared?: boolean;
      icon_name?: string;
      max_documents?: number;
    },
  ) =>
    request<KnowledgeBase>(`/knowledge-bases/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: (id: string) =>
    request<void>(`/knowledge-bases/${id}`, { method: "DELETE" }),

  listDocuments: (id: string, prefix?: string) =>
    request<KnowledgeDocument[]>(
      prefix
        ? `/knowledge-bases/${id}/documents?prefix=${encodeURIComponent(
            prefix,
          )}`
        : `/knowledge-bases/${id}/documents`,
    ),

  createFolder: (id: string, path: string) =>
    request<KnowledgeDocument>(`/knowledge-bases/${id}/folders`, {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  createTextDocument: (
    id: string,
    body: {
      name: string;
      format: "md" | "txt";
      content?: string;
      path?: string;
    },
  ) =>
    request<KnowledgeDocument>(`/knowledge-bases/${id}/documents/text`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getTextDocument: (id: string, documentId: string) =>
    request<{
      id: string;
      filename: string;
      content_type: string;
      /** The title the parser stored, empty until the file has been indexed. */
      title: string;
      text: string;
    }>(`/knowledge-bases/${id}/documents/${documentId}/content`),

  updateTextDocument: (id: string, documentId: string, content: string) =>
    request<KnowledgeDocument>(
      `/knowledge-bases/${id}/documents/${documentId}/content`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    ),

  uploadDocument: (
    id: string,
    file: File,
    relativePath?: string,
    onProgress?: (percent: number) => void,
    access?: KnowledgeUploadAccess,
  ) => {
    const body = new FormData();
    body.append("upload", file);
    if (relativePath) body.append("path", relativePath);
    if (access) {
      body.append("access_scope", access.scope);
      body.append("access_permission", access.permission);
      if (access.unit_key) body.append("access_unit_key", access.unit_key);
    }
    return requestUpload<KnowledgeDocument>(
      `/knowledge-bases/${id}/documents`,
      body,
      { method: "POST" },
      onProgress,
    );
  },
  uploadSourceDocument: (
    id: string,
    sourceId: string,
    file: File,
    relativePath: string,
    onProgress?: (percent: number) => void,
    access?: KnowledgeUploadAccess,
  ) => {
    const body = new FormData();
    body.append("upload", file);
    body.append("path", relativePath);
    if (access) {
      body.append("access_scope", access.scope);
      body.append("access_permission", access.permission);
      if (access.unit_key) body.append("access_unit_key", access.unit_key);
    }
    return requestUpload<KnowledgeDocument>(
      `/knowledge-bases/${id}/data-sources/${sourceId}/documents`,
      body,
      { method: "POST" },
      onProgress,
    );
  },

  deleteDocument: (id: string, documentId: string) =>
    request<void>(`/knowledge-bases/${id}/documents/${documentId}`, {
      method: "DELETE",
    }),

  reindexDocument: (id: string, documentId: string) =>
    request<KnowledgeDocument>(
      `/knowledge-bases/${id}/documents/${documentId}/reindex`,
      { method: "POST" },
    ),

  /** Keyword and full-text search over what this base may show (design §9). */
  searchDocuments: (id: string, query: string, limit = 20) =>
    request<KnowledgeSearchHit[]>(
      `/knowledge-bases/${id}/search?${new URLSearchParams({
        q: query,
        limit: String(limit),
      }).toString()}`,
    ),
  coverage: (id: string) =>
    request<KnowledgeCoverage>(`/knowledge-bases/${id}/coverage`),

  getEvidence: (id: string, documentId: string, segmentId: string) =>
    request<KnowledgeEvidence>(
      `/knowledge-bases/${id}/documents/${documentId}/segments/${segmentId}`,
    ),

  previewDocument: (id: string, documentId: string) =>
    request<{
      id: string;
      filename: string;
      /** What the document calls itself (design §6.2), else its file name. */
      title: string;
      /** Page count when the format declares one (PDF pages, slides). */
      pages: number | null;
      sections: string[];
      text: string;
      /** Indexed segments with their source locators (empty for raw text). */
      blocks?: {
        ordinal: number;
        text: string;
        kind: string;
        heading: string;
        locator: Record<string, unknown>;
      }[];
    }>(`/knowledge-bases/${id}/documents/${documentId}/preview`),

  /** Authenticated original-file bytes (download or preview). */
  fetchDocumentFile: (
    id: string,
    documentId: string,
    version?: string,
    disposition: "attachment" | "inline" = "attachment",
    onProgress?: (loaded: number, total: number) => void,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({ disposition });
    if (version) params.set("version", version);
    return requestBlob(
      `/knowledge-bases/${id}/documents/${documentId}/file?${params.toString()}`,
      { signal },
      onProgress,
    );
  },

  /** Authenticated rendered PNG for a CAD drawing (DWG/DXF). */
  renderDocument: (id: string, documentId: string, signal?: AbortSignal) =>
    requestBlob(`/knowledge-bases/${id}/documents/${documentId}/render`, {
      signal,
    }),

  reindex: (id: string) =>
    request<{ enqueued: number }>(`/knowledge-bases/${id}/reindex`, {
      method: "POST",
    }),

  renameDocument: (id: string, documentId: string, newName: string) =>
    request<KnowledgeDocument>(
      `/knowledge-bases/${id}/documents/${documentId}/rename`,
      {
        method: "POST",
        body: JSON.stringify({ new_name: newName }),
      },
    ),

  /** Move a file or folder (with its subtree) into `targetFolder`; `""` is the root. */
  moveDocument: (id: string, documentId: string, targetFolder: string) =>
    request<KnowledgeDocument>(
      `/knowledge-bases/${id}/documents/${documentId}/move`,
      {
        method: "POST",
        body: JSON.stringify({ target_folder: targetFolder }),
      },
    ),
};

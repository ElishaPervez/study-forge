export type UnitStatus =
  | "pending"
  | "running"
  | "verifying"
  | "repairing"
  | "ok"
  | "needs-attention"
  | "failed";

// These unit views remain available to the existing card component until the
// later guide viewer work replaces that component.
export interface UnitView {
  unit_id: string;
  label: string;
  page_start: number;
  page_end: number;
  status: UnitStatus | string;
}

export interface JobView {
  job_id: string;
  source_pdf: string;
  page_count: number;
  units: UnitView[];
}

export interface GenerateResult {
  status: UnitStatus | string;
  calls: number;
  requested_refs: string[];
  findings: string[];
  artifact_url: string | null;
}

export type SourceKind = "pdf" | "images";

export interface SourceFileDetail {
  name: string;
  size: number | null;
  exists: boolean;
}

export interface SourceView {
  source_id: string;
  kind: SourceKind;
  display_name: string;
  files: string[];
  page_count: number | null;
  image_count: number | null;
  total_bytes: number;
  created_at: string;
  file_details?: SourceFileDetail[];
}

export interface PdfAllSelection {
  mode: "all";
}

export interface PdfCustomSelection {
  mode: "custom";
  start: number;
  end: number;
}

export interface ImageSelection {
  mode: "images";
}

export type GuideSelection = PdfAllSelection | PdfCustomSelection | ImageSelection;

export type RevisionMode = "clarify" | "custom";

export interface RevisionRequest {
  selected_text: string;
  instruction: string;
  mode: RevisionMode;
}

export interface GuideView {
  kind?: "guide";
  guide_id: string;
  source_id: string;
  selection: GuideSelection;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  findings: string[];
  revision_count: number;
  source?: SourceView | null;
  source_error?: string;
  artifact_url: string | null;
}

export type GuideSummary = GuideView & { source: SourceView | null };

export interface LegacyHistoryEntry {
  kind: "legacy";
  history_id: string;
  job_id: string;
  name: string;
  status: "legacy";
  message: string;
  created_at: string;
  updated_at: string;
}

export type HistoryEntry = GuideSummary | LegacyHistoryEntry;

export interface GuideDeleteResult {
  guide_id: string;
  deleted: boolean;
}

export interface SourceRemovalResult {
  source_id: string;
  deleted: boolean;
  retained?: boolean;
}

export function artifactUrl(base: string, guideId: string, download: boolean): string {
  const origin = base.replace(/\/+$/, "");
  const path = `${origin}/api/guides/${encodeURIComponent(guideId)}/artifact.html`;
  return download ? `${path}?download=1` : path;
}

async function json<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;

  let detail = `request failed (${response.status})`;
  try {
    const body = (await response.json()) as {
      detail?: string | { message?: unknown; findings?: unknown };
    };
    if (typeof body.detail === "string" && body.detail) {
      detail = body.detail;
    } else if (body.detail !== null && typeof body.detail === "object") {
      const message = typeof body.detail.message === "string" ? body.detail.message : "";
      const findings = Array.isArray(body.detail.findings)
        ? body.detail.findings.filter((finding): finding is string => typeof finding === "string")
        : [];
      detail = [message, ...findings].filter(Boolean).join(" ") || detail;
    }
  } catch {
    // Keep the status-based message when the response is not JSON.
  }
  throw new Error(detail);
}

function apiOrigin(base: string): string {
  return base.replace(/\/+$/, "");
}

export function createApi(base: string) {
  const origin = apiOrigin(base);

  return {
    async registerSource(paths: string[]): Promise<SourceView> {
      return json<SourceView>(
        await fetch(`${origin}/api/sources`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paths }),
        }),
      );
    },

    async removeSource(sourceId: string): Promise<SourceRemovalResult | SourceView> {
      return json<SourceRemovalResult | SourceView>(
        await fetch(`${origin}/api/sources/${encodeURIComponent(sourceId)}`, {
          method: "DELETE",
        }),
      );
    },

    async getSource(sourceId: string): Promise<SourceView> {
      return json<SourceView>(
        await fetch(`${origin}/api/sources/${encodeURIComponent(sourceId)}`),
      );
    },

    async createGuide(sourceId: string, selection: GuideSelection): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_id: sourceId, selection }),
        }),
      );
    },

    async getGuide(guideId: string): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}`),
      );
    },

    async listGuides(): Promise<HistoryEntry[]> {
      return json<HistoryEntry[]>(await fetch(`${origin}/api/guides`));
    },

    async generateGuide(guideId: string): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}/generate`, {
          method: "POST",
        }),
      );
    },

    async reviseGuide(guideId: string, request: RevisionRequest): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}/revisions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        }),
      );
    },

    async retryGuide(guideId: string): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}/retry`, {
          method: "POST",
        }),
      );
    },

    async renameGuide(guideId: string, name: string): Promise<GuideView> {
      return json<GuideView>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        }),
      );
    },

    async deleteGuide(guideId: string): Promise<GuideDeleteResult> {
      return json<GuideDeleteResult>(
        await fetch(`${origin}/api/guides/${encodeURIComponent(guideId)}`, {
          method: "DELETE",
        }),
      );
    },

    artifactUrl(guideId: string, download: boolean): string {
      return artifactUrl(base, guideId, download);
    },
  };
}

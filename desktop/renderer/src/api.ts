export type UnitStatus =
  | "pending"
  | "running"
  | "verifying"
  | "repairing"
  | "ok"
  | "needs-attention"
  | "failed";

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

export function artifactUrl(
  base: string,
  jobId: string,
  unitId: string,
  download: boolean,
): string {
  const origin = base.replace(/\/+$/, "");
  const path = `${origin}/api/jobs/${jobId}/units/${unitId}/artifact.html`;
  return download ? `${path}?download=1` : path;
}

async function json<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;

  let detail = `request failed (${response.status})`;
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // Keep the status-based message when the response is not JSON.
  }
  throw new Error(detail);
}

export function createApi(base: string) {
  return {
    async createJob(pdf: string, ranges: [string, number, number][]): Promise<JobView> {
      return json<JobView>(
        await fetch(`${base.replace(/\/+$/, "")}/api/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pdf, ranges }),
        }),
      );
    },

    async getJob(jobId: string): Promise<JobView> {
      return json<JobView>(await fetch(`${base.replace(/\/+$/, "")}/api/jobs/${jobId}`));
    },

    async generateUnit(jobId: string, unitId: string): Promise<GenerateResult> {
      return json<GenerateResult>(
        await fetch(
          `${base.replace(/\/+$/, "")}/api/jobs/${jobId}/units/${unitId}/generate`,
          { method: "POST" },
        ),
      );
    },

    artifactUrl(jobId: string, unitId: string, download: boolean): string {
      return artifactUrl(base, jobId, unitId, download);
    },
  };
}

import type { GenerateResult, UnitView } from "../api";

export interface UnitCardProps {
  unit: UnitView;
  jobId: string;
  artifactUrl: (jobId: string, unitId: string, download: boolean) => string;
  result?: GenerateResult;
  error?: string;
  retrying?: boolean;
  onRetry: () => void;
}

const statusLabels: Record<string, string> = {
  pending: "Waiting",
  running: "Generating",
  verifying: "Checking",
  repairing: "Repairing",
  ok: "Ready",
  "needs-attention": "Needs attention",
  failed: "Could not finish",
};

const statusMessages: Record<string, string> = {
  pending: "This unit is queued for generation.",
  running: "The study artifact is being assembled from these pages.",
  verifying: "The artifact is being checked before it is shown.",
  repairing: "A small issue was found and the artifact is being repaired.",
  ok: "The artifact is ready to read or download.",
  "needs-attention": "The artifact is available, but a check still needs your attention.",
  failed: "This unit did not finish, but the other units can continue.",
};

function statusTone(status: string): string {
  if (status === "ok") return "success";
  if (status === "needs-attention") return "attention";
  if (status === "failed") return "failure";
  if (status === "pending") return "pending";
  return "working";
}

export function hasPublishedArtifact(result: GenerateResult | undefined): boolean {
  return typeof result?.artifact_url === "string" && result.artifact_url.length > 0;
}

export function UnitCard({
  unit,
  jobId,
  artifactUrl,
  result,
  error,
  retrying = false,
  onRetry,
}: UnitCardProps) {
  const status = unit.status;
  const statusLabel = statusLabels[status] ?? status;
  const tone = statusTone(status);
  const hasArtifact = hasPublishedArtifact(result);
  const canRetry = status === "needs-attention" || status === "failed";
  const references = result?.requested_refs ?? [];
  const findings = result?.findings ?? [];

  return (
    <article className={`unit-card tone-${tone}`} aria-labelledby={`unit-${unit.unit_id}-heading`}>
      <header className="unit-card-header">
        <div className="unit-card-index" aria-hidden="true">
          <span>Unit</span>
          <strong>{unit.unit_id.replace(/^unit-/, "").padStart(2, "0")}</strong>
        </div>
        <div className="unit-card-title">
          <h3 id={`unit-${unit.unit_id}-heading`}>{unit.label}</h3>
          <p>{`Pages ${unit.page_start}–${unit.page_end}`}</p>
        </div>
        <div className={`status-pill status-${tone}`} aria-label={`Status: ${statusLabel}`}>
          <span className="status-dot" aria-hidden="true" />
          {statusLabel}
        </div>
      </header>

      <div className="unit-card-content">
        <div className="unit-card-notes">
          <p className="unit-status-message">{statusMessages[status] ?? "This unit has an unknown status."}</p>

          {references.length > 0 ? (
            <div className="unit-detail-block">
              <h4>References used</h4>
              <ul className="reference-list">
                {references.map((reference) => <li key={reference}>{reference}</li>)}
              </ul>
            </div>
          ) : null}

          {status === "needs-attention" ? (
            <div className="finding-block" role="status">
              <h4>What needs a look</h4>
              {findings.length > 0 ? (
                <ul>
                  {findings.map((finding, index) => <li key={`${finding}-${index}`}>{finding}</li>)}
                </ul>
              ) : (
                <p>The checker found an issue in this artifact.</p>
              )}
            </div>
          ) : null}

          {error ? (
            <div className="finding-block failure-block" role="alert">
              <h4>Generation stopped</h4>
              <p>{error}</p>
            </div>
          ) : null}

          {canRetry ? (
            <button type="button" className="secondary-button retry-button" onClick={onRetry} disabled={retrying}>
              {retrying ? "Retrying…" : "Retry this unit"}
            </button>
          ) : null}

          {hasArtifact ? (
            <a
              className="download-button"
              href={artifactUrl(jobId, unit.unit_id, true)}
              download
            >
              Download artifact
            </a>
          ) : null}
        </div>

        <div className="unit-preview">
          <div className="preview-heading">
            <span>Preview</span>
            {hasArtifact ? <span className="preview-note">Same file as download</span> : null}
          </div>
          {hasArtifact ? (
            <iframe
              className="artifact-frame"
              src={artifactUrl(jobId, unit.unit_id, false)}
              title={`Preview of ${unit.label}`}
              loading="lazy"
              referrerPolicy="no-referrer"
              sandbox=""
            />
          ) : (
            <div className="preview-placeholder">
              <span className="placeholder-line placeholder-line-wide" />
              <span className="placeholder-line" />
              <span className="placeholder-line placeholder-line-short" />
              <p>The preview appears when this unit is ready.</p>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

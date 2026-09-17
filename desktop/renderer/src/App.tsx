import { useEffect, useMemo, useRef, useState } from "react";

import { createApi, type GenerateResult, type JobView } from "./api";
import { RangeEditor, type RangeDraft, validateRangeDraft } from "./components/RangeEditor";
import { UnitCard } from "./components/UnitCard";

const initialRows: RangeDraft[] = [
  { id: "range-1", label: "Unit 1", start: "1", end: "1" },
];

type StartupState = "starting" | "ready" | "error";
type WorkState = "picking" | "creating" | "generating" | null;

export function App() {
  const [startupState, setStartupState] = useState<StartupState>("starting");
  const [startupError, setStartupError] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [pdfPath, setPdfPath] = useState<string | null>(null);
  const [rows, setRows] = useState<RangeDraft[]>(initialRows);
  const [job, setJob] = useState<JobView | null>(null);
  const [results, setResults] = useState<Record<string, GenerateResult>>({});
  const [unitErrors, setUnitErrors] = useState<Record<string, string>>({});
  const [setupError, setSetupError] = useState<string | null>(null);
  const [setupErrorTitle, setSetupErrorTitle] = useState("Could not create the lesson");
  const [workState, setWorkState] = useState<WorkState>(null);
  const [retryingUnitId, setRetryingUnitId] = useState<string | null>(null);
  const [activeUnitId, setActiveUnitId] = useState<string | null>(null);
  const [completedUnits, setCompletedUnits] = useState(0);
  const nextRowNumber = useRef(2);

  const api = useMemo(() => (baseUrl === null ? null : createApi(baseUrl)), [baseUrl]);
  const hasJob = job !== null;
  const isBusy = workState !== null || retryingUnitId !== null;
  const selectedPdfName = pdfPath?.split(/[\\/]/).pop() || pdfPath;
  const pageCount = job?.page_count ?? null;
  const draftHasErrors = rows.some((row) => Object.keys(validateRangeDraft(row, pageCount)).length > 0);

  useEffect(() => {
    let active = true;

    const loadPort = async () => {
      try {
        if (!window.lessonGen) {
          throw new Error("The desktop bridge is unavailable.");
        }
        const port = await window.lessonGen.port();
        if (port === null) {
          throw new Error("The local service did not provide a port.");
        }
        if (active) {
          setBaseUrl(`http://127.0.0.1:${port}`);
          setStartupState("ready");
        }
      } catch (error: unknown) {
        if (active) {
          setStartupError(error instanceof Error ? error.message : "The local service could not start.");
          setStartupState("error");
        }
      }
    };

    void loadPort();
    return () => {
      active = false;
    };
  }, []);

  const handleChoosePdf = async () => {
    if (!window.lessonGen || isBusy) return;

    setWorkState("picking");
    setSetupError(null);
    try {
      const pickedPath = await window.lessonGen.pickPdf();
      if (pickedPath !== null) {
        setPdfPath(pickedPath);
        setRows(initialRows);
        nextRowNumber.current = 2;
        setJob(null);
        setResults({});
        setUnitErrors({});
        setCompletedUnits(0);
        setActiveUnitId(null);
      }
    } catch (error: unknown) {
      setSetupErrorTitle("Could not choose a PDF");
      setSetupError(error instanceof Error ? error.message : "The PDF picker could not open.");
    } finally {
      setWorkState(null);
    }
  };

  const handleRangeChange = (
    id: string,
    field: keyof Omit<RangeDraft, "id">,
    value: string,
  ) => {
    setRows((current) => current.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
    setSetupError(null);
  };

  const handleAddRange = () => {
    const rowNumber = nextRowNumber.current;
    nextRowNumber.current += 1;
    setRows((current) => [
      ...current,
      { id: `range-${rowNumber}`, label: `Unit ${rowNumber}`, start: "1", end: "1" },
    ]);
  };

  const handleRemoveRange = (id: string) => {
    setRows((current) => (current.length === 1 ? current : current.filter((row) => row.id !== id)));
  };

  const handleGenerate = async () => {
    if (api === null || pdfPath === null || startupState !== "ready" || isBusy || hasJob) return;

    if (draftHasErrors) {
      setSetupErrorTitle("Check the page ranges");
      setSetupError("Each unit needs a label and a valid page range before it can be generated.");
      return;
    }

    setSetupError(null);
    setWorkState("creating");
    setResults({});
    setUnitErrors({});
    setCompletedUnits(0);
    try {
      const createdJob = await api.createJob(
        pdfPath,
        rows.map((row) => [row.label.trim(), Number(row.start), Number(row.end)]),
      );
      setJob(createdJob);
      setWorkState("generating");

      for (const unit of createdJob.units) {
        setActiveUnitId(unit.unit_id);
        setJob((current) => current === null ? current : {
          ...current,
          units: current.units.map((item) => item.unit_id === unit.unit_id ? { ...item, status: "running" } : item),
        });

        try {
          const result = await api.generateUnit(createdJob.job_id, unit.unit_id);
          setResults((current) => ({ ...current, [unit.unit_id]: result }));
          setJob((current) => current === null ? current : {
            ...current,
            units: current.units.map((item) => item.unit_id === unit.unit_id ? { ...item, status: result.status } : item),
          });
        } catch (error: unknown) {
          const message = error instanceof Error ? error.message : "The unit could not be generated.";
          setUnitErrors((current) => ({ ...current, [unit.unit_id]: message }));
          setJob((current) => current === null ? current : {
            ...current,
            units: current.units.map((item) => item.unit_id === unit.unit_id ? { ...item, status: "failed" } : item),
          });
        }

        setCompletedUnits((current) => current + 1);
      }
    } catch (error: unknown) {
      setSetupErrorTitle("Could not create the lesson");
      setSetupError(error instanceof Error ? error.message : "The local service rejected the lesson.");
    } finally {
      setActiveUnitId(null);
      setWorkState(null);
    }
  };

  const handleRetry = async (unitId: string) => {
    if (api === null || job === null || retryingUnitId !== null || workState !== null) return;

    setRetryingUnitId(unitId);
    setUnitErrors((current) => {
      const next = { ...current };
      delete next[unitId];
      return next;
    });
    setJob((current) => current === null ? current : {
      ...current,
      units: current.units.map((item) => item.unit_id === unitId ? { ...item, status: "running" } : item),
    });

    try {
      const result = await api.generateUnit(job.job_id, unitId);
      setResults((current) => ({ ...current, [unitId]: result }));
      setJob((current) => current === null ? current : {
        ...current,
        units: current.units.map((item) => item.unit_id === unitId ? { ...item, status: result.status } : item),
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "The unit could not be generated.";
      setUnitErrors((current) => ({ ...current, [unitId]: message }));
      setJob((current) => current === null ? current : {
        ...current,
        units: current.units.map((item) => item.unit_id === unitId ? { ...item, status: "failed" } : item),
      });
    } finally {
      setRetryingUnitId(null);
    }
  };

  if (startupState === "starting") {
    return (
      <main className="state-screen" aria-live="polite">
        <div className="state-mark" aria-hidden="true">LG</div>
        <p className="section-kicker">Lesson generator</p>
        <h1>Starting the local workspace…</h1>
        <p>The desktop service is getting ready. Your PDF stays on this computer.</p>
      </main>
    );
  }

  if (startupState === "error") {
    return (
      <main className="state-screen state-screen-error" role="alert">
        <div className="state-mark" aria-hidden="true">!</div>
        <p className="section-kicker">Lesson generator</p>
        <h1>The local workspace is unavailable.</h1>
        <p>{startupError ?? "The desktop service could not start."}</p>
        <p className="state-footnote">Close and reopen the app to try again.</p>
      </main>
    );
  }

  const units = job?.units ?? [];
  const readyCount = units.filter((unit) => unit.status === "ok").length;
  const attentionCount = units.filter((unit) => unit.status === "needs-attention").length;
  const failedCount = units.filter((unit) => unit.status === "failed").length;
  const finishedCount = readyCount + attentionCount + failedCount;
  const activeUnit = units.find((unit) => unit.unit_id === activeUnitId);
  const resultSummary = !job
    ? "Choose a PDF and mark the pages that belong together."
    : workState === "generating"
      ? `Generating ${activeUnit?.label ?? "your units"} · ${completedUnits} of ${units.length} finished`
      : attentionCount > 0 || failedCount > 0
        ? `${readyCount} ready · ${attentionCount} needs attention · ${failedCount} could not finish`
        : finishedCount === units.length && units.length > 0
          ? `${readyCount} ${readyCount === 1 ? "unit" : "units"} ready to study`
          : "Your units are ready to generate.";

  return (
    <div className="app-frame">
      <aside className="setup-rail">
        <div className="rail-brand">
          <div className="brand-mark" aria-hidden="true">LG</div>
          <span>Lesson generator</span>
        </div>

        <div className="rail-intro">
          <p className="section-kicker">From pages to understanding</p>
          <h1>Build a study guide from your source.</h1>
          <p>Choose a PDF, mark the ranges that belong together, and generate one focused artifact per unit.</p>
        </div>

        <form className="setup-form" onSubmit={(event) => { event.preventDefault(); void handleGenerate(); }}>
          <section className="source-section" aria-labelledby="source-heading">
            <div className="section-heading compact-heading">
              <div>
                <p className="section-kicker">Step one</p>
                <h2 id="source-heading">Choose your source</h2>
              </div>
              <span className="step-marker" aria-hidden="true">01</span>
            </div>

            {pdfPath && selectedPdfName ? (
              <div className="selected-file" title={pdfPath}>
                <div className="file-icon" aria-hidden="true">PDF</div>
                <div className="selected-file-copy">
                  <strong>{selectedPdfName}</strong>
                  <span>{job ? `${pageCount} pages · ranges saved` : "Ready for page ranges"}</span>
                </div>
              </div>
            ) : (
              <p className="empty-source">No PDF selected yet. The original file will stay where you keep it.</p>
            )}

            <button type="button" className="secondary-button source-button" onClick={() => void handleChoosePdf()} disabled={isBusy}>
              {workState === "picking" ? "Opening PDF picker…" : pdfPath ? "Choose a different PDF" : "Choose PDF"}
            </button>
          </section>

          <RangeEditor
            rows={rows}
            pageCount={pageCount}
            disabled={!pdfPath || isBusy || hasJob}
            onChange={handleRangeChange}
            onAdd={handleAddRange}
            onRemove={handleRemoveRange}
          />

          {setupError ? (
            <div className="setup-error" role="alert">
              <strong>{setupErrorTitle}</strong>
              <p>{setupError}</p>
              {setupErrorTitle === "Could not create the lesson" ? <span>The server is authoritative about page limits; update the range and try again.</span> : null}
            </div>
          ) : null}

          <div className="setup-action">
            {!hasJob ? (
              <>
                <button
                  type="submit"
                  className="primary-button generate-button"
                  disabled={!pdfPath || draftHasErrors || isBusy}
                  aria-busy={workState === "creating" || workState === "generating"}
                >
                  {workState === "creating" ? "Creating units…" : workState === "generating" ? "Generating…" : "Generate"}
                </button>
                <p className="action-note">
                  {!pdfPath ? "Choose a PDF to begin." : draftHasErrors ? "Fix the highlighted ranges first." : "Each unit runs independently."}
                </p>
              </>
            ) : (
              <div className="job-created-note">
                <strong>{workState === "generating" ? "Generation is in progress." : "This lesson is ready."}</strong>
                <span>{`${units.length} ${units.length === 1 ? "unit" : "units"} · ${pageCount} pages in source`}</span>
              </div>
            )}
          </div>
        </form>

        <p className="rail-footnote">Source files are read in place. Only the generated study artifacts are saved for this lesson.</p>
      </aside>

      <main className="results-area" aria-labelledby="results-heading">
        <header className="results-header">
          <div>
            <p className="section-kicker">Your workspace</p>
            <h2 id="results-heading">Study units</h2>
            <p className="results-summary" aria-live="polite">{resultSummary}</p>
          </div>
          {job ? (
            <div className="results-count" aria-label={`${finishedCount} of ${units.length} units finished`}>
              <strong>{String(finishedCount).padStart(2, "0")}</strong>
              <span>of {String(units.length).padStart(2, "0")} finished</span>
            </div>
          ) : null}
        </header>

        {!job ? (
          <section className="empty-results" aria-labelledby="empty-results-heading">
            <div className="empty-rule" aria-hidden="true"><span /></div>
            <p className="section-kicker">Nothing generated yet</p>
            <h3 id="empty-results-heading">Your artifacts will appear here.</h3>
            <p>When you generate, each named page range gets its own preview and download.</p>
          </section>
        ) : (
          <section className="unit-list" aria-label="Generated study units">
            {units.map((unit) => (
              <UnitCard
                key={unit.unit_id}
                unit={unit}
                jobId={job.job_id}
                artifactUrl={api?.artifactUrl ?? (() => "")}
                result={results[unit.unit_id]}
                error={unitErrors[unit.unit_id]}
                retrying={retryingUnitId === unit.unit_id}
                onRetry={() => void handleRetry(unit.unit_id)}
              />
            ))}
          </section>
        )}
      </main>
    </div>
  );
}

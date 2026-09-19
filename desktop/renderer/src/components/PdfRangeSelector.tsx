import { memo, type ChangeEvent } from "react";

import { CustomDropdown, type DropdownOption } from "./CustomDropdown";

export type PdfSelectionMode = "all" | "custom";

export interface PdfAllSelection {
  mode: "all";
}

export interface PdfCustomSelection {
  mode: "custom";
  start: number;
  end: number;
}

export type PdfSelection = PdfAllSelection | PdfCustomSelection;

export interface PdfRangeSelectorProps {
  pageCount: number | null;
  mode: PdfSelectionMode;
  start: number;
  end: number;
  disabled?: boolean;
  onChange: (selection: PdfSelection) => void;
}

function pageLimit(pageCount: number | null): number | null {
  return pageCount !== null && Number.isSafeInteger(pageCount) && pageCount >= 1
    ? pageCount
    : null;
}

function normalizePage(value: number, fallback: number, pageCount: number | null): number {
  const limit = pageLimit(pageCount);
  const candidate = Number.isFinite(value) ? Math.trunc(value) : fallback;
  const minimum = 1;
  return limit === null
    ? Math.max(minimum, candidate || fallback)
    : Math.min(limit, Math.max(minimum, candidate || fallback));
}

export function clampPdfSelection(selection: PdfSelection, pageCount: number | null): PdfSelection {
  if (selection.mode === "all") return { mode: "all" };

  const start = normalizePage(selection.start ?? 1, 1, pageCount);
  const end = normalizePage(selection.end ?? start, start, pageCount);
  return {
    mode: "custom",
    start,
    end: Math.max(start, end),
  };
}

function emitCustom(
  start: number,
  end: number,
  pageCount: number | null,
  onChange: (selection: PdfSelection) => void,
): void {
  onChange(clampPdfSelection({ mode: "custom", start, end }, pageCount));
}

const PDF_MODE_OPTIONS: readonly DropdownOption<PdfSelectionMode>[] = [
  {
    value: "all",
    label: "Entire document",
    hint: "Process all pages in document",
  },
  {
    value: "custom",
    label: "Custom range",
    hint: "Specify start and end pages",
  },
];

export const PdfRangeSelector = memo(function PdfRangeSelector({
  pageCount,
  mode,
  start,
  end,
  disabled = false,
  onChange,
}: PdfRangeSelectorProps) {
  const handleModeChange = (newMode: PdfSelectionMode) => {
    if (newMode === "all") {
      onChange({ mode: "all" });
      return;
    }

    onChange(clampPdfSelection({ mode: "custom", start, end }, pageCount));
  };

  const handleStartChange = (event: ChangeEvent<HTMLInputElement>) => {
    emitCustom(Number(event.target.value), end, pageCount, onChange);
  };

  const handleEndChange = (event: ChangeEvent<HTMLInputElement>) => {
    emitCustom(start, Number(event.target.value), pageCount, onChange);
  };

  const limit = pageLimit(pageCount);

  return (
    <section className="setup-section pdf-selection" aria-labelledby="pdf-selection-heading">
      <div className="section-heading">
        <div>
          <p className="section-label" id="pdf-selection-heading">PDF pages</p>
        </div>
        {limit !== null ? <span className="section-count">{limit} pages</span> : null}
      </div>

      <label
        className="selection-label"
        id="pdf-selection-mode-label"
        htmlFor="pdf-selection-mode"
      >
        Selection
      </label>
      <CustomDropdown<PdfSelectionMode>
        id="pdf-selection-mode"
        labelId="pdf-selection-mode-label"
        name="pdf-selection-mode"
        value={mode}
        options={PDF_MODE_OPTIONS}
        disabled={disabled}
        onChange={handleModeChange}
      />

      {mode === "custom" ? (
        <div className="pdf-range-fields" aria-label="PDF page range">
          <div className="page-field">
            <label htmlFor="pdf-start">From page</label>
            <input
              id="pdf-start"
              name="pdf-start"
              type="number"
              min={1}
              max={limit ?? undefined}
              value={start}
              onChange={handleStartChange}
              disabled={disabled}
              inputMode="numeric"
            />
          </div>
          <span className="range-divider" aria-hidden="true">to</span>
          <div className="page-field">
            <label htmlFor="pdf-end">Through page</label>
            <input
              id="pdf-end"
              name="pdf-end"
              type="number"
              min={1}
              max={limit ?? undefined}
              value={end}
              onChange={handleEndChange}
              disabled={disabled}
              inputMode="numeric"
            />
          </div>
        </div>
      ) : null}
    </section>
  );
});

import type { ChangeEvent } from "react";

export interface RangeDraft {
  id: string;
  label: string;
  start: string;
  end: string;
}

export interface RangeErrors {
  label?: string;
  start?: string;
  end?: string;
  range?: string;
}

export interface RangeEditorProps {
  rows: RangeDraft[];
  pageCount: number | null;
  disabled?: boolean;
  onChange: (id: string, field: keyof Omit<RangeDraft, "id">, value: string) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
}

const PAGE_ERROR = "Use a whole page number from 1 onward.";

function parsePage(value: string): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;

  const page = Number(trimmed);
  return Number.isSafeInteger(page) && page >= 1 ? page : null;
}

export function validateRangeDraft(
  draft: RangeDraft,
  pageCount: number | null,
): RangeErrors {
  const errors: RangeErrors = {};
  const start = parsePage(draft.start);
  const end = parsePage(draft.end);

  if (!draft.label.trim()) errors.label = "Add a label for this unit.";
  if (start === null) errors.start = PAGE_ERROR;
  if (end === null) errors.end = PAGE_ERROR;

  if (pageCount !== null && Number.isSafeInteger(pageCount) && pageCount >= 1) {
    if (start !== null && start > pageCount) {
      errors.start = `This page is past the ${pageCount}-page PDF.`;
    }
    if (end !== null && end > pageCount) {
      errors.end = `This page is past the ${pageCount}-page PDF.`;
    }
  }

  if (start !== null && end !== null && start > end) {
    errors.range = "Start page must be on or before end page.";
  }

  return errors;
}

function fieldId(rowId: string, field: string): string {
  return `range-${rowId}-${field}`;
}

function fieldErrorId(rowId: string, field: string): string {
  return `${fieldId(rowId, field)}-error`;
}

export function RangeEditor({
  rows,
  pageCount,
  disabled = false,
  onChange,
  onAdd,
  onRemove,
}: RangeEditorProps) {
  return (
    <section className="setup-section" aria-labelledby="ranges-heading">
      <div className="section-heading">
        <div>
          <p className="section-label" id="ranges-heading">Source units</p>
        </div>
        <span className="section-count" aria-label={`${rows.length} unit${rows.length === 1 ? "" : "s"}`}>
          {String(rows.length).padStart(2, "0")}
        </span>
      </div>

      <div className="range-list">
        {rows.map((row, index) => {
          const errors = validateRangeDraft(row, pageCount);
          const labelId = fieldId(row.id, "label");
          const startId = fieldId(row.id, "start");
          const endId = fieldId(row.id, "end");
          const rangeErrorId = fieldErrorId(row.id, "range");

          const update = (field: keyof Omit<RangeDraft, "id">) =>
            (event: ChangeEvent<HTMLInputElement>) => onChange(row.id, field, event.target.value);

          return (
            <fieldset className="range-row" key={row.id} disabled={disabled}>
              <legend className="sr-only">Unit {index + 1}</legend>
              <div className="range-row-number" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </div>

              <div className="range-label-field">
                <label htmlFor={labelId}>Unit label</label>
                <input
                  id={labelId}
                  name={labelId}
                  value={row.label}
                  onChange={update("label")}
                  aria-invalid={Boolean(errors.label)}
                  aria-describedby={errors.label ? `${labelId}-error` : undefined}
                  placeholder="e.g. Cell structure"
                  autoComplete="off"
                />
                {errors.label ? <p id={`${labelId}-error`} className="field-error">{errors.label}</p> : null}
              </div>

              <div className="page-field">
                <label htmlFor={startId}>From</label>
                <input
                  id={startId}
                  name={startId}
                  value={row.start}
                  onChange={update("start")}
                  aria-invalid={Boolean(errors.start)}
                  aria-describedby={errors.start ? `${startId}-error` : undefined}
                  inputMode="numeric"
                  placeholder="1"
                />
                {errors.start ? <p id={`${startId}-error`} className="field-error">{errors.start}</p> : null}
              </div>

              <div className="range-divider" aria-hidden="true">to</div>

              <div className="page-field">
                <label htmlFor={endId}>Through</label>
                <input
                  id={endId}
                  name={endId}
                  value={row.end}
                  onChange={update("end")}
                  aria-invalid={Boolean(errors.end)}
                  aria-describedby={errors.end ? `${endId}-error` : undefined}
                  inputMode="numeric"
                  placeholder="5"
                />
                {errors.end ? <p id={`${endId}-error`} className="field-error">{errors.end}</p> : null}
              </div>

              <button
                type="button"
                className="icon-button"
                onClick={() => onRemove(row.id)}
                aria-label={`Remove ${row.label.trim() || `unit ${index + 1}`}`}
                title="Remove unit"
                disabled={disabled || rows.length === 1}
              >
                <span aria-hidden="true">×</span>
              </button>

              {errors.range ? (
                <p id={rangeErrorId} className="field-error range-error" role="alert">
                  {errors.range}
                </p>
              ) : null}
            </fieldset>
          );
        })}
      </div>

      <button type="button" className="text-button" onClick={onAdd} disabled={disabled}>
        <span aria-hidden="true">+</span> Add unit
      </button>
    </section>
  );
}

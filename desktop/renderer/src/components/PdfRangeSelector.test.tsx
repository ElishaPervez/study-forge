import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  PdfRangeSelector,
  clampPdfSelection,
  type PdfSelection,
} from "./PdfRangeSelector";

describe("PdfRangeSelector", () => {
  it("defaults to the Entire document mode", () => {
    const markup = renderToStaticMarkup(
      <PdfRangeSelector
        pageCount={20}
        mode="all"
        start={1}
        end={20}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("Entire document");
    expect(markup).toContain('selected=""');
    expect(markup).not.toContain('name="pdf-start"');
  });

  it("reveals one inclusive custom range", () => {
    const markup = renderToStaticMarkup(
      <PdfRangeSelector
        pageCount={20}
        mode="custom"
        start={3}
        end={8}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("Custom range");
    expect(markup).toContain('name="pdf-start"');
    expect(markup).toContain('name="pdf-end"');
    expect(markup).toContain('value="3"');
    expect(markup).toContain('value="8"');
  });

  it("clamps custom values to a valid inclusive selection", () => {
    expect(clampPdfSelection({ mode: "custom", start: 0, end: 99 }, 12)).toEqual({
      mode: "custom",
      start: 1,
      end: 12,
    });
    expect(clampPdfSelection({ mode: "custom", start: 10, end: 4 }, 12)).toEqual({
      mode: "custom",
      start: 10,
      end: 10,
    });
  });

  it("emits only all or valid custom selections", () => {
    const onChange = vi.fn<(selection: PdfSelection) => void>();
    const markup = renderToStaticMarkup(
      <PdfRangeSelector
        pageCount={12}
        mode="custom"
        start={2}
        end={4}
        disabled={false}
        onChange={onChange}
      />,
    );

    expect(markup).toContain('aria-label="PDF page range"');
    expect(onChange).not.toHaveBeenCalled();
  });
});

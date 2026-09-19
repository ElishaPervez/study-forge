import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { GuideSelection } from "../api";
import {
  SourceViewer,
  SOURCE_READ_FAILURE_MESSAGE,
  clampViewerSelection,
  selectionForPageEndpoint,
  setFirstPage,
  setLastPage,
  sourceViewerResetKey,
} from "./SourceViewer";
import type { SourceDraft } from "./SourceIntake";

const pdfSource: SourceDraft = {
  kind: "pdf",
  sourceId: "pdf-source",
  displayName: "course.pdf",
  metadata: { totalBytes: 100 },
  imageFiles: [],
  pageCount: 4,
  paths: ["C:/notes/course.pdf"],
  previewBaseUrl: "http://127.0.0.1:53124",
};

const imageSource: SourceDraft = {
  kind: "images",
  sourceId: "image-source",
  displayName: "first.png",
  metadata: { imageCount: 2, totalBytes: 100 },
  imageFiles: [
    { path: "C:/notes/first.png", name: "first.png" },
    { path: "C:/notes/second.webp", name: "second.webp" },
  ],
  pageCount: null,
  paths: ["C:/notes/first.png", "C:/notes/second.webp"],
  previewBaseUrl: "http://127.0.0.1:53124",
};

function renderViewer(source: SourceDraft, selection: GuideSelection): string {
  return renderToStaticMarkup(
    <SourceViewer source={source} selection={selection} onSelectionChange={() => undefined} />,
  );
}

describe("SourceViewer", () => {
  it("uses a direct recovery message when a stored preview cannot be read", () => {
    expect(SOURCE_READ_FAILURE_MESSAGE).toBe(
      "The stored source could not be read. Choose a different source in Source.",
    );
  });
  it("shows every PDF page in order without markers for Entire document", () => {
    const markup = renderViewer(pdfSource, { mode: "all" });

    expect(markup.match(/class="source-page-card/g)).toHaveLength(4);
    expect(markup.indexOf("/pages/1")).toBeLessThan(markup.indexOf("/pages/2"));
    expect(markup.indexOf("/pages/2")).toBeLessThan(markup.indexOf("/pages/4"));
    expect(markup).not.toContain("In range");
  });

  it("marks only the inclusive pages in a custom PDF range", () => {
    const markup = renderViewer(pdfSource, { mode: "custom", start: 2, end: 3 });

    expect(markup.match(/data-in-range="true"/g)).toHaveLength(2);
    expect(markup).toContain('data-page-number="2" data-in-range="true"');
    expect(markup).toContain('data-page-number="3" data-in-range="true"');
    expect(markup).toContain('data-page-number="1" data-in-range="false"');
    expect(markup).toContain('data-page-number="4" data-in-range="false"');
  });

  it("shows ordered original images without PDF page controls", () => {
    const markup = renderViewer(imageSource, { mode: "images" });

    expect(markup.indexOf("/images/1")).toBeLessThan(markup.indexOf("/images/2"));
    expect(markup).toContain("first.png");
    expect(markup).toContain("second.webp");
    expect(markup).not.toContain("/pages/");
    expect(markup).not.toContain("Set as first page");
    expect(markup).not.toContain("Set as last page");
  });

  it("clamps a custom selection to the available PDF pages", () => {
    expect(clampViewerSelection({ mode: "custom", start: 0, end: 99 }, 4)).toEqual({
      mode: "custom",
      start: 1,
      end: 4,
    });
  });

  it("moves the opposite endpoint when setting an inverted first page", () => {
    expect(setFirstPage({ mode: "custom", start: 1, end: 3 }, 4, 4)).toEqual({
      mode: "custom",
      start: 4,
      end: 4,
    });
  });

  it("moves the opposite endpoint when setting an inverted last page", () => {
    expect(setLastPage({ mode: "custom", start: 3, end: 4 }, 1, 4)).toEqual({
      mode: "custom",
      start: 1,
      end: 1,
    });
  });

  it("turns Entire document endpoint actions into a custom range", () => {
    expect(setFirstPage({ mode: "all" }, 3, 4)).toEqual({
      mode: "custom",
      start: 3,
      end: 4,
    });
    expect(setLastPage({ mode: "all" }, 3, 4)).toEqual({
      mode: "custom",
      start: 1,
      end: 3,
    });
  });

  it("applies the right-click endpoint actions through the selection callback path", () => {
    expect(
      selectionForPageEndpoint({ mode: "custom", start: 1, end: 3 }, "first", 4, 4),
    ).toEqual({ mode: "custom", start: 4, end: 4 });
    expect(
      selectionForPageEndpoint({ mode: "custom", start: 3, end: 4 }, "last", 1, 4),
    ).toEqual({ mode: "custom", start: 1, end: 1 });
  });

  it("changes its reset key when the source identity or image order changes", () => {
    expect(sourceViewerResetKey(pdfSource)).not.toBe(
      sourceViewerResetKey({ ...pdfSource, sourceId: "another-pdf-source" }),
    );
    expect(sourceViewerResetKey(imageSource)).not.toBe(
      sourceViewerResetKey({
        ...imageSource,
        paths: [imageSource.paths[1], imageSource.paths[0]],
      }),
    );
  });
});

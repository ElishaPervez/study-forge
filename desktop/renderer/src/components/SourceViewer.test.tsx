import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { GuideSelection } from "../api";
import {
  SourceViewer,
  SOURCE_MENU_EDGE_PADDING,
  SOURCE_READ_FAILURE_MESSAGE,
  clampViewerSelection,
  placeSourceMenu,
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

  it("anchors the page menu to the pointer it was opened at", () => {
    expect(
      placeSourceMenu({ x: 420, y: 310 }, { width: 1200, height: 800 }, { width: 164, height: 70 }),
    ).toEqual({ left: 420, top: 310, width: 164, height: 70 });
  });

  it("keeps a page menu opened near the right edge inside the window", () => {
    const placement = placeSourceMenu(
      { x: 1195, y: 300 },
      { width: 1200, height: 800 },
      { width: 164, height: 70 },
    );

    expect(placement.left).toBe(1200 - 164 - SOURCE_MENU_EDGE_PADDING);
    expect(placement.left + placement.width).toBeLessThanOrEqual(1200);
  });

  it("flips a page menu that would overflow the bottom edge above the pointer", () => {
    const placement = placeSourceMenu(
      { x: 300, y: 780 },
      { width: 1200, height: 800 },
      { width: 164, height: 70 },
    );

    expect(placement).toEqual({ left: 300, top: 710, width: 164, height: 70 });
    expect(placement.top + placement.height).toBeLessThanOrEqual(800);
  });

  it("slides a page menu opened past the bottom edge back into view", () => {
    const placement = placeSourceMenu(
      { x: 300, y: 799 },
      { width: 1200, height: 800 },
      { width: 164, height: 70 },
    );

    expect(placement.top + placement.height).toBeLessThanOrEqual(800 - SOURCE_MENU_EDGE_PADDING);
    expect(placement.left).toBe(300);
  });

  it("shrinks the page menu instead of letting it overflow a small window", () => {
    const placement = placeSourceMenu({ x: 5, y: 5 }, { width: 120, height: 60 });

    expect(placement.width).toBe(120 - SOURCE_MENU_EDGE_PADDING * 2);
    expect(placement.height).toBe(60 - SOURCE_MENU_EDGE_PADDING * 2);
    expect(placement.left).toBe(SOURCE_MENU_EDGE_PADDING);
    expect(placement.left + placement.width).toBeLessThanOrEqual(120);
    expect(placement.top + placement.height).toBeLessThanOrEqual(60);
  });

  it("keeps the page menu closed until a page is right-clicked", () => {
    const markup = renderViewer(pdfSource, { mode: "all" });

    expect(markup).not.toContain("source-context-menu");
    expect(markup).not.toContain("Set as first page");
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

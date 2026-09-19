import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  SourceIntake,
  beginSourceRemoval,
  classifySourcePaths,
  draftFromStoredSource,
  moveImage,
  removeImage,
  visibleSourceError,
  type SourceDraft,
} from "./SourceIntake";
import { ImageGroupEditor } from "./ImageGroupEditor";

const images = [
  { path: "C:/notes/first.png", name: "first.png" },
  { path: "C:/notes/second.png", name: "second.png" },
  { path: "C:/notes/third.png", name: "third.png" },
];

const imageDraft: SourceDraft = {
  kind: "images",
  sourceId: "source-1",
  displayName: "first.png",
  metadata: { imageCount: images.length, totalBytes: 30 },
  imageFiles: images,
  pageCount: null,
  paths: images.map((image) => image.path),
};

describe("source intake", () => {
  it("accepts one PDF as PDF mode", () => {
    expect(classifySourcePaths(["C:/notes/course.pdf"])).toEqual({ kind: "pdf" });
  });

  it("rejects multiple PDFs", () => {
    expect(classifySourcePaths(["C:/notes/one.pdf", "C:/notes/two.pdf"])).toEqual({
      error: "Choose one PDF, or choose one or more images.",
    });
  });

  it("accepts images in the supplied order", () => {
    expect(classifySourcePaths(images.map((image) => image.path))).toEqual({ kind: "images" });
    expect(moveImage(images, 1, -1).map((image) => image.name)).toEqual([
      "second.png",
      "first.png",
      "third.png",
    ]);
  });

  it("removes one image without changing the remaining order", () => {
    expect(removeImage(images, 1).map((image) => image.name)).toEqual(["first.png", "third.png"]);
  });

  it("rejects mixed PDF and image input directly", () => {
    expect(classifySourcePaths(["C:/notes/course.pdf", "C:/notes/page.png"])).toEqual({
      error: "PDF and image files cannot be mixed. Choose one PDF or an image group.",
    });
  });

  it("renders a real Remove action for a selected source", () => {
    const markup = renderToStaticMarkup(
      <SourceIntake
        source={imageDraft}
        onPathsSelected={() => undefined}
        onRemove={() => undefined}
      />,
    );

    expect(markup).toContain(">Remove</button>");
    expect(markup).not.toContain('disabled=""');
  });

  it("keeps a reopened stored source read-only while leaving source replacement available", () => {
    const storedSource = draftFromStoredSource({
      source_id: "stored-source",
      kind: "images",
      display_name: "first.png",
      files: ["first.png", "second.png"],
      page_count: null,
      image_count: 2,
      total_bytes: 30,
      created_at: "2026-09-19T00:00:00Z",
    });
    const markup = renderToStaticMarkup(
      <SourceIntake
        source={storedSource}
        onPathsSelected={() => undefined}
        onRemove={() => undefined}
        onImagesChange={() => undefined}
      />,
    );

    expect(markup).toMatch(/class="source-remove-action"[^>]*disabled=""/);
    expect(markup).toContain('disabled="" aria-label="Move first.png up"');
    expect(markup).toContain(">Choose different source</button>");
    expect(markup).toContain('type="file" multiple=""');
  });

  it("renders image controls without PDF page controls", () => {
    const markup = renderToStaticMarkup(
      <>
        <SourceIntake
          source={imageDraft}
          onPathsSelected={() => undefined}
          onRemove={() => undefined}
        />
        <ImageGroupEditor files={images} onChange={() => undefined} />
      </>,
    );

    expect(markup).toContain("first.png");
    expect(markup).toContain("Move up");
    expect(markup).toContain("Move down");
    expect(markup).toContain("Remove first.png");
    expect(markup).not.toContain("PDF page range");
    expect(markup).not.toContain("Entire document");
  });

  it("locks every source control while a guide request is busy", () => {
    const markup = renderToStaticMarkup(
      <SourceIntake
        source={imageDraft}
        busy
        onPathsSelected={() => undefined}
        onRemove={() => undefined}
        onImagesChange={() => undefined}
      />,
    );
    const buttons = markup.match(/<button[^>]*>/g) ?? [];

    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons.every((button) => button.includes('disabled=""'))).toBe(true);
    expect(markup).toContain('type="file"');
    expect(markup).toContain('type="file" multiple="" accept=".pdf,.png,.jpg,.jpeg,.webp,.gif" disabled=""');
  });

  it("shows a newer local error instead of a stale parent error", () => {
    expect(visibleSourceError("PDF and image files cannot be mixed.", "The source could not be registered.")).toBe(
      "PDF and image files cannot be mixed.",
    );
    expect(visibleSourceError(null, "The source could not be registered.")).toBe(
      "The source could not be registered.",
    );
  });

  it("shows a newer parent error instead of an older local validation error", () => {
    expect(visibleSourceError(
      "PDF and image files cannot be mixed.",
      "The image group could not be updated.",
      "The source was already selected.",
    )).toBe("The image group could not be updated.");
    expect(visibleSourceError(
      "PDF and image files cannot be mixed.",
      "The source was already selected.",
      "The source was already selected.",
    )).toBe("PDF and image files cannot be mixed.");
  });

  it("clears local validation before starting source removal", () => {
    const events: string[] = [];

    beginSourceRemoval(
      () => events.push("clear-local-error"),
      () => events.push("remove-source"),
    );

    expect(events).toEqual(["clear-local-error", "remove-source"]);
  });
});

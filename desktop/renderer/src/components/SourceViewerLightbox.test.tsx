// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  enlargedImagePage,
  enlargedPdfPage,
  isOutsideEnlargedPage,
  SourceViewer,
} from "./SourceViewer";
import type { SourceDraft } from "./SourceIntake";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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

let container: HTMLDivElement;
let root: Root;

async function draw(source: SourceDraft, disabled = false): Promise<void> {
  await act(async () => {
    root.render(
      <SourceViewer
        source={source}
        selection={{ mode: "all" }}
        onSelectionChange={() => undefined}
        disabled={disabled}
      />,
    );
  });
}

function enlargedOverlay(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>(".source-lightbox");
}

function enlargedBackdrop(): HTMLElement {
  const overlay = enlargedOverlay();
  if (overlay === null) throw new Error("the enlarged window is not open");
  return overlay;
}

function inOverlay(selector: string): HTMLElement {
  const match = enlargedOverlay()?.querySelector<HTMLElement>(selector) ?? null;
  if (match === null) throw new Error(`no ${selector} in the enlarged window`);
  return match;
}

async function open(label: string): Promise<void> {
  const control = container.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`);
  if (control === null) throw new Error(`no ${label} control in the source grid`);
  await act(async () => {
    control.click();
  });
}

async function pressOn(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
}

beforeEach(async () => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await draw(pdfSource);
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe("SourceViewer enlarged pages", () => {
  it("offers an enlarge control on every page while nothing is enlarged yet", () => {
    expect(enlargedOverlay()).toBeNull();
    expect(container.querySelectorAll('[aria-label^="Enlarge page "]')).toHaveLength(4);
    expect(document.body.querySelectorAll(".source-lightbox")).toHaveLength(0);
  });

  it("opens the clicked page in its own window over the blurred viewer", async () => {
    await open("Enlarge page 2");

    const overlay = enlargedOverlay();
    expect(overlay).not.toBeNull();
    const frame = inOverlay(".source-lightbox-window");

    expect(frame.getAttribute("role")).toBe("dialog");
    expect(frame.getAttribute("aria-modal")).toBe("true");
    expect(overlay?.textContent).toContain("Page 2");
    expect(inOverlay(".source-lightbox-image").getAttribute("src")).toBe(
      "http://127.0.0.1:53124/api/sources/pdf-source/pages/2",
    );
  });

  it("moves the enlarged window to another page when that page is clicked", async () => {
    await open("Enlarge page 1");
    await open("Enlarge page 4");

    expect(document.body.querySelectorAll(".source-lightbox")).toHaveLength(1);
    expect(inOverlay(".source-lightbox-caption").textContent).toBe("Page 4");
    expect(inOverlay(".source-lightbox-image").getAttribute("src")).toBe(
      "http://127.0.0.1:53124/api/sources/pdf-source/pages/4",
    );
  });

  it("retires the enlarged window when the blurred area outside it is pressed", async () => {
    await open("Enlarge page 2");

    await pressOn(enlargedBackdrop());

    expect(enlargedOverlay()).toBeNull();
  });

  it("keeps the enlarged window open when the window itself is pressed", async () => {
    await open("Enlarge page 2");

    await pressOn(inOverlay(".source-lightbox-image"));

    expect(enlargedOverlay()).not.toBeNull();
  });

  it("closes the enlarged window from its own close control", async () => {
    await open("Enlarge page 2");

    await act(async () => {
      inOverlay(".source-lightbox-close").click();
    });

    expect(enlargedOverlay()).toBeNull();
  });

  it("closes the enlarged window with Escape", async () => {
    await open("Enlarge page 2");

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(enlargedOverlay()).toBeNull();
  });

  it("leaves the enlarged window open for keys that are not Escape", async () => {
    await open("Enlarge page 2");

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "p" }));
    });

    expect(enlargedOverlay()).not.toBeNull();
  });
});

describe("SourceViewer enlarged pages that outlive the grid", () => {
  it("enlarges an ordered image by its own ordinal", async () => {
    await draw(imageSource);

    await open("Enlarge image 2: second.webp");

    expect(inOverlay(".source-lightbox-image").getAttribute("src")).toBe(
      "http://127.0.0.1:53124/api/sources/image-source/images/2",
    );
    expect(inOverlay(".source-lightbox-caption").textContent).toBe("2. second.webp");
  });

  it("still lets a locked source be read while a request is running", async () => {
    await draw(pdfSource, true);

    await open("Enlarge page 3");

    expect(inOverlay(".source-lightbox-image").getAttribute("src")).toBe(
      "http://127.0.0.1:53124/api/sources/pdf-source/pages/3",
    );
  });

  it("treats only a press on the backdrop itself as a press outside", () => {
    const backdrop = document.createElement("div");
    const inside = document.createElement("span");
    backdrop.append(inside);

    expect(isOutsideEnlargedPage(backdrop, backdrop)).toBe(true);
    expect(isOutsideEnlargedPage(backdrop, inside)).toBe(false);
    expect(isOutsideEnlargedPage(backdrop, null)).toBe(false);
    expect(isOutsideEnlargedPage(null, backdrop)).toBe(false);
  });

  it("labels an enlarged page and image the way its card does", () => {
    expect(enlargedPdfPage(3)).toEqual({ ordinal: 3, alt: "Page 3", caption: "Page 3" });
    expect(enlargedImagePage({ name: "second.webp" }, 2)).toEqual({
      ordinal: 2,
      alt: "Image 2: second.webp",
      caption: "2. second.webp",
    });
  });
});

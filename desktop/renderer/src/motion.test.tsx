// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DROP_OVERLAY_EXIT_MS,
  FORGE_EXIT_MS,
  REVISION_EXIT_MS,
  STARTUP_EXIT_MS,
  tabUnderlinePlacement,
} from "./App";
import { ForgingScreen, progressLabel } from "./components/ForgingScreen";
import {
  HistoryContextMenu,
  type HistoryMenuEntry,
} from "./components/HistoryContextMenu";
import { ImageGroupEditor } from "./components/ImageGroupEditor";
import { RevisionPopup } from "./components/RevisionPopup";
import {
  SOURCE_LIGHTBOX_EXIT_MS,
  SOURCE_MENU_EXIT_MS,
} from "./components/SourceViewer";
import { GlobalDropIndicator } from "./useGlobalFileDrop";
import { useLeavingValue } from "./useLeavingValue";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("Motion and Exit Transitions", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  describe("useLeavingValue", () => {
    function TestLeavingComponent({ value, duration }: { value: string | null; duration: number }) {
      const { shown, leaving } = useLeavingValue(value, duration);
      return (
        <div data-testid="probe" data-leaving={leaving ? "true" : "false"}>
          {shown ?? "empty"}
        </div>
      );
    }

    it("keeps the retiring value mounted with leaving flag until the duration elapses", async () => {
      await act(async () => {
        root.render(<TestLeavingComponent value="surface-1" duration={200} />);
      });

      const probe = container.querySelector('[data-testid="probe"]');
      expect(probe?.textContent).toBe("surface-1");
      expect(probe?.getAttribute("data-leaving")).toBe("false");

      // Dismiss the surface
      await act(async () => {
        root.render(<TestLeavingComponent value={null} duration={200} />);
      });

      // While exiting, the value is preserved and leaving is true
      expect(probe?.textContent).toBe("surface-1");
      expect(probe?.getAttribute("data-leaving")).toBe("true");

      // Advance halfway through fade
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
      expect(probe?.textContent).toBe("surface-1");
      expect(probe?.getAttribute("data-leaving")).toBe("true");

      // Advance past the duration
      await act(async () => {
        vi.advanceTimersByTime(100);
      });
      expect(probe?.textContent).toBe("empty");
      expect(probe?.getAttribute("data-leaving")).toBe("false");
    });

    it("cancels retirement if the value reappears before the fade finishes", async () => {
      await act(async () => {
        root.render(<TestLeavingComponent value="surface-1" duration={200} />);
      });

      await act(async () => {
        root.render(<TestLeavingComponent value={null} duration={200} />);
      });
      expect(container.querySelector('[data-testid="probe"]')?.getAttribute("data-leaving")).toBe("true");

      // Reappears mid-fade
      await act(async () => {
        vi.advanceTimersByTime(80);
        root.render(<TestLeavingComponent value="surface-reborn" duration={200} />);
      });

      const probe = container.querySelector('[data-testid="probe"]');
      expect(probe?.textContent).toBe("surface-reborn");
      expect(probe?.getAttribute("data-leaving")).toBe("false");

      // Advance timer further to ensure it wasn't unmounted by the old timer
      await act(async () => {
        vi.advanceTimersByTime(200);
      });
      expect(probe?.textContent).toBe("surface-reborn");
    });
  });

  describe("tabUnderlinePlacement", () => {
    it("computes the relative offset and width against the tab strip", () => {
      const tabRect = { left: 140, width: 85 };
      const stripLeft = 40;
      expect(tabUnderlinePlacement(tabRect, stripLeft)).toEqual({
        left: 100,
        width: 85,
      });
    });
  });

  describe("progressLabel", () => {
    it("extracts the activity wording ahead of counters to prevent jitter", () => {
      expect(progressLabel("Writing the guide · 120 lines · 4,500 characters")).toBe("Writing the guide");
      expect(progressLabel("Writing the guide · no new lines for 5s")).toBe("Writing the guide");
      expect(progressLabel("Waiting to start")).toBe("Waiting to start");
    });
  });

  describe("Leaving markup assertions", () => {
    it("renders ForgingScreen with is-leaving and aria-hidden when retiring", () => {
      const markup = renderToStaticMarkup(<ForgingScreen leaving detail="Writing the guide" />);
      expect(markup).toContain('class="forging-screen is-leaving"');
      expect(markup).toContain('aria-hidden="true"');
    });

    it("renders RevisionPopup with is-leaving and suppressed dialog role when retiring", () => {
      const markup = renderToStaticMarkup(
        <RevisionPopup
          selectedText="Sample text"
          anchorRect={{ left: 100, top: 100, width: 50, height: 20 }}
          leaving
          onClarify={() => undefined}
          onUpdate={() => undefined}
          onClose={() => undefined}
        />,
      );
      expect(markup).toContain('class="revision-popup is-leaving"');
      expect(markup).not.toContain('role="dialog"');
      expect(markup).toContain('aria-hidden="true"');
    });

    it("renders HistoryContextMenu with is-leaving and suppressed role when retiring", () => {
      const entries: HistoryMenuEntry[] = [{ action: "delete", label: "Delete" }];
      const markup = renderToStaticMarkup(
        <HistoryContextMenu
          guideName="Test Guide"
          entries={entries}
          anchor={{ x: 100, y: 100 }}
          viewport={{ left: 0, top: 0, width: 800, height: 600, scrollTop: 0 }}
          leaving
          onSelect={() => undefined}
          onClose={() => undefined}
        />,
      );
      expect(markup).toContain('class="history-menu is-leaving"');
      expect(markup).not.toContain('role="menu"');
      expect(markup).toContain('aria-hidden="true"');
    });

    it("renders GlobalDropIndicator with is-leaving when retiring", () => {
      const markup = renderToStaticMarkup(<GlobalDropIndicator leaving />);
      expect(markup).toContain('class="global-drop-indicator is-leaving"');
    });
  });

  describe("ImageGroupEditor row swap animation", () => {
    it("applies data-image-move attribute when reordering rows", async () => {
      const files = [
        { path: "img1.png", name: "img1.png" },
        { path: "img2.png", name: "img2.png" },
      ];
      let currentFiles = [...files];

      await act(async () => {
        root.render(
          <ImageGroupEditor
            files={currentFiles}
            onChange={(next) => {
              currentFiles = next;
            }}
          />,
        );
      });

      const moveDownButtons = container.querySelectorAll('button[title="Move down"]');
      expect(moveDownButtons).toHaveLength(2);

      // Move first image down
      await act(async () => {
        (moveDownButtons[0] as HTMLButtonElement).click();
      });

      const rows = container.querySelectorAll(".image-row");
      expect(rows[0].getAttribute("data-image-move")).toBe("down");
      expect(rows[1].getAttribute("data-image-move")).toBe("up");

      // After timeout, the animation attributes are cleared
      await act(async () => {
        vi.advanceTimersByTime(250);
      });

      expect(rows[0].getAttribute("data-image-move")).toBeNull();
      expect(rows[1].getAttribute("data-image-move")).toBeNull();
    });
  });

  describe("Exit durations align with CSS specs", () => {
    it("verifies exit durations are positive constants under 250ms", () => {
      expect(FORGE_EXIT_MS).toBe(220);
      expect(REVISION_EXIT_MS).toBe(160);
      expect(STARTUP_EXIT_MS).toBe(240);
      expect(DROP_OVERLAY_EXIT_MS).toBe(160);
      expect(SOURCE_MENU_EXIT_MS).toBe(160);
      expect(SOURCE_LIGHTBOX_EXIT_MS).toBe(180);
    });
  });
});

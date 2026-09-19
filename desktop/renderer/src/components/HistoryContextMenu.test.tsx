import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  HistoryContextMenu,
  historyMenuEntries,
  menuViewportFromElement,
  positionHistoryMenu,
  resolveMenuContainer,
  type MenuViewport,
} from "./HistoryContextMenu";

const viewport: MenuViewport = { left: 20, top: 48, width: 280, height: 600, scrollTop: 0 };
const size = { width: 176, height: 118 };

function fakeElement(overrides: Partial<HTMLElement> = {}): HTMLElement {
  return overrides as HTMLElement;
}

describe("historyMenuEntries", () => {
  it("offers rename and delete for a ready guide, which opens on a plain click", () => {
    const entries = historyMenuEntries("ok");

    expect(entries.map((entry) => entry.label)).toEqual(["Rename", "Delete"]);
    expect(entries[1].tone).toBe("danger");
  });

  it.each(["failed", "needs-attention"])("offers retry and delete for a %s guide", (status) => {
    expect(historyMenuEntries(status).map((entry) => entry.label)).toEqual(["Retry", "Delete"]);
  });

  it.each(["running", "pending", "verifying", "legacy"])(
    "offers nothing while a guide is %s",
    (status) => {
      expect(historyMenuEntries(status)).toEqual([]);
    },
  );
});

describe("positionHistoryMenu", () => {
  it("anchors the menu below the pointer, inside the rail", () => {
    expect(positionHistoryMenu({ x: 60, y: 100 }, viewport, size)).toEqual({
      left: 40,
      top: 55,
      width: 176,
      height: 118,
    });
  });

  it("flips the menu above the anchor when the rail runs out of room below", () => {
    expect(positionHistoryMenu({ x: 60, y: 638 }, viewport, size)).toEqual({
      left: 40,
      top: 469,
      width: 176,
      height: 118,
    });
  });

  it("keeps the menu inside the rail instead of overflowing either edge", () => {
    expect(positionHistoryMenu({ x: 275, y: 100 }, viewport, size).left).toBe(96);
    expect(positionHistoryMenu({ x: 5, y: 100 }, viewport, size).left).toBe(8);
  });

  it("clamps a menu taller than the visible rail into the visible area", () => {
    const placement = positionHistoryMenu({ x: 60, y: 100 }, viewport, {
      width: 176,
      height: 600,
    });

    expect(placement.height).toBe(584);
    expect(placement.top).toBe(8);
  });

  it("positions against the scrolled rail rather than the window", () => {
    const scrolled: MenuViewport = { ...viewport, scrollTop: 400 };
    const placement = positionHistoryMenu({ x: 60, y: 100 }, scrolled, size);

    expect(placement.top).toBe(455);
    expect(placement.top).toBeGreaterThan(scrolled.scrollTop);
  });
});

describe("menu surface helpers", () => {
  it("reads a viewport from the scrolling rail element", () => {
    const element = fakeElement({
      scrollTop: 120,
      getBoundingClientRect: () =>
        ({ left: 10, top: 20, width: 280, height: 500 }) as DOMRect,
    });

    expect(menuViewportFromElement(element)).toEqual({
      left: 10,
      top: 20,
      width: 280,
      height: 500,
      scrollTop: 120,
    });
  });

  it("prefers the provided scroll container and falls back to the positioned ancestor", () => {
    const provided = fakeElement();
    const offsetParent = fakeElement();
    const section = fakeElement({ offsetParent: offsetParent as Element });

    expect(resolveMenuContainer(provided, section)).toBe(provided);
    expect(resolveMenuContainer(null, section)).toBe(offsetParent);
    expect(resolveMenuContainer(undefined, null)).toBeNull();
  });
});

describe("HistoryContextMenu", () => {
  it("renders a labelled menu with one item per entry and a danger tone", () => {
    const markup = renderToStaticMarkup(
      <HistoryContextMenu
        guideName="Wave optics"
        entries={historyMenuEntries("ok")}
        anchor={{ x: 40, y: 120 }}
        viewport={viewport}
        onSelect={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain('role="menu"');
    expect(markup).toContain('aria-label="Guide options for Wave optics"');
    expect(markup.match(/role="menuitem"/g)).toHaveLength(2);
    expect(markup).not.toContain(">Open</button>");
    expect(markup).toContain(">Rename</button>");
    expect(markup).toContain("history-menu-item is-danger");
    expect(markup).toContain("left:20px");
    expect(markup).toContain("top:75px");
  });

  it("never renders an action a failed guide cannot run", () => {
    const markup = renderToStaticMarkup(
      <HistoryContextMenu
        guideName="Broken guide"
        entries={historyMenuEntries("failed")}
        anchor={{ x: 40, y: 120 }}
        viewport={viewport}
        onSelect={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain(">Retry</button>");
    expect(markup).toContain(">Delete</button>");
    expect(markup).not.toContain(">Open</button>");
    expect(markup).not.toContain(">Rename</button>");
  });
});

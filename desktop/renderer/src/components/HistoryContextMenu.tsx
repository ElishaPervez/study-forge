import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export type HistoryMenuAction = "open" | "rename" | "delete" | "retry";

export interface HistoryMenuEntry {
  action: HistoryMenuAction;
  label: string;
  tone?: "default" | "danger";
}

export interface MenuAnchor {
  x: number;
  y: number;
}

export interface MenuViewport {
  left: number;
  top: number;
  width: number;
  height: number;
  scrollTop: number;
}

export interface MenuSize {
  width: number;
  height: number;
}

export interface MenuPlacement extends MenuSize {
  left: number;
  top: number;
}

export const MENU_EDGE_PADDING = 8;
export const MENU_ANCHOR_GAP = 3;

const UNMEASURED_MENU_SIZE: MenuSize = { width: 176, height: 118 };

/**
 * The whole guide-maintenance vocabulary lives here, so the rail can keep its
 * cards clean and still expose every action through the context menu.
 */
export function historyMenuEntries(status: string): HistoryMenuEntry[] {
  if (status === "failed" || status === "needs-attention") {
    return [
      { action: "retry", label: "Retry" },
      { action: "delete", label: "Delete", tone: "danger" },
    ];
  }
  if (status === "ok") {
    return [
      { action: "open", label: "Open" },
      { action: "rename", label: "Rename" },
      { action: "delete", label: "Delete", tone: "danger" },
    ];
  }
  return [];
}

export function menuViewportFromElement(element: HTMLElement): MenuViewport {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    scrollTop: element.scrollTop,
  };
}

export function resolveMenuContainer(
  scrollContainer: HTMLElement | null | undefined,
  section: HTMLElement | null,
): HTMLElement | null {
  if (scrollContainer !== null && scrollContainer !== undefined) return scrollContainer;
  const positioned = section?.offsetParent as HTMLElement | null | undefined;
  return positioned ?? section;
}

/**
 * Anchors the menu to a pointer or item position inside the scrolling rail,
 * preferring below the anchor and flipping above when the rail runs out.
 */
export function positionHistoryMenu(
  anchor: MenuAnchor,
  viewport: MenuViewport,
  size: MenuSize = UNMEASURED_MENU_SIZE,
): MenuPlacement {
  const width = Math.min(size.width, Math.max(0, viewport.width - MENU_EDGE_PADDING * 2));
  const height = Math.min(size.height, Math.max(0, viewport.height - MENU_EDGE_PADDING * 2));

  const minLeft = MENU_EDGE_PADDING;
  const maxLeft = Math.max(minLeft, viewport.width - width - MENU_EDGE_PADDING);
  const left = Math.min(Math.max(anchor.x - viewport.left, minLeft), maxLeft);

  const minTop = viewport.scrollTop + MENU_EDGE_PADDING;
  const visibleBottom = viewport.scrollTop + viewport.height - MENU_EDGE_PADDING;
  const maxTop = Math.max(minTop, visibleBottom - height);
  const anchorTop = anchor.y - viewport.top + viewport.scrollTop;
  const below = anchorTop + MENU_ANCHOR_GAP;
  const above = anchorTop - MENU_ANCHOR_GAP - height;

  if (below + height <= visibleBottom) return { left, top: below, width, height };
  if (above >= minTop) return { left, top: above, width, height };
  return { left, top: Math.min(Math.max(below, minTop), maxTop), width, height };
}

export interface HistoryContextMenuProps {
  guideName: string;
  entries: HistoryMenuEntry[];
  anchor: MenuAnchor;
  viewport: MenuViewport;
  onSelect: (action: HistoryMenuAction) => void;
  onClose: () => void;
}

export function HistoryContextMenu({
  guideName,
  entries,
  anchor,
  viewport,
  onSelect,
  onClose,
}: HistoryContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [measured, setMeasured] = useState<MenuSize | null>(null);
  const placement = positionHistoryMenu(anchor, viewport, measured ?? UNMEASURED_MENU_SIZE);

  useEffect(() => {
    const menu = menuRef.current;
    if (menu === null) return;
    const rendered = menu.getBoundingClientRect();
    if (rendered.width <= 0 || rendered.height <= 0) return;
    setMeasured({ width: rendered.width, height: rendered.height });
  }, [guideName, entries.length]);

  useEffect(() => {
    itemRefs.current[0]?.focus();
  }, [guideName]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    const handleViewportChange = () => onClose();
    document.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
    };
  }, [onClose]);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
    if (items.length === 0) return;

    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Tab") {
      onClose();
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      items[event.key === "Home" ? 0 : items.length - 1].focus();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const activeIndex = items.findIndex((item) => item === document.activeElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(activeIndex + step + items.length) % items.length].focus();
    }
  };

  return (
    <div
      ref={menuRef}
      className="history-menu"
      role="menu"
      aria-label={`Guide options for ${guideName}`}
      style={{ left: `${placement.left}px`, top: `${placement.top}px` }}
      onKeyDown={handleKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      {entries.map((entry, index) => (
        <button
          key={entry.action}
          ref={(element) => {
            itemRefs.current[index] = element;
          }}
          type="button"
          role="menuitem"
          className={`history-menu-item${entry.tone === "danger" ? " is-danger" : ""}`}
          onClick={() => onSelect(entry.action)}
        >
          {entry.label}
        </button>
      ))}
    </div>
  );
}

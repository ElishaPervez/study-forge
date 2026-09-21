import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
} from "react";

import type { GuideSummary, HistoryEntry } from "../api";
import { useLeavingValue } from "../useLeavingValue";
import {
  HistoryContextMenu,
  historyMenuEntries,
  menuViewportFromElement,
  resolveMenuContainer,
  type HistoryMenuAction,
  type MenuAnchor,
  type MenuViewport,
} from "./HistoryContextMenu";

export const HISTORY_ITEM_EXIT_MS = 180;
export const HISTORY_STATUS_FLASH_MS = 700;
export const HISTORY_MENU_EXIT_MS = 160;

export interface HistoryListProps {
  guides: HistoryEntry[];
  activeGuideId?: string | null;
  disabled?: boolean;
  /** Guides with a request waiting or running: readable, but not changeable. */
  busyGuideIds?: ReadonlySet<string>;
  onOpen: (guide: GuideSummary) => void | Promise<void>;
  onRename: (guideId: string, name: string) => void | Promise<void>;
  onDelete: (guideId: string) => void | Promise<void>;
  onRetry: (guideId: string) => void | Promise<void>;
  scrollContainerRef?: RefObject<HTMLElement | null>;
}

export function sortHistoryNewestFirst(guides: HistoryEntry[]): HistoryEntry[] {
  return [...guides].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function resolveSubmittedGuideName(previousName: string, submittedName: string): string {
  const nextName = submittedName.trim();
  return nextName.length > 0 ? nextName : previousName;
}

/**
 * A plain left click on a modern guide opens it, so the card itself is the
 * control. A guide with a request waiting or running opens too: its saved state
 * is readable while the request finishes, and its card has no change menu. The
 * older incompatible records and a busy rail open nothing at all.
 */
export function historyItemOpensGuide(status: string, disabled: boolean, editing: boolean): boolean {
  if (disabled || editing) return false;
  return status === "ok"
    || status === "failed"
    || status === "needs-attention"
    || status === "pending"
    || status === "running"
    || status === "verifying"
    || status === "repairing";
}

function selectionLabel(guide: GuideSummary): string {
  if (guide.source === null) return "Source unavailable";
  if (guide.selection.mode === "images") {
    const count = guide.source.image_count ?? guide.source.files.length;
    return `${count} ${count === 1 ? "image" : "images"}`;
  }
  if (guide.selection.mode === "custom") {
    return `Pages ${guide.selection.start}–${guide.selection.end}`;
  }
  return "Entire document";
}

function statusLabel(status: string): string {
  if (status === "ok") return "Ready";
  if (status === "failed") return "Failed";
  if (status === "needs-attention") return "Needs attention";
  if (status === "running") return "Generating";
  if (status === "pending") return "Queued";
  return status;
}

function isFailed(status: string): boolean {
  return status === "failed" || status === "needs-attention";
}

function statusTone(status: string): string {
  if (status === "ok") return "success";
  if (isFailed(status)) return "failure";
  if (status === "pending") return "pending";
  return "working";
}

export const HistoryList = memo(function HistoryList({
  guides,
  activeGuideId = null,
  disabled = false,
  busyGuideIds,
  onOpen,
  onRename,
  onDelete,
  onRetry,
  scrollContainerRef,
}: HistoryListProps) {
  const [editingGuideId, setEditingGuideId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [menu, setMenu] = useState<{
    guide: GuideSummary;
    anchor: MenuAnchor;
    viewport: MenuViewport;
  } | null>(null);
  const menuSurface = useLeavingValue(menu, HISTORY_MENU_EXIT_MS);
  const [leavingGuideIds, setLeavingGuideIds] = useState<Set<string>>(new Set());
  const [statusChangedIds, setStatusChangedIds] = useState<Set<string>>(new Set());
  const previousStatusesRef = useRef<Map<string, string>>(new Map());
  const sectionRef = useRef<HTMLElement | null>(null);
  const menuTriggerRef = useRef<HTMLElement | null>(null);
  const orderedGuides = sortHistoryNewestFirst(guides);

  useEffect(() => {
    const prevMap = previousStatusesRef.current;
    const changed = new Set<string>();
    for (const entry of orderedGuides) {
      if (entry.kind === "legacy") continue;
      const prevStatus = prevMap.get(entry.guide_id);
      if (prevStatus !== undefined && prevStatus !== entry.status) {
        changed.add(entry.guide_id);
      }
      prevMap.set(entry.guide_id, entry.status);
    }
    if (changed.size > 0) {
      setStatusChangedIds((current) => new Set([...current, ...changed]));
      const timer = setTimeout(() => {
        setStatusChangedIds((current) => {
          const next = new Set(current);
          changed.forEach((id) => next.delete(id));
          return next;
        });
      }, HISTORY_STATUS_FLASH_MS);
      return () => clearTimeout(timer);
    }
  }, [orderedGuides]);

  const beginRename = (guide: GuideSummary) => {
    setEditingGuideId(guide.guide_id);
    setDraftName(guide.name);
  };

  const cancelRename = () => {
    setEditingGuideId(null);
    setDraftName("");
  };

  const finishRename = (guide: GuideSummary) => {
    if (editingGuideId !== guide.guide_id) return;
    const nextName = resolveSubmittedGuideName(guide.name, draftName);
    cancelRename();
    if (nextName !== guide.name) {
      void onRename(guide.guide_id, nextName);
    }
  };

  const handleRenameKey = (event: KeyboardEvent<HTMLInputElement>, guide: GuideSummary) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finishRename(guide);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  };

  const closeMenu = useCallback(() => {
    const trigger = menuTriggerRef.current;
    menuTriggerRef.current = null;
    setMenu(null);
    trigger?.focus();
  }, []);

  const openMenu = useCallback(
    (guide: GuideSummary, anchor: MenuAnchor, trigger: HTMLElement) => {
      const container = resolveMenuContainer(scrollContainerRef?.current, sectionRef.current);
      if (container === null) return;
      menuTriggerRef.current = trigger;
      setMenu({ guide, anchor, viewport: menuViewportFromElement(container) });
    },
    [scrollContainerRef],
  );

  const handleItemContextMenu = (
    event: MouseEvent<HTMLLIElement>,
    guide: GuideSummary,
    canOpenMenu: boolean,
  ) => {
    if (!canOpenMenu) return;
    event.preventDefault();
    openMenu(guide, { x: event.clientX, y: event.clientY }, event.currentTarget);
  };

  const handleItemClick = (guide: GuideSummary, canOpen: boolean) => {
    if (!canOpen) return;
    void onOpen(guide);
  };

  const handleItemKeyDown = (
    event: KeyboardEvent<HTMLLIElement>,
    guide: GuideSummary,
    canOpenMenu: boolean,
    canOpen: boolean,
  ) => {
    if (canOpen && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      void onOpen(guide);
      return;
    }
    if (!canOpenMenu) return;
    if (event.key !== "ContextMenu" && !(event.key === "F10" && event.shiftKey)) return;
    event.preventDefault();
    const itemRect = event.currentTarget.getBoundingClientRect();
    openMenu(guide, { x: itemRect.left + 14, y: itemRect.top + 12 }, event.currentTarget);
  };

  const handleMenuSelect = (action: HistoryMenuAction) => {
    const opened = menu;
    closeMenu();
    if (opened === null) return;
    if (action === "rename") {
      beginRename(opened.guide);
    } else if (action === "delete") {
      const guideId = opened.guide.guide_id;
      setLeavingGuideIds((current) => new Set(current).add(guideId));
      setTimeout(() => {
        void onDelete(guideId);
        setLeavingGuideIds((current) => {
          const next = new Set(current);
          next.delete(guideId);
          return next;
        });
      }, HISTORY_ITEM_EXIT_MS);
    } else {
      void onRetry(opened.guide.guide_id);
    }
  };

  // A guide that becomes busy loses its change menu and any rename in progress,
  // so a stale action cannot be chosen after the queue moved on.
  useEffect(() => {
    const busy = (guideId: string) => busyGuideIds?.has(guideId) ?? false;
    if (editingGuideId !== null && busy(editingGuideId)) cancelRename();
    if (menu !== null && (disabled || busy(menu.guide.guide_id))) setMenu(null);
  }, [disabled, busyGuideIds, editingGuideId, menu]);

  return (
    <section className="history-section" ref={sectionRef} aria-labelledby="history-heading">
      <div className="section-heading">
        <p className="section-label" id="history-heading">History</p>
        {orderedGuides.length > 0 ? <span className="section-count">{orderedGuides.length}</span> : null}
      </div>

      {orderedGuides.length === 0 ? (
        <p className="history-empty">Saved guides will appear here.</p>
      ) : (
        <ol className="history-list">
          {orderedGuides.map((guide) => {
            if (guide.kind === "legacy") {
              return (
                <li className="history-item history-item-legacy" key={guide.history_id}>
                  <div className="history-item-main">
                    <strong className="history-item-name">{guide.name}</strong>
                    <span className="history-item-source">Older Study Forge data</span>
                  </div>
                  <p className="history-item-notice" role="note">{guide.message}</p>
                </li>
              );
            }

            const editing = editingGuideId === guide.guide_id;
            const busy = busyGuideIds?.has(guide.guide_id) ?? false;
            const menuEntries = editing || disabled || busy
              ? []
              : historyMenuEntries(guide.status);
            const canOpenMenu = menuEntries.length > 0;
            const menuOpen = menu?.guide.guide_id === guide.guide_id;
            const canOpen = historyItemOpensGuide(guide.status, disabled, editing);
            const focusable = canOpen || canOpenMenu;
            const isLeaving = leavingGuideIds.has(guide.guide_id);
            const isStatusChanged = statusChangedIds.has(guide.guide_id);
            return (
              <li
                className={`history-item${isLeaving ? " is-leaving" : ""}${isStatusChanged ? " is-status-changed" : ""}${canOpen ? " is-openable" : ""}${activeGuideId === guide.guide_id ? " is-active" : ""}${menuOpen ? " is-menu-open" : ""}${busy ? " is-busy" : ""}`}
                key={guide.guide_id}
                data-guide-id={guide.guide_id}
                tabIndex={isLeaving ? undefined : (focusable ? 0 : undefined)}
                aria-haspopup={isLeaving ? undefined : (canOpenMenu ? "menu" : undefined)}
                aria-expanded={isLeaving ? undefined : (canOpenMenu ? menuOpen : undefined)}
                aria-keyshortcuts={isLeaving ? undefined : (canOpenMenu ? "Shift+F10" : undefined)}
                onClick={() => !isLeaving && handleItemClick(guide, canOpen)}
                onContextMenu={(event) => !isLeaving && handleItemContextMenu(event, guide, canOpenMenu)}
                onKeyDown={(event) => !isLeaving && handleItemKeyDown(event, guide, canOpenMenu, canOpen)}
              >
                <div className="history-item-main">
                  {editing ? (
                    <input
                      className="history-name-input"
                      value={draftName}
                      onChange={(event) => setDraftName(event.target.value)}
                      onBlur={() => finishRename(guide)}
                      onKeyDown={(event) => handleRenameKey(event, guide)}
                      aria-label={`Rename ${guide.name}`}
                      autoFocus
                    />
                  ) : (
                    <strong className="history-item-name">{guide.name}</strong>
                  )}
                  <span className="history-item-source">
                    {guide.source?.display_name ?? "Source unavailable"}
                  </span>
                  <span className="history-item-selection">{selectionLabel(guide)}</span>
                </div>

                <div className={`history-status status-${statusTone(guide.status)}`}>
                  <span className="status-dot" aria-hidden="true" />
                  <span className="history-status-text" key={busy ? "waiting" : guide.status}>
                    {busy ? "Waiting in the queue" : statusLabel(guide.status)}
                  </span>
                </div>

                {guide.error ? <p className="history-item-error">{guide.error}</p> : null}
                {guide.source_error ? (
                  <p className="history-item-error" role="alert">{guide.source_error}</p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}

      {menuSurface.shown !== null ? (
        <HistoryContextMenu
          guideName={menuSurface.shown.guide.name}
          entries={historyMenuEntries(menuSurface.shown.guide.status)}
          anchor={menuSurface.shown.anchor}
          viewport={menuSurface.shown.viewport}
          leaving={menuSurface.leaving}
          onSelect={handleMenuSelect}
          onClose={closeMenu}
        />
      ) : null}
    </section>
  );
});

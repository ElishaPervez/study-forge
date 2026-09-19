/**
 * In-app drags are not source submissions. Dragging a page render or an
 * uploaded image thumbnail reports "Files" on the drag data, but the payload
 * carries no file path, so the intake path can only reject it.
 *
 * `dragstart` only fires in the document a drag starts in, so a drag that never
 * raised `dragstart` here came from outside the window (the file manager) and is
 * a real file drop. `dragend` fires in the same document however the drag ends,
 * including a drop outside the window.
 */
let inAppDragActive = false;

export function inAppDragInProgress(): boolean {
  return inAppDragActive;
}

type DragEventTarget = Pick<EventTarget, "addEventListener" | "removeEventListener">;

export function trackInAppDrags(target: DragEventTarget = window): () => void {
  const handleDragStart = () => {
    inAppDragActive = true;
  };
  const handleDragEnd = () => {
    inAppDragActive = false;
  };

  target.addEventListener("dragstart", handleDragStart, true);
  target.addEventListener("dragend", handleDragEnd, true);
  return () => {
    inAppDragActive = false;
    target.removeEventListener("dragstart", handleDragStart, true);
    target.removeEventListener("dragend", handleDragEnd, true);
  };
}

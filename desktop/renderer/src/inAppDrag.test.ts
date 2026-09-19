import { describe, expect, it } from "vitest";

import { inAppDragInProgress, trackInAppDrags } from "./inAppDrag";

function fakeDragTarget() {
  const listeners = new Map<string, Set<EventListener>>();
  const target = {
    addEventListener: (type: string, listener: EventListener) => {
      const forType = listeners.get(type) ?? new Set<EventListener>();
      forType.add(listener);
      listeners.set(type, forType);
    },
    removeEventListener: (type: string, listener: EventListener) => {
      listeners.get(type)?.delete(listener);
    },
  } as unknown as Pick<EventTarget, "addEventListener" | "removeEventListener">;

  return {
    target,
    fire: (type: string) => {
      for (const listener of [...(listeners.get(type) ?? [])]) listener({} as Event);
    },
  };
}

describe("in-app drag tracking", () => {
  it("reports no drag before any drag starts", () => {
    const { target } = fakeDragTarget();
    const stopTracking = trackInAppDrags(target);
    expect(inAppDragInProgress()).toBe(false);
    stopTracking();
  });

  it("flags a drag that starts in this document until it ends", () => {
    const { target, fire } = fakeDragTarget();
    const stopTracking = trackInAppDrags(target);

    fire("dragstart");
    expect(inAppDragInProgress()).toBe(true);
    fire("dragend");
    expect(inAppDragInProgress()).toBe(false);

    stopTracking();
  });

  it("clears the flag when tracking stops mid-drag", () => {
    const { target, fire } = fakeDragTarget();
    const stopTracking = trackInAppDrags(target);

    fire("dragstart");
    stopTracking();
    expect(inAppDragInProgress()).toBe(false);

    fire("dragend");
    expect(inAppDragInProgress()).toBe(false);
  });
});

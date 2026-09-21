import { useEffect, useState } from "react";

/**
 * Keeps something that has just been dismissed on screen for one short fade, so
 * a retiring surface leaves the way it arrived instead of blinking off.
 *
 * The value is handed back once more with `leaving: true` while the fade runs;
 * `shown` is null when there is nothing left to draw. Nothing is measured and no
 * layout is read - the caller only draws `shown` and adds its own leaving class,
 * which is what lets the same hook serve the enlarged page, both menus, the
 * revision popup, the guide queue and the drop overlay.
 */
export interface LeavingValue<T> {
  /** The current value, or the value that has just retired, or null. */
  shown: T | null;
  /** True while `shown` is the retired value rather than the current one. */
  leaving: boolean;
}

export function useLeavingValue<T>(value: T | null, durationMs: number): LeavingValue<T> {
  const [prevValue, setPrevValue] = useState<T | null>(value);
  const [retired, setRetired] = useState<T | null>(null);

  // When value changes, adjust state synchronously during render so that a retiring
  // surface never unmounts for a frame before its exit animation begins.
  if (value !== prevValue) {
    setPrevValue(value);
    if (value === null && prevValue !== null) {
      setRetired(prevValue);
    } else if (value !== null) {
      setRetired(null);
    }
  }

  useEffect(() => {
    if (retired === null) return;
    const timer = setTimeout(() => {
      setRetired(null);
    }, durationMs);
    return () => clearTimeout(timer);
  }, [retired, durationMs]);

  const leaving = value === null && retired !== null;
  return { shown: value ?? retired, leaving };
}


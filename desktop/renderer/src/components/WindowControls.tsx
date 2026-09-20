import { useEffect, useState } from "react";

export function WindowControls() {
  const bridge = typeof window === "undefined" ? undefined : window.studyForge;
  const [isMaximized, setIsMaximized] = useState(false);

  useEffect(() => {
    if (bridge === undefined) return;
    let active = true;
    void bridge.isWindowMaximized().then((maximized) => {
      if (active) setIsMaximized(maximized);
    });
    const unsubscribe = bridge.onWindowMaximizedChange((maximized) => {
      setIsMaximized(maximized);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [bridge]);

  if (bridge === undefined) return null;

  return (
    <div className="window-controls">
      <button
        type="button"
        className="window-control"
        aria-label="Minimize"
        onClick={() => void bridge.minimizeWindow()}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <line x1="1" y1="6" x2="11" y2="6" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
      <button
        type="button"
        className="window-control"
        aria-label={isMaximized ? "Restore" : "Maximize"}
        onClick={() => void bridge.toggleMaximizeWindow()}
      >
        {isMaximized ? (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <rect
              x="3.5"
              y="1"
              width="7.5"
              height="7.5"
              rx="1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.1"
            />
            <rect
              x="1"
              y="3.5"
              width="7.5"
              height="7.5"
              rx="1"
              fill="var(--base)"
              stroke="currentColor"
              strokeWidth="1.1"
            />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <rect
              x="1.5"
              y="1.5"
              width="9"
              height="9"
              rx="1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.1"
            />
          </svg>
        )}
      </button>
      <button
        type="button"
        className="window-control window-control-close"
        aria-label="Close"
        onClick={() => void bridge.closeWindow()}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <line x1="1.5" y1="1.5" x2="10.5" y2="10.5" stroke="currentColor" strokeWidth="1.2" />
          <line x1="10.5" y1="1.5" x2="1.5" y2="10.5" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
    </div>
  );
}

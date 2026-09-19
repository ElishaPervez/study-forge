import { describe, expect, it } from "vitest";

import {
  beginGlobalDropSession,
  dataTransferHasFiles,
  dropSessionAfterEnter,
  dropSessionAfterLeave,
  dropSessionAfterOver,
  globalDropOverlayVisible,
  sourceDropHasFiles,
} from "./useGlobalFileDrop";

describe("global file drop session", () => {
  it("starts hidden", () => {
    expect(globalDropOverlayVisible(beginGlobalDropSession())).toBe(false);
  });

  it("shows the overlay only once a file drag has entered the window", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, false);
    expect(globalDropOverlayVisible(session)).toBe(false);
    session = dropSessionAfterEnter(session, true);
    expect(globalDropOverlayVisible(session)).toBe(true);
  });

  it("detects file drags from dataTransfer types", () => {
    const filesDrag = { types: ["Files"] } as unknown as DataTransfer;
    const textDrag = { types: ["text/plain"] } as unknown as DataTransfer;
    expect(dataTransferHasFiles(filesDrag)).toBe(true);
    expect(dataTransferHasFiles(textDrag)).toBe(false);
    expect(dataTransferHasFiles(null)).toBe(false);
  });

  it("ignores non-file drags even after enter/leave churn", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, false);
    session = dropSessionAfterOver(session, false);
    session = dropSessionAfterLeave(session);
    expect(globalDropOverlayVisible(session)).toBe(false);
  });

  it("stays visible while moving between child elements", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, true);
    session = dropSessionAfterEnter(session, true);
    session = dropSessionAfterLeave(session);
    expect(globalDropOverlayVisible(session)).toBe(true);
  });

  it("hides when the drag leaves the window entirely", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, true);
    session = dropSessionAfterEnter(session, true);
    session = dropSessionAfterLeave(session);
    session = dropSessionAfterLeave(session);
    expect(globalDropOverlayVisible(session)).toBe(false);
  });

  it("discovers files mid-drag via dragover (dragenter on a child may lack types)", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, false);
    expect(globalDropOverlayVisible(session)).toBe(false);
    session = dropSessionAfterOver(session, true);
    expect(globalDropOverlayVisible(session)).toBe(true);
  });

  it("keeps the sticky containsFiles flag through dragover events", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterEnter(session, true);
    session = dropSessionAfterOver(session, false);
    expect(globalDropOverlayVisible(session)).toBe(true);
  });

  it("never drives the depth below zero", () => {
    let session = beginGlobalDropSession();
    session = dropSessionAfterLeave(session);
    expect(session.depth).toBe(0);
    expect(globalDropOverlayVisible(session)).toBe(false);
  });

  it("counts only drags from outside the window as source drops", () => {
    const filesDrag = { types: ["Files"] } as unknown as DataTransfer;
    const textDrag = { types: ["text/plain"] } as unknown as DataTransfer;
    expect(sourceDropHasFiles(filesDrag, false)).toBe(true);
    expect(sourceDropHasFiles(filesDrag, true)).toBe(false);
    expect(sourceDropHasFiles(textDrag, false)).toBe(false);
    expect(sourceDropHasFiles(null, false)).toBe(false);
  });
});

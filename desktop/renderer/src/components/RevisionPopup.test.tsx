import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  RevisionPopup,
  constrainRevisionPopupSize,
  isRevisionInstructionValid,
  positionRevisionPopup,
  resizeRevisionBounds,
  truncateSelection,
  type RevisionBounds,
  type RevisionRect,
} from "./RevisionPopup";

const bounds: RevisionBounds = { left: 0, top: 0, right: 640, bottom: 480 };
const selection: RevisionRect = { left: 160, top: 120, right: 310, bottom: 148 };

describe("RevisionPopup", () => {
  it("quotes and truncates a long selection for the compact popup", () => {
    expect(truncateSelection("  A   long passage with more words than the popup needs  ", 24))
      .toBe("“A long passage with more…”");
  });

  it("places the popup below the selection when the viewer has room", () => {
    expect(positionRevisionPopup(selection, bounds, { width: 300, height: 160, gap: 12 }))
      .toEqual({ left: 160, top: 160, placement: "below" });
  });

  it("accepts DOM-style width and height rectangles", () => {
    expect(positionRevisionPopup(
      { left: 160, top: 120, width: 150, height: 28 },
      { left: 0, top: 0, width: 640, height: 480 },
      { width: 300, height: 160, gap: 12 },
    )).toEqual({ left: 160, top: 160, placement: "below" });
  });

  it("flips above the selection and clamps to the viewer when below would overflow", () => {
    const nearBottom: RevisionRect = { left: 590, top: 420, right: 632, bottom: 448 };

    expect(positionRevisionPopup(nearBottom, bounds, { width: 300, height: 160, gap: 12 }))
      .toEqual({ left: 328, top: 248, placement: "above" });
  });

  it("caps measured popup dimensions to the available viewer bounds", () => {
    expect(constrainRevisionPopupSize(
      { left: 0, top: 0, right: 500, bottom: 240 },
      { width: 320, height: 320, gap: 12 },
    )).toEqual({ width: 320, height: 216, gap: 12 });
  });

  it("rebuilds local viewer bounds after the viewer is resized", () => {
    expect(resizeRevisionBounds(bounds, 360, 220)).toEqual({
      left: 0,
      top: 0,
      right: 360,
      bottom: 220,
    });
  });

  it("requires custom text for Update guide but not for Clarify this", () => {
    expect(isRevisionInstructionValid("")).toBe(false);
    expect(isRevisionInstructionValid("  ")).toBe(false);
    expect(isRevisionInstructionValid("make it clearer")).toBe(true);

    const markup = renderToStaticMarkup(
      <RevisionPopup
        selectedText="The selected passage"
        anchorRect={selection}
        viewerBounds={bounds}
        onClarify={() => undefined}
        onUpdate={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain("The selected passage");
    expect(markup).toContain("Clarify this");
    expect(markup).toContain("Update guide");
    expect(markup).toContain("What should change?");
    expect(markup).toMatch(/Update guide<\/button>/);
    expect(markup).toContain('disabled=""');
  });
});

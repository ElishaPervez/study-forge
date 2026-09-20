import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const cssPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "styles.css");
const css = readFileSync(cssPath, "utf8");

describe("Study Forge accessibility and compact-window rules", () => {
  it("keeps usable controls visibly focused", () => {
    expect(css).toMatch(/button:focus-visible,[\s\S]*a:focus-visible,[\s\S]*input:focus-visible,[\s\S]*select:focus-visible,[\s\S]*textarea:focus-visible/);
    expect(css).toContain("outline: 2px solid var(--accent)");
  });

  it("snaps motion when reduced motion is requested", () => {
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation-duration: 0.001ms !important");
    expect(css).toContain("transition-duration: 0.001ms !important");
  });

  it("keeps the 320px minimum and responsive single-column layout", () => {
    expect(css).toContain("min-width: 320px");
    expect(css).toContain("@media (max-width: 900px)");
    expect(css).toContain("grid-template-columns: 1fr");
    expect(css).toContain("@media (max-width: 640px)");
  });

  it("defines fixed-position compact sidebar layout and toggle controls", () => {
    expect(css).toContain(".app-body.is-rail-compact");
    expect(css).toContain(".rail-toggle");
    expect(css).toContain(".rail-header");
  });

  it("anchors the guide queue to the bottom right with its own scrolling list", () => {
    expect(css).toMatch(/\.generation-queue \{[\s\S]*?position: fixed;/);
    expect(css).toMatch(/\.generation-queue \{[\s\S]*?bottom: 16px;/);
    expect(css).toMatch(/\.generation-queue-list \{[\s\S]*?overflow-y: auto;/);
    expect(css).toMatch(/@media \(max-width: 640px\) \{[\s\S]*?\.generation-queue \{/);
  });
});

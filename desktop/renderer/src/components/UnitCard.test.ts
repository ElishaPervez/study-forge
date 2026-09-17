import { describe, expect, it } from "vitest";

import { hasPublishedArtifact } from "./UnitCard";

describe("hasPublishedArtifact", () => {
  it.each(["failed", "needs-attention"])(
    "does not treat %s status as an available artifact when the server returns null",
    (status) => {
      expect(
        hasPublishedArtifact({
          status,
          calls: 1,
          requested_refs: [],
          findings: ["not published"],
          artifact_url: null,
        }),
      ).toBe(false);
    },
  );

  it("recognizes a published artifact URL", () => {
    expect(
      hasPublishedArtifact({
        status: "ok",
        calls: 1,
        requested_refs: [],
        findings: [],
        artifact_url: "/api/jobs/job/units/unit/artifact.html",
      }),
    ).toBe(true);
  });
});

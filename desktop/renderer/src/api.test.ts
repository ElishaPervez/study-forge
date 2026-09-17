import { describe, expect, it, vi } from "vitest";

import { artifactUrl, createApi } from "./api";

describe("createApi", () => {
  it("posts ranges to create a job", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: "abc", units: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = createApi("http://127.0.0.1:5000");
    await api.createJob("C:/scans/book.pdf", [["Unit 1", 1, 5]]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:5000/api/jobs");
    expect(JSON.parse(init.body)).toEqual({
      pdf: "C:/scans/book.pdf",
      ranges: [["Unit 1", 1, 5]],
    });
  });

  it("builds preview and download urls from the same path", () => {
    const base = "http://127.0.0.1:5000";

    expect(artifactUrl(base, "job1", "unit-01", false)).toBe(
      `${base}/api/jobs/job1/units/unit-01/artifact.html`,
    );
    expect(artifactUrl(base, "job1", "unit-01", true)).toBe(
      `${base}/api/jobs/job1/units/unit-01/artifact.html?download=1`,
    );
  });

  it("exposes the same artifact URL builder on the API object", () => {
    const api = createApi("http://127.0.0.1:5000");

    expect(api.artifactUrl("job1", "unit-01", false)).toBe(
      "http://127.0.0.1:5000/api/jobs/job1/units/unit-01/artifact.html",
    );
  });

  it("surfaces the server's detail message on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({ detail: "page 99 is outside 1..20" }),
      }),
    );

    await expect(createApi("http://x").getJob("a")).rejects.toThrow("page 99");
  });
});

import { describe, expect, it, vi } from "vitest";

import { artifactUrl, createApi } from "./api";

describe("createApi", () => {
  it("registers the selected source paths", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        source_id: "source-1",
        kind: "images",
        display_name: "page-1.png",
        files: ["page-1.png", "page-2.png"],
        page_count: null,
        image_count: 2,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = createApi("http://127.0.0.1:5000");
    await api.registerSource(["C:/scans/page-1.png", "C:/scans/page-2.png"]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:5000/api/sources");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      paths: ["C:/scans/page-1.png", "C:/scans/page-2.png"],
    });
  });

  it("creates one guide with the selected source and selection", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ guide_id: "guide-1", status: "pending" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = createApi("http://127.0.0.1:5000");
    await api.createGuide("source-1", { mode: "custom", start: 3, end: 7 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:5000/api/guides");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      source_id: "source-1",
      selection: { mode: "custom", start: 3, end: 7 },
    });
  });

  it("starts generation for the created guide", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ guide_id: "guide-1", status: "ok" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").generateGuide("guide-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1/generate",
      { method: "POST" },
    );
  });

  it("posts the selected passage and revision instruction", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        guide_id: "guide-1",
        status: "ok",
        revision_count: 1,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").reviseGuide("guide-1", {
      selected_text: "The selected passage",
      instruction: "Explain the distinction",
      mode: "custom",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1/revisions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_text: "The selected passage",
          instruction: "Explain the distinction",
          mode: "custom",
        }),
      },
    );
  });

  it("loads persisted guides newest first from the history route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").listGuides();

    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:5000/api/guides");
  });

  it("renames one persisted guide", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ guide_id: "guide-1", name: "Renamed guide" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").renameGuide("guide-1", "Renamed guide");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Renamed guide" }),
      },
    );
  });

  it("retries the same failed guide id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ guide_id: "failed-guide", status: "ok" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").retryGuide("failed-guide");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/failed-guide/retry",
      { method: "POST" },
    );
  });

  it("deletes one persisted guide", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ guide_id: "guide-1", deleted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").deleteGuide("guide-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1",
      { method: "DELETE" },
    );
  });

  it("removes only the current source", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ source_id: "source-1", deleted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").removeSource("source-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/sources/source-1",
      { method: "DELETE" },
    );
  });

  it("builds the guide artifact URL", () => {
    const base = "http://127.0.0.1:5000";

    expect(artifactUrl(base, "guide-1", false)).toBe(
      `${base}/api/guides/guide-1/artifact.html`,
    );
    expect(artifactUrl(base, "guide-1", true)).toBe(
      `${base}/api/guides/guide-1/artifact.html?download=1`,
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

    await expect(createApi("http://x").getGuide("guide-1")).rejects.toThrow("page 99");
  });

  it("surfaces structured revision findings on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({
          detail: {
            message: "revision failed",
            findings: ["The replacement is missing a diagram title."],
          },
        }),
      }),
    );

    await expect(
      createApi("http://x").reviseGuide("guide-1", {
        selected_text: "text",
        instruction: "change",
        mode: "custom",
      }),
    ).rejects.toThrow("The replacement is missing a diagram title.");
  });
});

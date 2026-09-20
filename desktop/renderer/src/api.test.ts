import { describe, expect, it, vi } from "vitest";

import { ApiError, artifactUrl, createApi } from "./api";

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

  it("accepts one guide with the selected source, selection and receipt", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ guide_id: "guide-1", status: "pending" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const api = createApi("http://127.0.0.1:5000");
    await api.createGuide("source-1", { mode: "custom", start: 3, end: 7 }, "a".repeat(32));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:5000/api/guides");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      source_id: "source-1",
      selection: { mode: "custom", start: 3, end: 7 },
      receipt: "a".repeat(32),
    });
  });

  it("starts generation for the created guide with its receipt", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ guide_id: "guide-1", status: "pending" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").generateGuide("guide-1", "b".repeat(32));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1/generate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ receipt: "b".repeat(32) }),
      },
    );
  });

  it("posts the selected passage and revision instruction with a receipt", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({
        guide_id: "guide-1",
        status: "ok",
        revision_count: 1,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").reviseGuide(
      "guide-1",
      {
        selected_text: "The selected passage",
        instruction: "Explain the distinction",
        mode: "custom",
      },
      "c".repeat(32),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/guide-1/revisions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selected_text: "The selected passage",
          instruction: "Explain the distinction",
          mode: "custom",
          receipt: "c".repeat(32),
        }),
      },
    );
  });

  it("reads the queue summary and one request row", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          service_start: "service-1",
          change_number: 4,
          accepting: true,
          closing: false,
          operations: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ receipt: "a".repeat(32), state: "running" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi("http://127.0.0.1:5000");

    const summary = await api.getQueue();
    const operation = await api.getOperation("a".repeat(32));

    expect(summary.change_number).toBe(4);
    expect(summary.accepting).toBe(true);
    expect(operation.state).toBe("running");
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:5000/api/queue");
    expect(fetchMock.mock.calls[1][0]).toBe(
      `http://127.0.0.1:5000/api/operations/${"a".repeat(32)}`,
    );
  });

  it("retries a failed request by its receipt and prepares, resumes and confirms closing", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ guide_id: "guide-1", status: "ok" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi("http://127.0.0.1:5000");

    await api.retryOperation("a".repeat(32), "d".repeat(32));
    await api.prepareClose();
    await api.resumeClose();
    await api.confirmClose();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `http://127.0.0.1:5000/api/operations/${"a".repeat(32)}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ receipt: "d".repeat(32) }),
      },
    );
    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:5000/api/shutdown/prepare");
    expect(fetchMock.mock.calls[2][0]).toBe("http://127.0.0.1:5000/api/shutdown/resume");
    expect(fetchMock.mock.calls[3][0]).toBe("http://127.0.0.1:5000/api/shutdown/confirm");
  });

  it("reports the status of a refusal so a lost reply can be told apart", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: "unknown request" }),
      }),
    );

    await expect(
      createApi("http://x").getOperation("a".repeat(32)),
    ).rejects.toMatchObject({ status: 404 });
    await expect(createApi("http://x").getOperation("a".repeat(32))).rejects.toBeInstanceOf(
      ApiError,
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

  it("retries the same failed guide id with a receipt", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ guide_id: "failed-guide", status: "failed" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApi("http://127.0.0.1:5000").retryGuide("failed-guide", "e".repeat(32));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:5000/api/guides/failed-guide/retry",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ receipt: "e".repeat(32) }),
      },
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

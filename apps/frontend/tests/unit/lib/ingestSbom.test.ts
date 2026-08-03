/**
 * projectsApi.ingestSbom — feat/demo-sandbox-scan.
 *
 * Guards the multipart body the SBOM ingest endpoint receives: the `sbom` file
 * is always attached, and the optional `ref` / `release` fields are only sent
 * when non-blank (so an empty label never lands on the wire).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { post: vi.fn() },
}));

import { api } from "@/lib/api";
import { ingestSbom } from "@/lib/projectsApi";

const mockedPost = vi.mocked(api.post);

function sbomFile(): File {
  return new File(['{"bomFormat":"CycloneDX"}'], "sbom.cdx.json", {
    type: "application/json",
  });
}

describe("ingestSbom", () => {
  it("POSTs a multipart form with just the file when no ref/release given", async () => {
    mockedPost.mockResolvedValueOnce({ data: { id: "scan-1" } });
    const scan = await ingestSbom("proj-1", sbomFile());

    expect(scan).toEqual({ id: "scan-1" });
    const [url, body] = mockedPost.mock.calls[0];
    expect(url).toBe("/v1/projects/proj-1/sbom-ingest");
    const form = body as FormData;
    expect(form.get("sbom")).toBeInstanceOf(File);
    expect(form.get("ref")).toBeNull();
    expect(form.get("release")).toBeNull();
  });

  it("includes trimmed ref + release when provided", async () => {
    mockedPost.mockResolvedValueOnce({ data: { id: "scan-2" } });
    await ingestSbom("proj-1", sbomFile(), {
      ref: "  refs/heads/main ",
      release: " v1.2.3 ",
    });

    const form = mockedPost.mock.calls.at(-1)?.[1] as FormData;
    expect(form.get("ref")).toBe("refs/heads/main");
    expect(form.get("release")).toBe("v1.2.3");
  });

  it("omits blank ref/release", async () => {
    mockedPost.mockResolvedValueOnce({ data: { id: "scan-3" } });
    await ingestSbom("proj-1", sbomFile(), { ref: "   ", release: "" });

    const form = mockedPost.mock.calls.at(-1)?.[1] as FormData;
    expect(form.get("ref")).toBeNull();
    expect(form.get("release")).toBeNull();
  });
});

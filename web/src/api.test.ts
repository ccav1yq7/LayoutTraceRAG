import { describe, it, expect } from "vitest";
import { mediaUrl, mergeRun, type Run } from "./api";
describe("untrusted identifiers and run replay", () => {
  it("only creates same-origin asset URLs from opaque IDs", () => {
    expect(mediaUrl("assets", "asset_example")).toBe(
      "/v1/assets/asset_example",
    );
    for (const id of [
      "https://evil.invalid/a.png",
      "../../LLM.config",
      "asset_x?token=x",
      "upload_example",
    ])
      expect(mediaUrl("assets", id)).toBeUndefined();
  });
  it("deduplicates resumed runs without losing revision order", () => {
    const one = { run_id: "run_a", revision: 1, status: "RUNNING" } as Run;
    const two = { run_id: "run_b", revision: 2, status: "COMPLETED" } as Run;
    const replay = { ...one, status: "COMPLETED" };
    const result = mergeRun([one, two], replay);
    expect(result.map((r) => r.run_id)).toEqual(["run_a", "run_b"]);
    expect(result[0].status).toBe("COMPLETED");
  });
});

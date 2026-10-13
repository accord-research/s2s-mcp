import { describe, expect, it } from "vitest";
import { pendingMessage } from "../src/pending";

describe("pendingMessage", () => {
  it("names the method and the time elapsed", () => {
    const m = pendingMessage(35, "BCSD");
    expect(m).toContain("BCSD");
    expect(m).toContain("35 s");
  });
  it("formats minutes once past a minute and works without a method", () => {
    const m = pendingMessage(75);
    expect(m).toContain("1 min 15 s");
    expect(m).not.toContain("undefined");
  });
});

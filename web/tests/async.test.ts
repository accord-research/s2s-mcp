import { describe, expect, it, vi } from "vitest";
import { withTimeout } from "../src/async";

describe("withTimeout", () => {
  it("passes a value through when the promise settles in time", async () => {
    await expect(withTimeout(Promise.resolve(42), 1000, "late")).resolves.toBe(42);
  });
  it("rejects with the given message when the promise outlives the deadline", async () => {
    vi.useFakeTimers();
    const never = new Promise<number>(() => {});
    const p = withTimeout(never, 75_000, "still computing");
    vi.advanceTimersByTime(75_000);
    await expect(p).rejects.toThrow("still computing");
    vi.useRealTimers();
  });
});

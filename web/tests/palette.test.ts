import { describe, expect, it } from "vitest";
import { bandIndex, cellColor, classify, LIGHT_NO_SIGNAL, type Style } from "../src/palette";

const style: Style = {
  below_colors: ["b0", "b1", "b2", "b3", "b4", "b5"],
  normal_colors: ["n0", "n1", "n2", "n3", "n4", "n5"],
  above_colors: ["a0", "a1", "a2", "a3", "a4", "a5"],
  prob_bins: [33.33, 36, 40, 50, 60, 70, 100.01],
  dry_color: "#bebebe",
  nodata_color: "#ffffff",
};

describe("bandIndex", () => {
  it("maps percentages onto the six bands", () => {
    expect(bandIndex(30, style.prob_bins)).toBe(-1);
    expect(bandIndex(33.33, style.prob_bins)).toBe(0);
    expect(bandIndex(35.9, style.prob_bins)).toBe(0);
    expect(bandIndex(36, style.prob_bins)).toBe(1);
    expect(bandIndex(55, style.prob_bins)).toBe(3);
    expect(bandIndex(100, style.prob_bins)).toBe(5);
  });
});

describe("classify", () => {
  it("picks the argmax tercile", () => {
    expect(classify(0.2, 0.3, 0.5)).toEqual({ tercile: 2, p: 0.5 });
    expect(classify(0.5, 0.3, 0.2)).toEqual({ tercile: 0, p: 0.5 });
  });
});

describe("cellColor", () => {
  it("is transparent for NaN cells", () => {
    expect(cellColor(null, 0.3, 0.3, style, "dominant")).toBeNull();
  });
  it("uses the dominant tercile's ramp at its band", () => {
    expect(cellColor(0.2, 0.25, 0.55, style, "dominant")).toBe("a3");
    expect(cellColor(0.5, 0.3, 0.2, style, "dominant")).toBe("b3");
    expect(cellColor(0.3, 0.4, 0.3, style, "dominant")).toBe("n2");
  });
  it("uses nodata colour when no tercile reaches the first bin", () => {
    expect(cellColor(0.333, 0.333, 0.333, style, "dominant")).toBe(style.nodata_color);
  });
  it("single-tercile layers use one ramp and a light colour below the first bin", () => {
    expect(cellColor(0.2, 0.25, 0.55, style, "above")).toBe("a3");
    expect(cellColor(0.2, 0.25, 0.55, style, "below")).toBe(LIGHT_NO_SIGNAL);
  });
});

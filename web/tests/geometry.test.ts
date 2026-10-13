import { describe, expect, it } from "vitest";
import { aspectOf, cellAt, clampTransform, fitBox, fullscreenAvailable, zoomAt, type Bounds } from "../src/geometry";
import * as geometry from "../src/geometry";

const ea: Bounds = [-12, 6, 28, 42]; // 18 tall, 14 wide

describe("fitBox", () => {
  it("letterboxes at the region aspect", () => {
    expect(aspectOf(ea)).toBeCloseTo(14 / 18);
    expect(fitBox(900, 700, aspectOf(ea))).toEqual({ w: 700 * (14 / 18), h: 700 });
    expect(fitBox(300, 700, aspectOf(ea))).toEqual({ w: 300, h: 300 / (14 / 18) });
  });
});

describe("zoom and clamp", () => {
  it("never scales below 1 and keeps the content covering the box", () => {
    expect(clampTransform(1, 50, -50, 400, 300)).toEqual({ s: 1, tx: 0, ty: 0 });
    expect(clampTransform(2, 10, -1000, 400, 300)).toEqual({ s: 2, tx: 0, ty: -300 });
    const z = zoomAt(1, 0, 0, 2, 200, 150, 400, 300);
    expect(z.s).toBe(2);
    expect(z.tx).toBe(-200);
    expect(z.ty).toBe(-150);
    expect(zoomAt(1, 0, 0, 0.5, 200, 150, 400, 300).s).toBe(1);
    expect(zoomAt(8, 0, 0, 2, 0, 0, 400, 300).s).toBe(8);
  });
});

describe("cellAt", () => {
  it("maps pixels to grid rows south-to-north", () => {
    const hit = cellAt(0, 299, 400, 300, ea, 18, 14)!;
    expect(hit).toMatchObject({ i: 0, j: 0 });
    expect(hit.lat).toBeCloseTo(-11.5, 0);
    expect(hit.lon).toBeCloseTo(28.0, 0);
    expect(cellAt(399, 0, 400, 300, ea, 18, 14)).toMatchObject({ i: 17, j: 13 });
    expect(cellAt(-1, 0, 400, 300, ea, 18, 14)).toBeNull();
    expect(cellAt(0, 300, 400, 300, ea, 18, 14)).toBeNull();
  });
});

describe("fullscreenAvailable", () => {
  it("is false unless the host lists fullscreen", () => {
    expect(fullscreenAvailable(undefined)).toBe(false);
    expect(fullscreenAvailable(["inline"])).toBe(false);
    expect(fullscreenAvailable(["inline", "fullscreen"])).toBe(true);
  });
});

describe("inlineMapArea", () => {
  it("caps the inline map height so the card fits one screen in wide hosts", () => {
    const { inlineMapArea, INLINE_MAX_MAP_HEIGHT } = geometry;
    expect(INLINE_MAX_MAP_HEIGHT).toBeLessThanOrEqual(480);
    expect(inlineMapArea(760)).toEqual({ w: 732, h: INLINE_MAX_MAP_HEIGHT });   // ChatGPT-width card: capped
    expect(inlineMapArea(400)).toEqual({ w: 372, h: Math.round(372 * 0.9) });  // narrow card: unchanged rule
    expect(inlineMapArea(100).w).toBe(120);                                     // never below the floor
  });
});

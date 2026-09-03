/* Bağımlılıksız çalışır:  node --test web/src/lib/sideDrawer.test.ts */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { nextDrawerState } from "./sideDrawer.ts";

describe("nextDrawerState", () => {
  it("☰ açar ve tekrar basınca kapatır", () => {
    assert.equal(nextDrawerState(false, "toggle"), true);
    assert.equal(nextDrawerState(true, "toggle"), false);
  });

  it("dışa tıklama, ✕ ve Esc kapatır", () => {
    for (const olay of ["backdrop", "close", "escape"] as const) {
      assert.equal(nextDrawerState(true, olay), false, olay);
    }
  });

  it("sayfa değişince menü kapanır (arkada açık kalmaz)", () => {
    assert.equal(nextDrawerState(true, "navigate"), false);
  });

  it("☰ dışında hiçbir olay menüyü AÇMAZ", () => {
    for (const olay of ["backdrop", "close", "escape", "navigate"] as const) {
      assert.equal(nextDrawerState(false, olay), false, olay);
    }
  });
});

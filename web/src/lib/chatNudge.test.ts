/* Bağımlılıksız çalışır:  node --test web/src/lib/chatNudge.test.ts
 * (Node 24 .ts dosyalarındaki tipleri kendisi sıyırır; web'de test koşucusu
 * yok, bu yüzden mantık DOM'suz tutuldu.) */
import assert from "node:assert/strict";
import { describe, it, mock } from "node:test";

import {
  NUDGE_DURATION_MS,
  NUDGE_INTRO_DELAY_MS,
  NUDGE_PERIOD_MS,
  isOutsideClick,
  startNudgeCycle,
} from "./chatNudge.ts";

const panel = { contains: (node: unknown) => node === "ic" };

describe("isOutsideClick", () => {
  it("panelin dışına tıklama küçültür", () => {
    assert.equal(isOutsideClick(panel, "dis" as unknown as EventTarget), true);
  });

  it("panelin içine tıklama küçültmez", () => {
    assert.equal(isOutsideClick(panel, "ic" as unknown as EventTarget), false);
  });

  it("panel ya da hedef yoksa hiçbir şey yapılmaz", () => {
    assert.equal(isOutsideClick(null, "dis" as unknown as EventTarget), false);
    assert.equal(isOutsideClick(panel, null), false);
  });
});

describe("startNudgeCycle", () => {
  it("ilk hatırlatma gecikmeli ve belirgin, sonrakiler ~10 sn'de bir hafif", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const kinds: string[] = [];
      const stop = startNudgeCycle((k) => kinds.push(k), { intro: true });

      mock.timers.tick(NUDGE_INTRO_DELAY_MS - 1);
      assert.deepEqual(kinds, [], "gecikme dolmadan animasyon başlamaz");

      mock.timers.tick(1);
      assert.deepEqual(kinds, ["intro"]);

      mock.timers.tick(NUDGE_DURATION_MS);
      assert.deepEqual(kinds, ["intro", "none"], "animasyon süresi bitince sıfırlanır");

      // Bir sonraki hatırlatma, öncekinin BAŞLANGICINDAN tam bir periyot sonra.
      mock.timers.tick(NUDGE_PERIOD_MS - NUDGE_DURATION_MS - 1);
      assert.deepEqual(kinds, ["intro", "none"]);
      mock.timers.tick(1);
      assert.deepEqual(kinds, ["intro", "none", "soft"], "ikinci ve sonrakiler hafif");

      stop();
      mock.timers.tick(NUDGE_PERIOD_MS * 3);
      assert.deepEqual(kinds, ["intro", "none", "soft"], "durdurulan döngü devam etmez");
    } finally {
      mock.timers.reset();
    }
  });

  it("intro geçilirse ilk hatırlatma da hafiftir", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const kinds: string[] = [];
      const stop = startNudgeCycle((k) => kinds.push(k), { intro: false });
      mock.timers.tick(NUDGE_PERIOD_MS - NUDGE_DURATION_MS);
      assert.deepEqual(kinds, ["soft"]);
      stop();
    } finally {
      mock.timers.reset();
    }
  });
});

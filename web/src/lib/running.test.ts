/* Bağımlılıksız çalışır:  node --test web/src/lib/running.test.ts
 * (bkz. chatNudge.test.ts — web'de test koşucusu yok, mantık DOM'suz.)
 *
 * Koşan format form tarafı. Sunucu ikizinin testleri:
 * tests/test_parser.py > "koşan format" bölümü. İkisi AYNI kuralları
 * doğrular; biri değişirse diğeri de değişmeli. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseRunning, runningDirectionWarning, runningError, runningHint } from "./running.ts";

describe("parseRunning — yön ve fark", () => {
  it("azalış borçtur, kaydedilen sayı farktır", () => {
    const r = parseRunning("70-20-50");
    assert.equal(r?.kind, "debt");
    assert.equal(r?.qty, 20);
    assert.equal(r?.consistent, true);
  });

  it("artış tahsilattır", () => {
    const r = parseRunning("70-30-100");
    assert.equal(r?.kind, "payment");
    assert.equal(r?.qty, 30);
    assert.equal(r?.consistent, true);
  });

  it("büyük sayılar", () => {
    const r = parseRunning("1000-285-715");
    assert.equal(r?.kind, "debt");
    assert.equal(r?.qty, 285);
    assert.equal(r?.consistent, true);
  });
});

describe("parseRunning — ayraç salt görseldir", () => {
  for (const metin of ["70-20-50", "70+20+50", "70*20*50", "70 - 20 - 50", "70 +20+ 50"]) {
    it(`"${metin}" aynı sonucu verir`, () => {
      const r = parseRunning(metin);
      assert.equal(r?.qty, 20);
      assert.equal(r?.kind, "debt");
      assert.equal(r?.consistent, true);
    });
  }
});

describe("parseRunning — matematik kontrolü", () => {
  it("orta sayı farka eşit değilse tutarsız işaretlenir", () => {
    const r = parseRunning("70-25-50");
    assert.equal(r?.consistent, false);
    assert.equal(r?.qty, 20); // önerilen doğru fark
    assert.match(runningError(r!)!, /fark 20 olmalı ama 25/);
  });

  it("tutarlıysa hata yok", () => {
    assert.equal(runningError(parseRunning("70-20-50")!), null);
  });
});

describe("parseRunning — yanlış tetiklenme koruması", () => {
  for (const metin of [
    "20", // düz adet
    "20,5", // düz ondalık adet
    "12-05-2026", // tarih
    "2026-05-12", // tarih (ters)
    "0532-456-789", // baştaki sıfır
    "0532-456-78-90", // dört parça (telefon)
    "70-0-70", // hareket yok
    "70-20", // iki parça
    "saman", // sayı değil
    "70-20-50 saman", // adet alanı yalnızca sayı taşır
  ]) {
    it(`"${metin}" koşan format sayılmaz`, () => {
      assert.equal(parseRunning(metin), null);
    });
  }

  it("tarihe benzeyen ama matematiği TUTAN üçlü geçerlidir", () => {
    assert.equal(parseRunning("10-5-5")?.qty, 5);
  });
});

describe("gösterim", () => {
  it("ipucu ilk → son ve farkı gösterir", () => {
    assert.equal(runningHint(parseRunning("70-20-50")!, "balya"), "70 → 50 · 20 balya (borç)");
  });

  it("yön modalla çelişirse uyarır, çelişmezse susar", () => {
    assert.equal(runningDirectionWarning(parseRunning("70-20-50")!, "debt"), null);
    assert.match(
      runningDirectionWarning(parseRunning("70-30-100")!, "debt")!,
      /tahsilata benziyor/,
    );
  });
});

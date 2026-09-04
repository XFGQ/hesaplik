/* Bağımlılıksız çalışır:  node --test web/src/lib/format.test.ts
 *
 * Kişi defterindeki hareket KARTININ dört parçası (dikey tarih, başlık, tür
 * etiketi, birim fiyat) burada üretiliyor. DOM koşucusu yok; kartın metni
 * saf fonksiyonlardan geldiği için asıl davranış böyle test edilebiliyor.
 * Kartın YAPISI (renkli sol çizgi, koşan bakiye alanı) ayrıca PersonDetail.tsx
 * ve index.css metni okunarak doğrulanıyor — aynı yöntem actionBar.test.ts'te. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { accountTone, dateParts, txCardTitle, txKindLabel, txUnitPrice } from "./format.ts";

const samanKalem = {
  product_name: "Saman",
  qty: "20.000",
  unit: "balya",
  unit_price: "75.00",
  line_total: "1500.00",
};

const borc = {
  kind: "DEBIT" as const,
  note: null,
  reverses_id: null,
  is_reversed: false,
  lines: [samanKalem],
};

const nakitTahsilat = {
  kind: "CREDIT" as const,
  note: null,
  reverses_id: null,
  is_reversed: false,
  lines: [],
};

describe("dateParts", () => {
  it("günü ve ay-yılı ayırır (dikey tarih)", () => {
    assert.deepEqual(dateParts("2025-07-28T09:30:00Z"), { day: "28", monthYear: "Tem 25" });
  });
});

describe("txCardTitle", () => {
  it("kalem varsa adet + birim + ürün", () => {
    assert.equal(txCardTitle(borc), "20 balya saman");
  });

  it("kalem yoksa not gösterilir", () => {
    assert.equal(txCardTitle({ ...nakitTahsilat, note: "elden ödeme" }), "elden ödeme");
  });

  it("kalem de not da yoksa işlemin kendisi yazılır", () => {
    assert.equal(txCardTitle(nakitTahsilat), "Nakit tahsilat");
    assert.equal(txCardTitle({ ...borc, lines: [] }), "Nakit borç");
  });

  it("birden çok kalemde kalanı sayar", () => {
    const iki = { ...borc, lines: [samanKalem, { ...samanKalem, product_name: "Arpa" }] };
    assert.equal(txCardTitle(iki), "20 balya saman +1");
  });
});

describe("txKindLabel", () => {
  it("borç ve tahsilat ayrı okunur", () => {
    assert.equal(txKindLabel(borc), "Borç eklendi");
    assert.equal(txKindLabel(nakitTahsilat), "Nakit tahsilat yapıldı");
  });

  it("iptal durumu türü ezer — önce geçerlilik görünür", () => {
    assert.equal(txKindLabel({ ...borc, is_reversed: true }), "İptal edildi");
    assert.equal(txKindLabel({ ...nakitTahsilat, reverses_id: 12 }), "İptal kaydı");
  });
});

describe("txUnitPrice", () => {
  it("kalemli kayıtta birim fiyat", () => {
    assert.equal(txUnitPrice(borc), "₺75,00/balya");
  });

  it("düz parada birim fiyat yoktur", () => {
    assert.equal(txUnitPrice(nakitTahsilat), null);
  });
});

describe("accountTone", () => {
  it("borçlu kırmızı, lehte yeşil, kapalı nötr", () => {
    assert.equal(accountTone("1500.00"), "borc");
    assert.equal(accountTone("-1500.00"), "alacak");
    assert.equal(accountTone("0.00"), "zero");
  });
});

// ------------------------------------------------------- kart yapısı (metin)

const page = readFileSync(new URL("../pages/PersonDetail.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");

describe("hareket kartı yapısı", () => {
  it("hareketler tablo olarak değil kart olarak çizilir", () => {
    assert.ok(page.includes('className="tx-cards"'), "kart listesi yok");
    assert.ok(!page.includes("<table>"), "hareketler hâlâ tabloda");
    assert.ok(!css.includes(".tx-table-wrap"), "eski tablo stili duruyor");
  });

  it("kart türünü sol renkli çizgiyle söyler (borç kırmızı, tahsilat yeşil)", () => {
    assert.match(css, /\.tx-card\s*\{[^}]*border-left:\s*4px solid/);
    assert.match(css, /\.tx-card-borc\s*\{\s*border-left-color:\s*var\(--borc\)/);
    assert.match(css, /\.tx-card-tahsilat\s*\{\s*border-left-color:\s*var\(--success\)/);
    // Renk kartın tamamını boyamasın: başlık ve tarih normal metin rengi.
    assert.ok(
      !/className={`tx-card \${isDebt \? "borc"/.test(page),
      ".borc/.tahsilat kartın tamamını boyuyor",
    );
  });

  it("dikey tarih: gün üstte, ay-yıl altta", () => {
    assert.ok(page.includes('className="tx-day"') && page.includes('className="tx-month"'));
    assert.ok(page.indexOf('className="tx-day"') < page.indexOf('className="tx-month"'));
  });

  it("koşan bakiye sunucudan gelir, ekranda toplanmaz", () => {
    assert.ok(page.includes("t.running_balance_try"), "koşan bakiye gösterilmiyor");
    assert.ok(
      page.includes("t.running_balance_try !== null"),
      "onaysız kayıtta (null) bakiye gizlenmiyor",
    );
    assert.ok(!/reduce\(|kümülatif/.test(page), "bakiye ekranda hesaplanıyor");
  });

  it("tutarlar ve bakiye mono + tabular (defterde rakam hizalanır)", () => {
    assert.match(css, /\.tx-amount\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
    assert.match(css, /\.tx-running b\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
  });

  it("kartlar arası boşluk ferah (>=12px)", () => {
    const blok = css.slice(css.indexOf(".tx-cards {"));
    assert.match(blok.slice(0, 260), /gap:\s*1[2-9]px/);
  });

  it("düzelt/sil menüsü ve alt eylemler korunur", () => {
    assert.ok(page.includes("<RowMenu"), "üç nokta menüsü kayboldu");
    assert.ok(page.includes("Düzelt") && page.includes("Sil"));
    assert.ok(page.includes('className="back"'), "← Defter butonu kayboldu");
    assert.ok(page.includes("Ekstre (PDF)"));
  });
});

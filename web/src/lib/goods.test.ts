/* Bağımlılıksız çalışır:  node --test web/src/lib/goods.test.ts
 * (bkz. running.test.ts — web'de DOM koşucusu yok, mantık saf.)
 *
 * Borç/tahsilat formundaki TEK akıllı ürün alanı: ayrıştırma, birim
 * algılama, koşan format, zorunlu fiyat ve otomatik fiyat hesabı. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  autoAmount,
  checkForm,
  goodsSummary,
  parseGoods,
  resolveGoods,
  type CatalogProduct,
} from "./goods.ts";

const KATALOG: CatalogProduct[] = [
  { name: "Saman", base_unit: "balya", unit_price: "75.00" },
  { name: "Arpa", base_unit: "kilo", unit_price: "18.50" },
  { name: "Yonca", base_unit: "balya", unit_price: null },
];

describe("ürün ayrıştırma", () => {
  it("çıplak sayı saman varsayar", () => {
    const g = resolveGoods("20", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.productName, "saman");
    assert.equal(g.unitName, "balya");
    assert.equal(g.isNewProduct, false);
  });

  it("'20 kg arpa' -> arpa / kilo", () => {
    const g = resolveGoods("20 kg arpa", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.unitName, "kilo");
    assert.equal(g.productName, "arpa");
  });

  it("'500 balya saman' -> saman / balya", () => {
    const g = resolveGoods("500 balya saman", KATALOG);
    assert.equal(g.qty, 500);
    assert.equal(g.unitName, "balya");
    assert.equal(g.productName, "saman");
  });

  it("kelime sırası serbest: 'arpa 20 kilo'", () => {
    const g = resolveGoods("arpa 20 kilo", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.unitName, "kilo");
    assert.equal(g.productName, "arpa");
  });

  it("bitişik yazım: '20kg arpa'", () => {
    const g = resolveGoods("20kg arpa", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.unitName, "kilo");
  });

  it("ondalık ve binlik ayırıcı", () => {
    assert.equal(parseGoods("2,5 ton arpa").qty, 2.5);
    assert.equal(parseGoods("1.500 balya saman").qty, 1500);
  });

  it("çok kelimeli ürün adı korunur", () => {
    assert.equal(parseGoods("10 çuval mısır kırması").product, "mısır kırması");
  });

  it("katalogda olmayan ürün yeni sayılır", () => {
    assert.equal(resolveGoods("10 kilo mısır", KATALOG).isNewProduct, true);
    assert.equal(resolveGoods("10 kilo arpa", KATALOG).isNewProduct, false);
  });

  it("adet yoksa hata verir, uydurmaz", () => {
    const g = parseGoods("arpa");
    assert.equal(g.qty, null);
    assert.match(g.error!, /Adet/);
  });

  it("ürün adına çıplak sayı karışmaz ('saman 15')", () => {
    const g = parseGoods("20 saman 15");
    assert.equal(g.product, "saman");
    assert.match(g.error!, /Anlaşılmadı/);
  });

  it("boş alan hatasızdır (tahsilatta düz para)", () => {
    const g = resolveGoods("   ", KATALOG);
    assert.equal(g.empty, true);
    assert.equal(g.error, null);
  });
});

describe("birim algılama", () => {
  for (const [yazim, birim] of [
    ["kg", "kilo"],
    ["kilo", "kilo"],
    ["kilogram", "kilo"],
    ["balya", "balya"],
    ["adet", "adet"],
    ["ton", "ton"],
    ["çuval", "çuval"],
    ["cuval", "çuval"],
    ["litre", "litre"],
    ["lt", "litre"],
  ] as const) {
    it(`"${yazim}" -> ${birim}`, () => {
      assert.equal(parseGoods(`20 ${yazim} arpa`).unit, birim);
    });
  }

  it("birim yazılmazsa ürünün kendi birimi kullanılır", () => {
    assert.equal(resolveGoods("20 arpa", KATALOG).unitName, "kilo");
    assert.equal(resolveGoods("20 yonca", KATALOG).unitName, "balya");
  });

  it("birim yazılmazsa ve ürün tanınmıyorsa dropdown yedeği kullanılır", () => {
    assert.equal(resolveGoods("20 mısır", KATALOG, "ton").unitName, "ton");
    assert.equal(resolveGoods("20 mısır", KATALOG).unitName, "balya");
  });

  it("yazıdaki birim dropdown yedeğini EZER", () => {
    assert.equal(resolveGoods("20 kg arpa", KATALOG, "ton").unitName, "kilo");
  });
});

describe("koşan format formda", () => {
  it("70-20-50 -> 20 balya saman, azalış borç", () => {
    const g = resolveGoods("70-20-50", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.running?.kind, "debt");
    assert.equal(g.productName, "saman");
    assert.equal(g.error, null);
  });

  it("70-30-100 artıştır (tahsilat)", () => {
    assert.equal(resolveGoods("70-30-100", KATALOG).running?.kind, "payment");
  });

  it("ürün ve birimle birlikte yazılabilir", () => {
    const g = resolveGoods("70-20-50 kg arpa", KATALOG);
    assert.equal(g.qty, 20);
    assert.equal(g.unitName, "kilo");
    assert.equal(g.productName, "arpa");
  });

  it("ayraç salt görseldir", () => {
    for (const metin of ["70-20-50", "70+20+50", "70*20*50", "70 - 20 - 50"]) {
      assert.equal(resolveGoods(metin, KATALOG).qty, 20, metin);
    }
  });

  it("running_mismatch: adet çözülmez, uyarı verilir", () => {
    const g = resolveGoods("70-25-50", KATALOG);
    assert.equal(g.qty, null);
    assert.match(g.error!, /fark 20 olmalı ama 25/);
  });

  it("özet ilk → son ve farkı gösterir", () => {
    assert.equal(goodsSummary(resolveGoods("70-20-50", KATALOG)), "70 → 50 · 20 balya saman (borç)");
    assert.equal(goodsSummary(resolveGoods("20 kg arpa", KATALOG)), "20 kilo arpa");
  });
});

describe("tutar zorunlu", () => {
  const g = resolveGoods("20 balya saman", KATALOG);

  it("boş tutarla kayıt yok", () => {
    assert.deepEqual(checkForm(g, "", { goodsRequired: true }), {
      ok: false,
      reason: "Tutar girin",
    });
  });

  it("sıfır tutarla kayıt yok", () => {
    assert.equal(checkForm(g, "0", { goodsRequired: true }).ok, false);
  });

  it("tutar dolu ve ürün çözülmüşse kaydedilir", () => {
    assert.equal(checkForm(g, "1500", { goodsRequired: true }).ok, true);
    assert.equal(checkForm(g, "1.500,50", { goodsRequired: true }).ok, true);
  });

  it("koşan format matematiği tutmuyorsa kayıt engellenir", () => {
    const bozuk = resolveGoods("70-25-50", KATALOG);
    const sonuc = checkForm(bozuk, "1500", { goodsRequired: true });
    assert.equal(sonuc.ok, false);
    assert.match(sonuc.reason!, /fark 20 olmalı/);
  });

  it("borçta ürün zorunlu, tahsilatta değil", () => {
    const bos = resolveGoods("", KATALOG);
    assert.equal(checkForm(bos, "2000", { goodsRequired: true }).ok, false);
    assert.equal(checkForm(bos, "2000", { goodsRequired: false }).ok, true);
    assert.equal(checkForm(bos, "", { goodsRequired: false }).ok, false);
  });
});

describe("otomatik fiyat", () => {
  it("kayıtlı birim fiyattan tutarı hesaplar", () => {
    assert.equal(autoAmount(resolveGoods("20 balya saman", KATALOG)), 1500);
    assert.equal(autoAmount(resolveGoods("10 kg arpa", KATALOG)), 185);
  });

  it("koşan formatta FARK üzerinden hesaplar", () => {
    assert.equal(autoAmount(resolveGoods("70-20-50", KATALOG)), 1500);
  });

  it("kuruşa yuvarlar", () => {
    assert.equal(autoAmount(resolveGoods("2,5 kg arpa", KATALOG)), 46.25);
  });

  it("ürünün kayıtlı fiyatı yoksa tik pasiftir", () => {
    const g = resolveGoods("20 balya yonca", KATALOG);
    assert.equal(g.catalogPrice, null);
    assert.equal(autoAmount(g), null);
  });

  it("birim ürünün birimiyle tutmuyorsa fiyat KULLANILMAZ", () => {
    // saman fiyatı balya başına; "20 kg saman"da balya fiyatı çarpılmaz.
    const g = resolveGoods("20 kg saman", KATALOG);
    assert.equal(g.catalogPrice, null);
    assert.equal(g.priceUnit, "balya");
    assert.equal(autoAmount(g), null);
  });

  it("adet çözülmediyse hesap yok", () => {
    assert.equal(autoAmount(resolveGoods("70-25-50", KATALOG)), null);
  });
});

describe("aynı satırda tutar", () => {
  it("'20 balya saman 5000 tl' tutarı önerir", () => {
    const g = parseGoods("20 balya saman 5000 tl");
    assert.equal(g.qty, 20);
    assert.equal(g.product, "saman");
    assert.equal(g.amountHint, 5000);
  });

  it("bitişik '5000tl' de anlaşılır", () => {
    assert.equal(parseGoods("20 saman 5000tl").amountHint, 5000);
  });

  it("tutar yazılmazsa öneri yok — uydurulmaz", () => {
    assert.equal(parseGoods("20 balya saman").amountHint, null);
  });
});

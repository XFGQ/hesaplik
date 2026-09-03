/* Bağımlılıksız çalışır:  node --test web/src/lib/actionBar.test.ts
 *
 * Alt eylem barının davranışı ağırlıklı olarak CSS'te yaşıyor (konum, opaklık,
 * içeriğin alt boşluğu, dokunma hedefleri). DOM koşucusu yok, o yüzden burada
 * doğrudan index.css ve Layout.tsx metni okunup SÖZLEŞME doğrulanıyor:
 * bunlar kullanıcının bildirdiği dört somut hatanın geri gelip gelmediğini
 * yakalar (bar saydamlaşırsa, buton yeniden serbest yüzerse, geri butonu
 * küçülürse test kırmızıya döner). */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const css = readFileSync(new URL("../index.css", import.meta.url), "utf8");
const layout = readFileSync(new URL("../components/Layout.tsx", import.meta.url), "utf8");

/** `{`/`}` sayarak bir bloğun gövdesini çıkarır (iç içe @media için gerekli). */
function blockAfter(text: string, startIndex: number): string {
  const open = text.indexOf("{", startIndex);
  let depth = 0;
  for (let i = open; i < text.length; i += 1) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(open + 1, i);
    }
  }
  throw new Error("kapanmayan blok: " + text.slice(startIndex, startIndex + 60));
}

/** Aynı sorguya sahip TÜM @media bloklarının gövdesi (dosyada birden fazla). */
function media(query: string): string {
  const parts: string[] = [];
  for (let i = css.indexOf(query); i !== -1; i = css.indexOf(query, i + 1)) {
    parts.push(blockAfter(css, i));
  }
  assert.ok(parts.length > 0, `@media bulunamadı: ${query}`);
  return parts.join("\n");
}

/** Mobil (<720px) ve masaüstü (>=720px) kuralları; ikisi de birden çok blok. */
const mobile = media("@media (max-width: 719px)");
const desktop = media("@media (min-width: 720px)");

/** @media'sız gövde: "scope" verilmeyince aranan yer burasıdır, yoksa bir
 *  media içindeki override asıl kuralın önüne geçebilir. */
const topLevel = (() => {
  let out = "";
  let i = 0;
  while (i < css.length) {
    const at = css.indexOf("@media", i);
    if (at === -1) return out + css.slice(i);
    out += css.slice(i, at);
    const body = blockAfter(css, at);
    i = css.indexOf(body, at) + body.length + 1;
  }
  return out;
})();

/** Bir seçicinin `sel {` yazımının dosyadaki ilk konumu (yoksa -1). */
function konum(selector: string, from = 0): number {
  const re = new RegExp(`(^|[},/*\\s])${selector.replace(/[.()\\-]/g, "\\$&")}\\s*\\{`, "m");
  const m = re.exec(css.slice(from));
  return m ? from + m.index : -1;
}

/** Mobil override, temel kuralı GERÇEKTEN ezmeli.
 *
 * Medya sorgusu özgüllük eklemez; aynı özgüllükteki iki kuraldan dosyada
 * SONRA gelen kazanır. Mobil blok temel tanımdan önce dursaydı değer CSS'te
 * doğru görünür ama tarayıcıda uygulanmazdı — bir kez tam olarak bu oldu
 * (mobil "Kişi ekle" 48px yazıyordu, 56px çiziliyordu). */
function mobilEzer(selector: string): void {
  const temel = konum(selector);
  const mobilBlok = css.lastIndexOf("@media (max-width: 719px)");
  const mobil = konum(selector, mobilBlok);
  assert.ok(temel >= 0 && mobil > temel, `${selector}: mobil kural temel kuralı ezmiyor`);
}

/** Bir kuralın gövdesi. `scope` verilmezse @media dışındaki tanım aranır. */
function rule(selector: string, scope = topLevel): string {
  const re = new RegExp(`(^|[},/*\\s])${selector.replace(/[.()\\-]/g, "\\$&")}\\s*\\{`, "m");
  const m = re.exec(scope);
  assert.ok(m, `kural bulunamadı: ${selector}`);
  return blockAfter(scope, m.index + m[0].length - 1);
}

/** Bir bildirimin sayısal değeri (px'li ya da z-index gibi birimsiz). */
function px(body: string, prop: string): number {
  const m = new RegExp(`(^|[;{\\s])${prop}\\s*:\\s*(\\d+)(?:px)?\\s*;`, "m").exec(body);
  assert.ok(m, `${prop} bulunamadı: ${body.slice(0, 80)}`);
  return Number(m[2]);
}

describe("alt eylem barı — opak ve sabit", () => {
  const bar = rule(".action-bar");

  it("ekranın altına sabitlenir", () => {
    assert.match(bar, /position:\s*fixed/);
    assert.match(bar, /bottom:\s*0/);
  });

  it("OPAKtır: arkasından kişi listesi görünmez", () => {
    /* Zemin bir tema değişkeni olmalı; rgba/transparent yarı saydamlık
       demektir ve kullanıcının bildirdiği hatanın ta kendisidir. */
    const zemin = /background:\s*var\(--(panel|paper|card)\)/.exec(bar);
    assert.ok(zemin, "bar zemini opak bir tema değişkeni değil");
    assert.doesNotMatch(bar, /background:[^;]*(transparent|rgba?\()/);
  });

  it("üstünde net ayrım var (çizgi + gölge)", () => {
    assert.match(bar, /border-top:\s*1px solid/);
    assert.match(bar, /box-shadow:/);
  });

  it("içerik barın ARKASINDAN geçmez: .side-main tam bar kadar boşluk bırakır", () => {
    assert.match(rule(".side-main"), /padding-bottom:\s*var\(--bar-space\)/);
    /* --bar-space bar yüksekliğinden türer, elle yazılmış bir sayı değil. */
    assert.match(css, /--bar-space:\s*calc\(var\(--bar-h\)\s*\+\s*env\(safe-area-inset-bottom\)\)/);
  });

  it("masaüstünde sol panelin (230px) üstüne binmez", () => {
    assert.match(rule(".action-bar", desktop), /left:\s*230px/);
  });
});

describe('"Kişi ekle" solda, sohbet balonu sağında', () => {
  it("yuvalar DOM'da bu sırada: önce ana eylem, sonra sohbet", () => {
    const main = layout.indexOf("action-bar-main");
    const side = layout.indexOf("action-bar-side");
    assert.ok(main > 0 && side > 0, "yuvalar Layout'ta yok");
    assert.ok(main < side, "sohbet balonu ana eylemin SOLUNDA kalmış");
  });

  it("ana eylem esner, sohbet balonu esnemez", () => {
    assert.match(rule(".action-bar-main"), /flex:\s*1/);
    assert.match(rule(".action-bar-side"), /flex:\s*0 0 56px/);
  });

  it("aralarında yanlış tıklamayı önleyecek boşluk var", () => {
    assert.ok(px(rule(".action-bar-inner"), "gap") >= 12);
    assert.ok(px(rule(".action-bar-inner", mobile), "gap") >= 12);
  });

  it("sohbet balonu serbest yüzmez, boyu 56px kare kalır", () => {
    const fab = rule(".chat-fab");
    assert.doesNotMatch(fab, /position:\s*fixed/);
    assert.equal(px(fab, "width"), 56);
    assert.equal(px(fab, "height"), 56);
    /* Balon mobilde de büyümez/küçülmez: <720px bloğunda yeniden boyu yok. */
    assert.doesNotMatch(mobile, /\.chat-fab\s*\{/);
  });

  it("sayfa butonları da serbest yüzmez (bar dışında fixed kalmaz)", () => {
    assert.doesNotMatch(rule(".fab"), /position:\s*fixed/);
    assert.doesNotMatch(rule(".fab-row"), /position:\s*fixed/);
  });
});

describe("dokunma hedefleri (HCI alt sınırı)", () => {
  it('"Kişi ekle" mobilde küçülür ama 48px altına inmez', () => {
    const masaustu = px(rule(".fab"), "min-height");
    const mobil = px(rule(".fab", mobile), "min-height");
    assert.ok(mobil < masaustu, "mobilde küçülmemiş");
    assert.ok(mobil >= 48, `mobil dokunma hedefi ${mobil}px < 48px`);
    mobilEzer(".fab");
  });

  it("mobil override'lar temel kuralların SONRASINDA tanımlı", () => {
    for (const sel of [".fab", ".fab-row", ".row-menu-trigger", ".chat-btn", ".chat-edit"]) {
      mobilEzer(sel);
    }
  });

  it('"← Defter" her iki ekranda da büyük ve çerçeveli', () => {
    const back = rule(".back");
    assert.match(back, /min-height:\s*var\(--tap\)/); /* --tap = 52px */
    assert.match(back, /border:\s*1px solid/);
    /* Mobilde küçültülmüyor: geri butonu her yerde aynı boyda. */
    assert.doesNotMatch(mobile, /\.back\s*\{/);
  });

  it("--tap 48px'in üstünde", () => {
    assert.ok(px(rule(":root"), "--tap") >= 48);
  });

  it("küçük ikonların dokunma alanı geniş (üç nokta, ✕, kalem)", () => {
    assert.ok(px(rule(".row-menu-trigger"), "height") >= 44);
    assert.ok(px(rule(".row-menu-trigger", mobile), "height") >= 48);
    assert.ok(px(rule(".side-icon-btn"), "height") >= 48);
    assert.ok(px(rule(".side-close"), "height") >= 48);
    assert.ok(px(rule(".chat-panel-min"), "height") >= 48);
  });

  it("satır menüsü seçenekleri ve bot şıkları parmakla seçilir", () => {
    assert.ok(px(rule(".row-menu-dropdown button"), "min-height") >= 48);
    assert.ok(px(rule(".chat-btn"), "min-height") >= 44);
    assert.ok(px(rule(".chat-btn", mobile), "min-height") >= 48);
  });

  it('üst bardaki metin bağlantılar ("Ekstre (PDF)") tam boy hedef', () => {
    assert.match(rule(".link"), /min-height:\s*var\(--tap\)/);
  });
});

describe("barın üstünde kalması gerekenler", () => {
  it("bildirimler (toast) barın arkasına düşmez", () => {
    const toast = rule(".toast-wrap");
    assert.match(toast, /bottom:\s*calc\(var\(--bar-space\)/);
    assert.ok(px(toast, "z-index") > px(rule(".action-bar"), "z-index"));
  });

  it("masaüstünde yüzen sohbet paneli barın üstünde biter", () => {
    assert.match(rule(".chat-panel", desktop), /var\(--bar-space\)/);
  });
});

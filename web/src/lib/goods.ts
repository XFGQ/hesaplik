/* Akıllı ürün girişi — borç/tahsilat formundaki TEK alan.
 *
 * Kullanıcı ürünü, adedi ve birimi ayrı ayrı doldurmaz; hepsini tek satıra
 * yazar ve burası çözer:
 *
 *   "20"                -> 20 balya saman   (ürün yazılmazsa saman)
 *   "20 kg arpa"        -> 20 kilo arpa
 *   "500 balya saman"   -> 500 balya saman
 *   "70-20-50"          -> koşan format: 20 balya saman (bkz. running.ts)
 *   "70-25-50"          -> matematik tutmuyor, kayıt engellenir
 *   "20 balya saman 5000 tl" -> tutar alanına 5000 önerilir
 *
 * Birim YAZIDAN algılanır (kg/kilo/kilogram -> kilo); dropdown yalnızca
 * yedektir. Birimler ayrı tutulur: 20 balya ile 20 kilo aynı şey değildir.
 *
 * Bu dosya app/services/parser.py'nin form karşılığıdır: aynı birim
 * kümesi, aynı koşan format kuralları (running.ts ikizi üzerinden). Kural
 * değişirse üçü birden değişir.
 * Testi: web/src/lib/goods.test.ts (`just web-test`).
 */
import { parseNumber } from "./format.ts";
import { parseRunning, runningError, type Running } from "./running.ts";

/** Ürün söylenmezse ne varsayılır (kullanıcının işi ağırlıklı saman). */
export const DEFAULT_PRODUCT = "saman";
/** Ne ürün ne birim bilinmiyorsa son çare birim. */
export const DEFAULT_UNIT = "balya";

/** Dropdown yedeğinde gösterilen birimler. */
export const BIRIMLER = ["balya", "kilo", "adet", "ton", "çuval", "litre", "gram", "paket"];

/* Yazım varyasyonları -> kanonik birim. parser.py'deki UNITS kümesiyle
 * aynı kelimeler; oradaki her yazım burada bir kanonik ada bağlanır. */
const UNIT_ALIASES: Record<string, string> = {
  balya: "balya",
  kg: "kilo",
  kilo: "kilo",
  kilogram: "kilo",
  gr: "gram",
  gram: "gram",
  ton: "ton",
  adet: "adet",
  tane: "adet",
  çuval: "çuval",
  cuval: "çuval",
  litre: "litre",
  lt: "litre",
  paket: "paket",
  koli: "koli",
  kasa: "kasa",
  teneke: "teneke",
  varil: "varil",
  metre: "metre",
  kutu: "kutu",
  torba: "torba",
  top: "top",
  çift: "çift",
  cift: "çift",
  düzine: "düzine",
  duzine: "düzine",
};

const CURRENCY = new Set(["tl", "try", "lira", "₺", "tl.", "tl,"]);

const NUM_RE = /^(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)$/;
/** Bitişik yazılmış sayı+kelime: "20kg", "500balya", "5000tl". */
const GLUED_RE = /^(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)([a-zçğıöşü₺.]+)$/;
/** Üç parçalı görünen ama koşan format olarak çözülemeyen token (tarih vb.). */
const TRIPLE_SHAPE = /^\d+(?:,\d+)?(?:[-+*]\d+(?:,\d+)?){2}$/;

function lower(text: string): string {
  return text.toLocaleLowerCase("tr");
}

function toNumber(token: string): number {
  return Number(parseNumber(token));
}

/** Metni tokenlara böler; ayraç etrafındaki boşluklar önce kapatılır ki
 *  "70 - 20 - 50" tek bir koşan format tokenı olsun (running.ts ile aynı
 *  "ayraç salt görseldir" kuralı). Bitişik sayı+birim de ayrılır. */
function tokenize(text: string): string[] {
  const collapsed = lower(text).replace(/\s*([-+*])\s*/g, "$1");
  const out: string[] = [];
  for (const raw of collapsed.split(/\s+/)) {
    const token = raw.trim();
    if (!token) continue;
    const glued = GLUED_RE.exec(token);
    if (glued && (UNIT_ALIASES[glued[2]] || CURRENCY.has(glued[2]))) {
      out.push(glued[1], glued[2]);
      continue;
    }
    out.push(token);
  }
  return out;
}

export type ParsedGoods = {
  /** Kullanıcı hiçbir şey yazmadı. */
  empty: boolean;
  /** Çözülen adet; koşan formatta üçlünün FARKI. Çözülemediyse null. */
  qty: number | null;
  /** Yazıdan algılanan birim; yazılmadıysa null (varsayılan sonra uygulanır). */
  unit: string | null;
  /** Yazılan ürün adı; yazılmadıysa null (varsayılan sonra uygulanır). */
  product: string | null;
  running: Running | null;
  /** Aynı satırda "... 5000 tl" yazıldıysa tutar önerisi. */
  amountHint: number | null;
  /** Kayıt engelleyen sorun (koşan format matematiği, çözülemeyen sayı). */
  error: string | null;
};

/** Tek akıllı alanın ham metnini çözer. Ürün/birim varsayılanları BURADA
 *  uygulanmaz — onlar kataloğu bilen `resolveGoods`'un işi. */
export function parseGoods(text: string): ParsedGoods {
  const bos: ParsedGoods = {
    empty: true,
    qty: null,
    unit: null,
    product: null,
    running: null,
    amountHint: null,
    error: null,
  };
  if (!text || !text.trim()) return bos;

  const tokens = tokenize(text);
  if (tokens.length === 0) return bos;

  let amountHint: number | null = null;
  let unit: string | null = null;
  let qty: number | null = null;
  let running: Running | null = null;
  let error: string | null = null;
  const rest: string[] = [];

  // 1. Para birimi ve hemen öncesindeki sayı tutardır, adet değil
  //    (CLAUDE.md > "Para vs adet").
  const kalan: string[] = [];
  for (const token of tokens) {
    if (CURRENCY.has(token)) {
      const prev = kalan[kalan.length - 1];
      if (amountHint === null && prev !== undefined && NUM_RE.test(prev)) {
        amountHint = toNumber(kalan.pop() as string);
      }
      continue;
    }
    kalan.push(token);
  }

  // 2. Adet: önce koşan format (üçlü), sonra düz sayı. Ardından birim
  //    gelirse o kullanılır; birim satırın herhangi bir yerinde de olabilir.
  for (const token of kalan) {
    if (qty === null) {
      const r = parseRunning(token);
      if (r) {
        running = r;
        qty = r.consistent ? r.qty : null;
        error = runningError(r);
        continue;
      }
      if (TRIPLE_SHAPE.test(token)) {
        error = `Adet anlaşılmadı: ${token}`;
        continue;
      }
      if (NUM_RE.test(token)) {
        qty = toNumber(token);
        continue;
      }
    }
    const kanonik = UNIT_ALIASES[token];
    if (kanonik && unit === null) {
      unit = kanonik;
      continue;
    }
    // Adet zaten bulunduysa ikinci bir çıplak sayı ürün adına KARIŞMAZ
    // ("saman 15" diye bir ürün açılmasın, CLAUDE.md > "Ürün yazım
    // düzeltme"): ne olduğu belirsiz, sorulur.
    if (NUM_RE.test(token) || TRIPLE_SHAPE.test(token)) {
      if (error === null) error = `Anlaşılmadı: ${token}`;
      continue;
    }
    rest.push(token);
  }

  const product = rest.join(" ").trim();
  if (qty === null && running === null && error === null) {
    error = product ? `Adet yazılmadı: ${product}` : `Adet anlaşılmadı: ${text.trim()}`;
  }

  return {
    empty: false,
    qty,
    unit,
    product: product || null,
    running,
    amountHint,
    error,
  };
}

export type CatalogProduct = { name: string; base_unit: string; unit_price: string | null };

export type ResolvedGoods = ParsedGoods & {
  /** Varsayılanlar uygulanmış hâli. */
  productName: string;
  unitName: string;
  /** Katalogda böyle bir ürün yok — kayıtta yeni ürün açılacak. */
  isNewProduct: boolean;
  /** Otomatik tutarın birim fiyatı. YALNIZCA birim fiyatın birimiyle
   *  aynıysa dolu olur: balya fiyatı kilo adediyle çarpılmaz. */
  catalogPrice: number | null;
  /** Fiyat var ama birim tutmuyor (uyarı metni için). */
  priceUnit: string | null;
  /** catalogPrice nereden geldi: "saman" = ayarlanabilir varsayılan saman
   *  fiyatı (CLAUDE.md > "Varsayılan saman fiyatı"), "katalog" = ürünün
   *  kayıtlı fiyat geçmişi. */
  priceSource: "saman" | "katalog" | null;
};

function validPrice(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value) && value > 0;
}

/** Ham metni katalogla birleştirip kaydedilebilir hâle getirir.
 *  `unitOverride` dropdown yedeğinden gelir; yazıda birim varsa çağıran
 *  taraf override'ı düşürür (yazı her zaman kazanır).
 *  `samanPrice` varsayılan saman balya fiyatıdır: ürün saman ise ürünün
 *  kayıtlı fiyat geçmişini EZER (güncel fiyat kullanıcının ayarladığıdır),
 *  diğer ürünlere hiç dokunmaz. */
export function resolveGoods(
  text: string,
  products: CatalogProduct[] | undefined,
  unitOverride?: string | null,
  samanPrice?: number | null,
): ResolvedGoods {
  const parsed = parseGoods(text);
  const productName = parsed.product ?? DEFAULT_PRODUCT;
  const key = lower(productName);
  const hit = products?.find((p) => lower(p.name) === key);

  const unitName = parsed.unit ?? unitOverride ?? hit?.base_unit ?? DEFAULT_UNIT;

  let price: number | null = null;
  let priceUnit: string | null = null;
  let source: "saman" | "katalog" | null = null;
  if (key === DEFAULT_PRODUCT && validPrice(samanPrice)) {
    price = samanPrice;
    priceUnit = DEFAULT_UNIT;
    source = "saman";
  } else {
    const kayitli = hit?.unit_price != null ? Number(hit.unit_price) : null;
    if (validPrice(kayitli)) {
      price = kayitli;
      priceUnit = hit!.base_unit;
      source = "katalog";
    }
  }
  const birimTutuyor = price !== null && priceUnit === unitName;

  return {
    ...parsed,
    productName,
    unitName,
    isNewProduct: products !== undefined && !parsed.empty && hit === undefined,
    catalogPrice: birimTutuyor ? price : null,
    priceUnit,
    priceSource: birimTutuyor ? source : null,
  };
}

/** Otomatik fiyat tiki kullanıcı dokunmadan AÇIK mı başlar? Yalnızca
 *  varsayılan saman fiyatında: saman için tutar yazılmazsa varsayılan
 *  fiyattan hesaplanır. Diğer ürünlerde kayıtlı fiyat tutarı bağlamaz
 *  (kural 4), tik kapalı başlar. */
export function defaultAutoPrice(g: ResolvedGoods): boolean {
  return g.priceSource === "saman";
}

/** Tik işaretliyken tutar: adet × kayıtlı birim fiyat, kuruşa yuvarlı.
 *  Fiyat yoksa/adet çözülmediyse null — tik pasif kalır. */
export function autoAmount(g: ResolvedGoods): number | null {
  if (g.catalogPrice === null || g.qty === null || g.qty <= 0) return null;
  return Math.round(g.qty * g.catalogPrice * 100) / 100;
}

/** Ürün alanının çözülmüş özeti: kullanıcı ne kaydedileceğini görsün. */
export function goodsSummary(g: ResolvedGoods): string {
  if (g.empty || g.qty === null) return "";
  const adet = String(g.qty).replace(".", ",");
  const ozet = `${adet} ${g.unitName} ${g.productName}`;
  if (!g.running) return ozet;
  const say = (n: number) => String(n).replace(".", ",");
  const yon = g.running.kind === "debt" ? "borç" : "tahsilat";
  return `${say(g.running.before)} → ${say(g.running.after)} · ${ozet} (${yon})`;
}

export type FormCheck = { ok: boolean; reason: string | null };

/** Kaydet butonunun tek karar noktası. Fiyat HER ZAMAN zorunlu (elle ya da
 *  otomatik tikle dolmuş olmalı); boş tutarla kayıt yok. */
export function checkForm(
  g: ResolvedGoods,
  amountRaw: string,
  opts: { goodsRequired: boolean },
): FormCheck {
  if (g.empty) {
    if (opts.goodsRequired) return { ok: false, reason: "Ürün ve adet yazın" };
  } else {
    if (g.error) return { ok: false, reason: g.error };
    if (g.qty === null || g.qty <= 0) return { ok: false, reason: "Adet anlaşılmadı" };
  }
  const tutar = Number(parseNumber(amountRaw));
  if (!Number.isFinite(tutar) || tutar <= 0) return { ok: false, reason: "Tutar girin" };
  return { ok: true, reason: null };
}

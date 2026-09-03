/* Mobil çekmecenin (sol panel) açılma/kapanma kuralı. Tek bir boolean ama
 * kuralın kendisi bir yerde yazılı dursun: hangi olay kapatır, hangisi
 * açabilir. Bileşenden ayrı durur ki DOM olmadan test edilebilsin
 * (chatNudge.ts ile aynı gerekçe, bkz. web/src/lib/sideDrawer.test.ts). */

/** Çekmecenin durumunu değiştirebilecek olaylar. */
export type DrawerEvent =
  /** Üstteki ☰: kapalıysa açar, açıksa kapatır. */
  | "toggle"
  /** Panelin içindeki ✕. */
  | "close"
  /** Karartmaya, yani panelin DIŞINA tıklama. */
  | "backdrop"
  /** Esc tuşu. */
  | "escape"
  /** Menüden bir yere gidildi (sayfa değişti). */
  | "navigate";

/* Yalnızca ☰ açabilir. Diğer her olay kapatır — kapalıyken gelirse de zaten
 * kapalı kalır (Esc'in ya da sayfa değişiminin menüyü AÇMASI şaşırtıcı olurdu). */
export function nextDrawerState(open: boolean, event: DrawerEvent): boolean {
  return event === "toggle" ? !open : false;
}

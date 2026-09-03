import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/* Alt eylem barı — tek bir OPAK, sabit şerit.
 *
 * Bar Layout'ta yaşar (her sayfada aynı yerde, aynı yükseklikte), ama içini
 * sayfalar doldurur: People "Kişi ekle", PersonDetail "Borç ekle / Tahsilat
 * ekle", ChatWidget da sağdaki sohbet balonunu. Eskiden bunların her biri
 * kendi `position: fixed` butonuydu; bu yüzden hem içerik altlarından geçip
 * gidiyordu (bar yüzer bir katman gibiydi, arkasından kişi isimleri
 * görünüyordu) hem de sohbet balonu "Kişi ekle"nin üstüne biniyordu. Tek bar
 * + iki yuva ikisini de bitirir: bar gerçekten opaktır ve içindekiler yan
 * yana DİZİLİR, üst üste binmez.
 *
 * Yuvalar DOM düğümü olarak paylaşılır (context) çünkü butonların sahibi
 * sayfa bileşenleridir — mantık (hangi modal açılır, ne kaydedilir) orada
 * kalsın, yalnızca çizildikleri yer değişsin. Düğüm ilk render'da henüz
 * yoktur; Layout onu callback ref ile state'e koyar, bu yüzden yuva
 * bileşenleri bir render boyunca null döner (tek kare, göze görünmez).
 *
 * Yerleşim: `main` esner ve kalan genişliği alır, `side` kendi boyunu korur
 * (sohbet balonu büyüyüp küçülmez). Aralarındaki boşluk CSS'te (.action-bar)
 * ve kasıtlıdır: iki farklı işlevli buton yanlışlıkla karışmasın. */
export type ActionBarSlots = {
  /** Sayfanın birincil eylemi; esner, kalan genişliği doldurur. */
  main: HTMLElement | null;
  /** Sabit boyutlu ikincil eylem (sohbet balonu); sağda durur. */
  side: HTMLElement | null;
};

const ActionBarContext = createContext<ActionBarSlots>({ main: null, side: null });

export const ActionBarProvider = ActionBarContext.Provider;

function Slot({ slot, children }: { slot: keyof ActionBarSlots; children: ReactNode }) {
  const target = useContext(ActionBarContext)[slot];
  return target ? createPortal(children, target) : null;
}

/** Sayfanın birincil eylem butonu(ları) — barın solunda, esneyen yuvada. */
export function ActionBarMain({ children }: { children: ReactNode }) {
  return <Slot slot="main">{children}</Slot>;
}

/** Sabit boyutlu ikincil eylem — barın sağında. */
export function ActionBarSide({ children }: { children: ReactNode }) {
  return <Slot slot="side">{children}</Slot>;
}

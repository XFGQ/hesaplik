import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { api } from "../api/client";
import type { ChatButton, ChatMessage } from "../api/types";
import type { NudgeKind } from "../lib/chatNudge";
import { isOutsideClick, startNudgeCycle } from "../lib/chatNudge";
import { useToast } from "../lib/toast";

type Bubble = {
  id: number;
  /* "note": sunucudan gelmeyen, yerel bilgi satırı ("İptal edildi.") — sohbet
   * geçmişinin parçası ama bir bot cevabı değil, soluk gösterilir. */
  from: "user" | "assistant" | "note";
  text: string;
  buttons: ChatButton[];
  reportPath: string | null;
};

let nextId = 1;

/* İkonlar dışarıdan kütüphaneyle değil elle çizilir (CLAUDE.md > arayüz
 * kuralları: harici bileşen kütüphanesi yok). currentColor kullanır, iki
 * temada da doğru renklenir; 20px net ve uzaktan seçilir. */
function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path d="M12 20V5M12 5l-6 6M12 5l6 6" fill="none" stroke="currentColor" strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" focusable="false">
      <path d="M4 20h4l10-10a2.8 2.8 0 0 0-4-4L4 16v4zM13.5 6.5l4 4" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* Sağ altta yüzen balon → sağdan panel (CLAUDE.md > "Web'e chat asistanı
 * ekle"). Telegram botuyla AYNI beyni (app/services/message_processor.py)
 * kullanan /api/chat + /api/chat/confirm uçlarını çağırır — buradaki tek iş
 * ProcessResult'tan üretilmiş JSON'u (ChatMessage[]) balon+buton olarak
 * göstermek. Küçültme (✕) paneli kapatır ama `bubbles` state'i Layout monte
 * kaldığı sürece (sayfa gezinmelerinde) korunur, geri açılınca geçmiş durur.
 *
 * Durdurma (⏹) ve mesaj düzenleme ağırlıklı olarak burada, frontend'de yaşar
 * (CLAUDE.md > "Web chat durdurma ve mesaj düzenleme"); sunucuda yalnızca
 * /api/chat/cancel var — o da bekleyen soruyu ve kuyrukta kalan parçaları
 * düşürür, deftere dokunmaz.
 *
 * Küçültmenin iki yolu var: başlıktaki ✕ ve panelin DIŞINA (boşluğa) tıklama.
 * İkisi de yalnızca `open`'ı false yapar — `bubbles` bu bileşende yaşadığı ve
 * bileşen Layout'ta monte kaldığı için geçmiş silinmez, sunucudaki bekleyen
 * soru da düşürülmez (küçültmek "iptal" değildir; iptal ⏹ butonudur). */
export default function ChatWidget() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  /* Bot son cevabında bir şey SORDU mu ("hangisi?", "Ekleyeyim mi?", "DUMAN
   * yaz")? Sunucuda `web_chat_pending` satırı bekliyor demektir; durdurma
   * butonu bu yüzden istek bitmişken de görünür. */
  const [pendingConfirm, setPendingConfirm] = useState(false);
  /* Düzenlenen KULLANICI balonunun id'si; null ise normal gönderme modu. */
  const [editingId, setEditingId] = useState<number | null>(null);
  /* Panel kapalıyken balonun kısa hatırlatma animasyonu; "intro" oturumda bir
   * kez (biraz daha belirgin), sonrası "soft". */
  const [nudge, setNudge] = useState<NudgeKind>("none");
  const introDoneRef = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [bubbles, open]);

  /* Boşluğa tıkla → küçült. Dinleyici `pointerdown`da: tıklanan öğe cevap
   * verirken (menü kapanması gibi) DOM'dan düşse bile hedef hâlâ paneldedir.
   * Paneli açan tıklama bu dinleyiciden ÖNCE bittiği için panel anında geri
   * kapanmaz. */
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (isOutsideClick(panelRef.current, e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  /* Hatırlatma yalnızca panel KAPALIYKEN döner; açılınca durur ve sıfırlanır
   * (açık panelin yanında kıpırdayan bir buton rahatsız eder). */
  useEffect(() => {
    if (open) {
      setNudge("none");
      return;
    }
    /* "Bir kez" işareti animasyon GERÇEKTEN oynayınca konur, zamanlayıcı
     * kurulunca değil: StrictMode geliştirmede efekti iki kez kurar, kurulumda
     * işaretlesek belirgin ilk hareket hiç görünmezdi. */
    return startNudgeCycle(
      (kind) => {
        if (kind === "intro") introDoneRef.current = true;
        setNudge(kind);
      },
      { intro: !introDoneRef.current },
    );
  }, [open]);

  function appendAssistant(messages: ChatMessage[]) {
    setBubbles((prev) => [
      ...prev,
      ...messages.map((m) => ({
        id: nextId++,
        from: "assistant" as const,
        text: m.reply,
        buttons: m.buttons,
        reportPath: m.report_path,
      })),
    ]);
    const last = messages[messages.length - 1];
    setPendingConfirm(!!last && (last.buttons.length > 0 || last.awaits_text));
  }

  function appendNote(text: string) {
    setBubbles((prev) => [...prev, { id: nextId++, from: "note", text, buttons: [], reportPath: null }]);
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setBubbles((prev) => [...prev, { id: nextId++, from: "user", text: trimmed, buttons: [], reportPath: null }]);
    setInput("");
    setEditingId(null);
    setSending(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await api.chat(trimmed, ctrl.signal);
      appendAssistant(res.messages);
    } catch {
      /* İptal edilen fetch de buraya düşer — kullanıcı zaten bilerek durdurdu,
       * hata gösterilmez (notu stop() yazar). */
      if (!ctrl.signal.aborted) toast("Mesaj gönderilemedi, tekrar dener misin?", "info");
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setSending(false);
    }
  }

  async function confirm(action: string) {
    if (sending) return;
    setSending(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await api.chatConfirm(action, ctrl.signal);
      appendAssistant(res.messages);
    } catch {
      if (!ctrl.signal.aborted) toast("İşlem yapılamadı, tekrar dener misin?", "info");
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setSending(false);
    }
  }

  /* Durdurma (⏹): süren isteği keser VE sunucudaki bekleyen soruyu düşürür.
   * İkisi tek butonda birleşir — kullanıcı için ayrım yok, "dur" demiştir.
   * Not: iptal isteği, yoldaki asıl istek sunucuda bitmeden varırsa o istek
   * yeni bir bekleyen soru bırakabilir; o zaman buton yeniden görünür ve
   * ikinci basış temizler. */
  async function stopCurrent() {
    const wasSending = abortRef.current !== null;
    abortRef.current?.abort();
    abortRef.current = null;
    setPendingConfirm(false);
    try {
      const res = await api.chatCancel();
      if (!wasSending) {
        appendAssistant(res.messages);
        return;
      }
    } catch {
      /* Sunucuya ulaşılamasa bile istemci tarafı durdu; not yine yazılır. */
    }
    appendNote("İptal edildi.");
  }

  function startEdit(b: Bubble) {
    if (sending) return;
    setEditingId(b.id);
    setInput(b.text);
    inputRef.current?.focus();
  }

  function cancelEdit() {
    setEditingId(null);
    setInput("");
  }

  /* Düzenlenen mesaj gönderilince o balon ve ONDAN SONRAKİ her şey (botun ona
   * verdiği cevap, sonraki tüm alışveriş) silinir; sohbet oradan yeniden
   * başlar — normal AI arayüzü davranışı. Silinen dalın sunucuda bıraktığı
   * bekleyen soru da düşürülür, yoksa yeni metin o soruya "cevap" sanılır. */
  async function submitEdit(id: number, text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setBubbles((prev) => {
      const i = prev.findIndex((b) => b.id === id);
      return i === -1 ? prev : prev.slice(0, i);
    });
    setEditingId(null);
    setPendingConfirm(false);
    try {
      await api.chatCancel();
    } catch {
      /* iptal edilecek bir şey yoksa/ulaşılamazsa gönderme yine denenir */
    }
    await send(trimmed);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (editingId !== null) submitEdit(editingId, input);
    else send(input);
  }

  if (!open) {
    return (
      <button
        className={`chat-fab${nudge === "none" ? "" : ` chat-fab-${nudge}`}`}
        onClick={() => setOpen(true)}
        aria-label="Sohbeti aç"
      >
        💬
      </button>
    );
  }

  return (
    <div className="chat-panel" role="complementary" aria-label="Sohbet asistanı" ref={panelRef}>
      <div className="chat-panel-bar">
        <h2>Asistan</h2>
        <button className="chat-panel-min" onClick={() => setOpen(false)} aria-label="Küçült">
          ✕
        </button>
      </div>

      <div className="chat-messages" ref={listRef}>
        {bubbles.length === 0 && (
          <p className="chat-empty">Borç ya da tahsilat yazabilirsin: "Ahmet 500 tl borç yazdım" gibi.</p>
        )}
        {bubbles.map((b) =>
          b.from === "user" ? (
            <div key={b.id} className="chat-row-user">
              <button
                className="chat-edit"
                type="button"
                onClick={() => startEdit(b)}
                disabled={sending}
                aria-label="Mesajı düzenle"
                title="Düzenle"
              >
                <PencilIcon />
              </button>
              <div className={`chat-bubble chat-bubble-user${editingId === b.id ? " chat-bubble-editing" : ""}`}>
                <p>{b.text}</p>
              </div>
            </div>
          ) : (
            <div key={b.id} className={`chat-bubble chat-bubble-${b.from}`}>
              <p>{b.text}</p>
              {b.reportPath && (
                <button
                  className="link chat-report-link"
                  onClick={() => api.openReportPath(b.reportPath as string).catch(() => toast("Rapor alınamadı", "info"))}
                >
                  PDF indir
                </button>
              )}
              {b.buttons.length > 0 && (
                <div className="chat-buttons">
                  {b.buttons.map((btn) => (
                    <button
                      key={btn.action}
                      className="chat-btn"
                      onClick={() => confirm(btn.action)}
                      disabled={sending}
                    >
                      {btn.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {sending && <p className="chat-typing muted">…</p>}
      </div>

      {editingId !== null && (
        <div className="chat-edit-bar">
          <span>Mesaj düzenleniyor</span>
          <button type="button" className="link" onClick={cancelEdit}>
            Vazgeç
          </button>
        </div>
      )}

      <form className="chat-input-row" onSubmit={onSubmit}>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && editingId !== null) cancelEdit();
          }}
          placeholder={editingId !== null ? "Mesajı düzelt..." : "Yaz..."}
          disabled={sending}
          aria-label="Mesaj"
        />
        {sending ? (
          <button
            type="button"
            className="chat-send chat-stop"
            onClick={stopCurrent}
            aria-label="Durdur"
            title="Durdur"
          >
            <StopIcon />
          </button>
        ) : (
          <>
            {pendingConfirm && (
              <button
                type="button"
                className="chat-send chat-stop"
                onClick={stopCurrent}
                aria-label="Bekleyen işlemi iptal et"
                title="İptal et"
              >
                <StopIcon />
              </button>
            )}
            <button
              type="submit"
              className="chat-send"
              disabled={!input.trim()}
              aria-label={editingId !== null ? "Kaydet ve gönder" : "Gönder"}
              title={editingId !== null ? "Kaydet ve gönder" : "Gönder"}
            >
              <SendIcon />
            </button>
          </>
        )}
      </form>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { api } from "../api/client";
import type { ChatButton, ChatMessage } from "../api/types";
import { useToast } from "../lib/toast";

type Bubble = {
  id: number;
  from: "user" | "assistant";
  text: string;
  buttons: ChatButton[];
  reportPath: string | null;
};

let nextId = 1;

/* Sağ altta yüzen balon → sağdan panel (CLAUDE.md > "Web'e chat asistanı
 * ekle"). Telegram botuyla AYNI beyni (app/services/message_processor.py)
 * kullanan /api/chat + /api/chat/confirm uçlarını çağırır — buradaki tek iş
 * ProcessResult'tan üretilmiş JSON'u (ChatMessage[]) balon+buton olarak
 * göstermek. Küçültme (✕) paneli kapatır ama `bubbles` state'i Layout monte
 * kaldığı sürece (sayfa gezinmelerinde) korunur, geri açılınca geçmiş durur. */
export default function ChatWidget() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [bubbles, open]);

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
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setBubbles((prev) => [...prev, { id: nextId++, from: "user", text: trimmed, buttons: [], reportPath: null }]);
    setInput("");
    setSending(true);
    try {
      const res = await api.chat(trimmed);
      appendAssistant(res.messages);
    } catch {
      toast("Mesaj gönderilemedi, tekrar dener misin?", "info");
    } finally {
      setSending(false);
    }
  }

  async function confirm(action: string) {
    if (sending) return;
    setSending(true);
    try {
      const res = await api.chatConfirm(action);
      appendAssistant(res.messages);
    } catch {
      toast("İşlem yapılamadı, tekrar dener misin?", "info");
    } finally {
      setSending(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  if (!open) {
    return (
      <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Sohbeti aç">
        💬
      </button>
    );
  }

  return (
    <div className="chat-panel" role="complementary" aria-label="Sohbet asistanı">
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
        {bubbles.map((b) => (
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
        ))}
        {sending && <p className="chat-typing muted">…</p>}
      </div>

      <form className="chat-input-row" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Yaz..."
          disabled={sending}
          aria-label="Mesaj"
        />
        <button type="submit" className="chat-send" disabled={sending || !input.trim()} aria-label="Gönder">
          ↑
        </button>
      </form>
    </div>
  );
}

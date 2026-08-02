/* Admin paneli (/admin) — Faz 7 iskeleti.
 *
 * Sol menü + sağ içerik. Şimdilik yalnızca "İşlem Akışı" dolu; kalan altı
 * bölüm menüde görünür ama "Yakında" der (yer tutuyorlar, sırayla
 * doldurulacak — bkz. admin.md).
 *
 * Oturum: şifre doğruysa sunucu httpOnly çerez bırakır. Sayfa açılışında
 * /me sorulur; 401 ise şifre ekranı gösterilir. Token JS'te tutulmaz.
 */

import { useEffect, useState } from "react";

import { adminApi, Unauthorized } from "../api/admin";
import AdminFlow from "../components/admin/AdminFlow";

type SectionId =
  | "flow"
  | "health"
  | "llm"
  | "queue"
  | "logs"
  | "data"
  | "controls";

type Section = { id: SectionId; label: string; hint: string; ready?: boolean };

const SECTIONS: Section[] = [
  { id: "flow", label: "İşlem Akışı", hint: "Ne yazıldı → ne algılandı → ne yapıldı", ready: true },
  { id: "health", label: "Sistem Sağlığı", hint: "API, bot, veritabanı, Ollama durumu" },
  { id: "llm", label: "LLM İzleme", hint: "Çağrılar, süreler, başarı oranı" },
  { id: "queue", label: "İstek Kuyruğu", hint: "Bekleyen ve yarım kalan istekler" },
  { id: "logs", label: "Loglar", hint: "Bot ve API kayıtları" },
  { id: "data", label: "Kişiler & İşlemler", hint: "Veri yönetimi, arşiv" },
  { id: "controls", label: "Kontroller", hint: "LLM aç/kapat, model, yedek" },
];

export default function Admin() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [section, setSection] = useState<SectionId>("flow");

  useEffect(() => {
    let iptal = false;
    adminApi
      .me()
      .then(() => !iptal && setAuthed(true))
      .catch(() => !iptal && setAuthed(false));
    return () => {
      iptal = true;
    };
  }, []);

  if (authed === null) {
    return (
      <div className="admin-gate">
        <p className="muted">Yükleniyor…</p>
      </div>
    );
  }

  if (!authed) return <AdminLogin onSuccess={() => setAuthed(true)} />;

  const active = SECTIONS.find((s) => s.id === section)!;

  async function cikis() {
    await adminApi.logout().catch(() => undefined);
    setAuthed(false);
  }

  return (
    <div className="admin">
      <aside className="admin-side">
        <div className="admin-brand">
          <span className="admin-brand-name">Hesaplık</span>
          <span className="admin-brand-tag">yönetim</span>
        </div>

        <nav className="admin-nav">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={`admin-nav-item ${s.id === section ? "admin-nav-active" : ""}`}
              onClick={() => setSection(s.id)}
            >
              <span>{s.label}</span>
              {!s.ready && <span className="admin-soon">yakında</span>}
            </button>
          ))}
        </nav>

        <div className="admin-side-foot">
          <a className="admin-side-link" href="/">
            ← Deftere dön
          </a>
          <button className="admin-side-link" onClick={cikis}>
            Çıkış yap
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <header className="admin-head">
          <h1>{active.label}</h1>
          <p>{active.hint}</p>
        </header>

        {section === "flow" ? <AdminFlow onUnauthorized={() => setAuthed(false)} /> : <Soon />}
      </main>
    </div>
  );
}

function Soon() {
  return (
    <div className="admin-soon-box">
      <h2>Yakında</h2>
      <p>Bu bölüm henüz doldurulmadı. İlk gerçek içerik "İşlem Akışı" bölümünde.</p>
    </div>
  );
}

function AdminLogin({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await adminApi.login(password);
      onSuccess();
    } catch (err) {
      setError(
        err instanceof Unauthorized
          ? "Şifre hatalı."
          : err instanceof Error
            ? err.message
            : "Giriş yapılamadı.",
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-gate">
      <form className="admin-gate-box" onSubmit={submit}>
        <h1>Yönetim paneli</h1>
        <p className="muted">Devam etmek için yönetim şifresini girin.</p>

        <label className="field">
          <span>Şifre</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
          />
        </label>

        {error && <p className="error">{error}</p>}

        <button className="primary" type="submit" disabled={busy || !password}>
          {busy ? "Kontrol ediliyor…" : "Giriş"}
        </button>

        <a className="admin-side-link" href="/">
          ← Deftere dön
        </a>
      </form>
    </div>
  );
}

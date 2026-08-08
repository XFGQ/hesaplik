/* Admin paneli (/admin) — Faz 7 iskeleti.
 *
 * Sol menü + sağ içerik. "İşlem Akışı", "Sistem Sağlığı", "Yedekleme" ve
 * "LLM Yönetimi" dolu; kalan bölümler menüde görünür ama "Yakında" der
 * (yer tutuyorlar, sırayla doldurulacak — bkz. admin.md).
 *
 * Oturum: şifre doğruysa sunucu httpOnly çerez bırakır. Sayfa açılışında
 * /me sorulur; 401 ise şifre ekranı gösterilir. Token JS'te tutulmaz.
 *
 * LLM Yönetimi ayrı bir uca gider (/api/admin/llm, X-Admin-Password
 * header) — cookie oturumundan bağımsız bir mekanizma, bu yüzden bölüme
 * girince kendi şifresini ayrıca ister.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { adminApi, Unauthorized } from "../api/admin";
import { api, ApiError } from "../api/client";
import type { AdminLLMStatus } from "../api/types";
import AdminBackups from "../components/admin/AdminBackups";
import AdminFlow from "../components/admin/AdminFlow";
import AdminHealth from "../components/admin/AdminHealth";
import { useToast } from "../lib/toast";

type SectionId =
  | "flow"
  | "health"
  | "backups"
  | "llm"
  | "queue"
  | "logs"
  | "data"
  | "controls";

type Section = { id: SectionId; label: string; hint: string; ready?: boolean };

const SECTIONS: Section[] = [
  { id: "flow", label: "İşlem Akışı", hint: "Ne yazıldı → ne algılandı → ne yapıldı", ready: true },
  {
    id: "health",
    label: "Sistem Sağlığı",
    hint: "API, bot, veritabanı, Ollama, yedek durumu",
    ready: true,
  },
  {
    id: "backups",
    label: "Yedekleme",
    hint: "Tüm yedekler ve geri yükleme",
    ready: true,
  },
  {
    id: "llm",
    label: "LLM Yönetimi",
    hint: "Kaynak durumu, vLLM/Ollama tercihi",
    ready: true,
  },
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

        {section === "flow" && <AdminFlow onUnauthorized={() => setAuthed(false)} />}
        {section === "health" && <AdminHealth onUnauthorized={() => setAuthed(false)} />}
        {section === "backups" && <AdminBackups onUnauthorized={() => setAuthed(false)} />}
        {section === "llm" && <LlmSection />}
        {!active.ready && <Soon />}
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

/* LLM Yönetimi /api/admin/llm ucunu kullanır: cookie oturumundan bağımsız,
 * her istekte X-Admin-Password header'ı ister. Bu yüzden bölüme girince
 * kendi mini şifre formunu gösterir (aynı şifre, ayrı doğrulama). */
function LlmSection() {
  const [password, setPassword] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setChecking(true);
    try {
      await api.adminLlmStatus(input);
      setPassword(input);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Giriş başarısız");
    } finally {
      setChecking(false);
    }
  }

  if (password === null) {
    return (
      <form className="panel" onSubmit={handleLogin}>
        <label className="field">
          <span>Admin şifresi</span>
          <input
            type="password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            autoFocus
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="primary" type="submit" disabled={checking || !input}>
          Giriş
        </button>
      </form>
    );
  }

  return <LlmPanel password={password} />;
}

const PREFERENCES: { value: string; label: string }[] = [
  { value: "auto", label: "Otomatik" },
  { value: "vllm", label: "vLLM zorla" },
  { value: "ollama", label: "Ollama zorla" },
  { value: "none", label: "Kapalı" },
];

const SOURCE_LABEL: Record<AdminLLMStatus["active"], string> = {
  vllm: "vLLM",
  ollama: "Ollama",
  none: "Kapalı",
};

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`status-dot ${ok ? "status-dot-ok" : "status-dot-down"}`} />;
}

function LlmPanel({ password }: { password: string }) {
  const qc = useQueryClient();
  const toast = useToast();

  const status = useQuery({
    queryKey: ["admin-llm", password],
    queryFn: () => api.adminLlmStatus(password),
    refetchInterval: 10_000,
  });

  const setPrimary = useMutation({
    mutationFn: (llmPrimary: string) => api.adminSetLlmPrimary(password, llmPrimary),
    onSuccess: (data) => {
      qc.setQueryData(["admin-llm", password], data);
      toast("LLM tercihi güncellendi", "success");
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Güncelleme başarısız", "info"),
  });

  if (status.isLoading) return <p className="muted">Yükleniyor…</p>;
  if (status.error) {
    return (
      <div className="error">
        {status.error instanceof ApiError ? status.error.message : "Durum alınamadı"}
      </div>
    );
  }

  const data = status.data!;

  return (
    <>
      <div className="panel">
        <p className="panel-title">Şu an aktif</p>
        <div className="admin-active">
          <StatusDot ok={data.active !== "none"} />
          <strong>{SOURCE_LABEL[data.active]}</strong>
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Kaynak durumu</p>
        <div className="admin-source">
          <span className="admin-source-name">
            <StatusDot ok={data.vllm.ok} />
            vLLM (Bosna)
          </span>
          <span className="admin-source-meta">{data.vllm.ok ? data.vllm.model : "erişilemiyor"}</span>
        </div>
        <div className="admin-source">
          <span className="admin-source-name">
            <StatusDot ok={data.ollama.ok} />
            Ollama (İzmir)
          </span>
          <span className="admin-source-meta">
            {data.ollama.ok ? data.ollama.model : "erişilemiyor"}
          </span>
        </div>
      </div>

      <div className="panel">
        <p className="panel-title">Tercih</p>
        <div className="admin-pref">
          {PREFERENCES.map((p) => (
            <button
              key={p.value}
              aria-pressed={data.primary === p.value}
              disabled={setPrimary.isPending}
              onClick={() => setPrimary.mutate(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {data.primary === "ollama" && (
          <p className="hint" style={{ marginTop: 10 }}>
            vLLM'e hiç dokunulmuyor — GPU'yu kendiniz kullanabilirsiniz.
          </p>
        )}
      </div>
    </>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "../api/client";
import type { AdminLLMStatus } from "../api/types";
import { useToast } from "../lib/toast";

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

export default function Admin() {
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
      <div className="admin-page">
        <h1 className="admin-title">Yönetim</h1>
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
      </div>
    );
  }

  return (
    <div className="admin-page">
      <h1 className="admin-title">LLM Yönetimi</h1>
      <LlmPanel password={password} />
    </div>
  );
}

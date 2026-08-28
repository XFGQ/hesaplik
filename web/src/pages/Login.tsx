import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { login } from "../api/client";
import { setToken } from "../lib/auth";

export default function Login() {
  const nav = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(username, password);
      setToken(result.access_token);
      const dest = (location.state as { from?: string } | null)?.from ?? "/";
      nav(dest, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Giriş yapılamadı.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-gate">
      <form className="admin-gate-box" onSubmit={submit}>
        <h1>Hesaplık</h1>
        <p className="muted">Devam etmek için giriş yapın.</p>

        <label className="field">
          <span>Kullanıcı adı</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>

        <label className="field">
          <span>Şifre</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        {error && <p className="error">{error}</p>}

        <button className="primary" type="submit" disabled={busy || !username || !password}>
          {busy ? "Kontrol ediliyor…" : "Giriş"}
        </button>
      </form>
    </div>
  );
}

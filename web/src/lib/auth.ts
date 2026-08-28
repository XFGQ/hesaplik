/* Tek hesap girişi: JWT localStorage'da tutulur (gerçek site, HTTPS var —
 * bkz. CLAUDE.md). API istemcileri (client.ts, admin.ts) her istekte
 * `authHeader()`'ı ekler; 401 gelirse `onUnauthorized()` token'ı silip
 * girişe döner. */

const TOKEN_KEY = "hesaplik_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Token süresi dolmuş/geçersiz: temizle, girişe dön. Sekmedeki tüm React
 * state'i sıfırlaması gerektiğinden (React Query önbelleği dahil) sert
 * yönlendirme kullanılır — istemci tarafı router'a bağımlı kalınmaz. */
export function onUnauthorized(): void {
  clearToken();
  if (location.pathname !== "/login") {
    location.assign("/login");
  }
}

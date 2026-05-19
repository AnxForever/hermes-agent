// Thin client for the Hermes data-plane API. All requests go through the
// Vite proxy in dev (/v1 → http://127.0.0.1:8642), and direct in prod
// (nginx fronts both /v1 and the static bundle).

const TOKEN_KEY = "hermes.access_token";
const USER_KEY = "hermes.user";

export interface UserInfo {
  user_id: string;
  handle: string;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  expires_at: number;
  user: UserInfo;
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): UserInfo | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as UserInfo) : null;
  } catch {
    return null;
  }
}

export function setStoredCredentials(token: string, user: UserInfo): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearStoredCredentials(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function jsonFetch<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getStoredToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  login: (handle: string, password: string) =>
    jsonFetch<LoginResponse>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ handle, password }),
    }),
  me: () =>
    jsonFetch<{
      user_id: string;
      handle: string;
      role: string;
      auth_source: string;
    }>("/v1/auth/me"),
  logout: () =>
    jsonFetch<{ status: string }>("/v1/auth/logout", { method: "POST" }),
};

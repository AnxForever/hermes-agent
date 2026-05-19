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

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface SkillEntry {
  name: string;
  description?: string;
  enabled: boolean;
  global_default?: boolean;
  override?: boolean;
}

export interface UploadResult {
  path: string;
  size: number;
  original_name: string;
  extension: string;
}

/**
 * Stream a chat completion response. Yields content deltas one at a time
 * until the server emits ``data: [DONE]\n\n``. Throws on HTTP errors
 * (caller wraps the call in try/catch to surface them in the UI).
 *
 * The protocol is standard OpenAI Chat Completions streaming:
 *   data: {"choices": [{"delta": {"content": "..."}}]}\n\n
 *   ...
 *   data: [DONE]\n\n
 */
export async function* streamChatCompletion(
  messages: ChatMessage[],
  options: { model?: string; signal?: AbortSignal } = {},
): AsyncGenerator<string, void, unknown> {
  const token = getStoredToken();
  if (!token) {
    throw new Error("not authenticated");
  }
  const res = await fetch("/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      model: options.model ?? "hermes-agent",
      messages,
      stream: true,
    }),
    signal: options.signal,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${body}`);
  }
  if (!res.body) {
    throw new Error("response body missing");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by \n\n. Anything before the last \n\n
    // is complete; the rest stays in the buffer for the next chunk.
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      sep = buffer.indexOf("\n\n");

      for (const rawLine of frame.split("\n")) {
        const line = rawLine.trim();
        if (!line || !line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          const parsed = JSON.parse(data);
          const delta = parsed?.choices?.[0]?.delta?.content;
          if (typeof delta === "string" && delta.length > 0) {
            yield delta;
          }
        } catch {
          // ignore malformed frame
        }
      }
    }
  }
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
  listSkills: () => jsonFetch<{ skills: SkillEntry[] }>("/v1/skills"),
  toggleSkill: (name: string, enabled: boolean) =>
    jsonFetch<{ name: string; enabled: boolean }>(
      `/v1/skills/${encodeURIComponent(name)}/toggle`,
      { method: "POST", body: JSON.stringify({ enabled }) },
    ),
  uploadFile: async (file: File): Promise<UploadResult> => {
    const token = getStoredToken();
    if (!token) throw new Error("not authenticated");
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/v1/uploads", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${body}`);
    }
    return res.json() as Promise<UploadResult>;
  },
};

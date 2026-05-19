import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  api,
  clearStoredCredentials,
  getStoredToken,
  getStoredUser,
  type UserInfo,
} from "@/lib/api";

export default function ChatPage() {
  const navigate = useNavigate();
  const [user, setUser] = useState<UserInfo | null>(() => getStoredUser());

  useEffect(() => {
    if (!getStoredToken()) {
      navigate("/login", { replace: true });
      return;
    }
    // Re-validate the stored token. If it expired, bounce to login.
    api
      .me()
      .then((u) => setUser({ user_id: u.user_id, handle: u.handle, role: u.role }))
      .catch(() => {
        clearStoredCredentials();
        navigate("/login", { replace: true });
      });
  }, [navigate]);

  async function onLogout() {
    try {
      await api.logout();
    } catch {
      // ignore — server is stateless
    }
    clearStoredCredentials();
    navigate("/login", { replace: true });
  }

  return (
    <main className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-neutral-800 bg-neutral-950 px-6 py-3">
        <div className="text-sm font-semibold">Hermes Chat</div>
        <div className="flex items-center gap-3 text-xs text-neutral-400">
          {user ? (
            <>
              <span>
                @{user.handle} · {user.role}
              </span>
              <button
                type="button"
                onClick={onLogout}
                className="rounded border border-neutral-700 px-2 py-1 hover:border-neutral-500"
              >
                Sign out
              </button>
            </>
          ) : (
            <span>Loading…</span>
          )}
        </div>
      </header>

      <section className="flex flex-1 items-center justify-center px-6 text-center">
        <div className="max-w-md space-y-2">
          <h2 className="text-xl font-semibold">Scaffold ready.</h2>
          <p className="text-sm text-neutral-400">
            Chat surface (SSE + uploads + skill drawer) lands in a follow-up
            commit. This page already verifies your JWT against
            <code className="mx-1 rounded bg-neutral-900 px-1.5 py-0.5 text-xs">
              /v1/auth/me
            </code>
            and lets you sign out cleanly.
          </p>
        </div>
      </section>
    </main>
  );
}

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, setStoredCredentials } from "@/lib/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [handle, setHandle] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api.login(handle.trim(), password);
      setStoredCredentials(res.access_token, res.user);
      navigate("/chat", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-neutral-800 bg-neutral-950 p-6 shadow-xl"
      >
        <h1 className="text-2xl font-semibold tracking-tight">
          Hermes Chat
        </h1>
        <p className="text-sm text-neutral-400">Sign in to start chatting.</p>

        <label className="block text-sm">
          <span className="text-neutral-300">Handle</span>
          <input
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            autoComplete="username"
            required
            className="mt-1 w-full rounded border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
        </label>

        <label className="block text-sm">
          <span className="text-neutral-300">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className="mt-1 w-full rounded border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
        </label>

        {error ? (
          <div className="rounded border border-red-700/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-white px-3 py-2 text-sm font-medium text-neutral-900 transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

/**
 * Admin Users page — manages the local Hermes user/admin accounts that
 * back /v1/auth/login and the dashboard's /api/admin/login. Talks to the
 * dashboard FastAPI (which reads the same SQLite users table the gateway
 * does), so changes show up on both surfaces immediately.
 *
 * Self-protection rules echo the backend:
 *   - can't disable, demote, or delete the currently-logged-in admin
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Plus, Trash2, X } from "lucide-react";

import { api, type AdminUser } from "@/lib/api";

function formatTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

interface CreateFormState {
  handle: string;
  password: string;
  role: string;
}

const EMPTY_FORM: CreateFormState = { handle: "", password: "", role: "user" };

export default function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [meHandle, setMeHandle] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(EMPTY_FORM);
  const [busyUser, setBusyUser] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.adminListUsers();
      setUsers(res.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // Best-effort lookup of "who am I" so self-rows can be flagged.
    fetch("/api/admin/me", {
      headers: window.__HERMES_SESSION_TOKEN__
        ? { "X-Hermes-Session-Token": window.__HERMES_SESSION_TOKEN__ }
        : {},
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && data.handle) setMeHandle(data.handle);
      })
      .catch(() => {});
  }, [refresh]);

  const onCreate = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!createForm.handle.trim() || !createForm.password) return;
      setBusyUser("__new__");
      try {
        await api.adminCreateUser(
          createForm.handle.trim(),
          createForm.password,
          createForm.role,
        );
        setCreateForm(EMPTY_FORM);
        setCreating(false);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create user");
      } finally {
        setBusyUser(null);
      }
    },
    [createForm, refresh],
  );

  const onToggleDisabled = useCallback(
    async (user: AdminUser) => {
      setBusyUser(user.user_id);
      try {
        await api.adminPatchUser(user.user_id, { disabled: !user.disabled });
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update");
      } finally {
        setBusyUser(null);
      }
    },
    [refresh],
  );

  const onChangeRole = useCallback(
    async (user: AdminUser, role: string) => {
      if (role === user.role) return;
      setBusyUser(user.user_id);
      try {
        await api.adminPatchUser(user.user_id, { role });
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update");
      } finally {
        setBusyUser(null);
      }
    },
    [refresh],
  );

  const onResetPassword = useCallback(
    async (user: AdminUser) => {
      const password = window.prompt(
        `Set a new password for @${user.handle}:`,
      );
      if (!password) return;
      setBusyUser(user.user_id);
      try {
        await api.adminPatchUser(user.user_id, { password });
        setError(null);
        window.alert(`Password reset for @${user.handle}.`);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to reset password",
        );
      } finally {
        setBusyUser(null);
      }
    },
    [],
  );

  const onDelete = useCallback(
    async (user: AdminUser) => {
      if (!window.confirm(`Delete @${user.handle}? This cannot be undone.`))
        return;
      setBusyUser(user.user_id);
      try {
        await api.adminDeleteUser(user.user_id);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete user");
      } finally {
        setBusyUser(null);
      }
    },
    [refresh],
  );

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <div className="text-sm text-neutral-400">
          {loading
            ? "Loading…"
            : `${users.length} user${users.length === 1 ? "" : "s"}`}
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm hover:border-neutral-500"
        >
          <Plus size={14} /> New user
        </button>
      </div>

      {error ? (
        <div className="rounded border border-red-700/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      ) : null}

      {creating ? (
        <form
          onSubmit={onCreate}
          className="rounded-lg border border-neutral-800 bg-neutral-950 p-4"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Create user</h3>
            <button
              type="button"
              onClick={() => {
                setCreating(false);
                setCreateForm(EMPTY_FORM);
              }}
              className="text-neutral-500 hover:text-neutral-200"
              aria-label="Cancel"
            >
              <X size={14} />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <label className="text-xs">
              <span className="text-neutral-300">Handle</span>
              <input
                value={createForm.handle}
                onChange={(e) =>
                  setCreateForm((s) => ({ ...s, handle: e.target.value }))
                }
                autoComplete="off"
                required
                className="mt-1 w-full rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:border-neutral-500"
              />
            </label>
            <label className="text-xs">
              <span className="text-neutral-300">Password</span>
              <input
                type="password"
                value={createForm.password}
                onChange={(e) =>
                  setCreateForm((s) => ({ ...s, password: e.target.value }))
                }
                autoComplete="new-password"
                required
                className="mt-1 w-full rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:border-neutral-500"
              />
            </label>
            <label className="text-xs">
              <span className="text-neutral-300">Role</span>
              <select
                value={createForm.role}
                onChange={(e) =>
                  setCreateForm((s) => ({ ...s, role: e.target.value }))
                }
                className="mt-1 w-full rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:border-neutral-500"
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </label>
          </div>
          <div className="mt-3 flex justify-end">
            <button
              type="submit"
              disabled={busyUser === "__new__"}
              className="rounded bg-white px-3 py-1.5 text-sm font-medium text-neutral-900 hover:bg-neutral-100 disabled:opacity-50"
            >
              {busyUser === "__new__" ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      ) : null}

      <div className="overflow-x-auto rounded-lg border border-neutral-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-950 text-xs uppercase text-neutral-400">
            <tr>
              <th className="px-3 py-2">Handle</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2">Last seen</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isSelf = u.handle === meHandle;
              const rowBusy = busyUser === u.user_id;
              return (
                <tr
                  key={u.user_id}
                  className="border-t border-neutral-800 hover:bg-neutral-950/60"
                >
                  <td className="px-3 py-2 font-medium">
                    @{u.handle}
                    {isSelf ? (
                      <span className="ml-2 rounded bg-neutral-800 px-1.5 py-0.5 text-[10px] uppercase text-neutral-400">
                        you
                      </span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={u.role}
                      onChange={(e) => onChangeRole(u, e.target.value)}
                      disabled={isSelf || rowBusy}
                      className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1 text-xs outline-none disabled:opacity-50"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {u.disabled ? (
                      <span className="rounded bg-amber-950/40 px-1.5 py-0.5 text-amber-400">
                        disabled
                      </span>
                    ) : (
                      <span className="rounded bg-emerald-950/40 px-1.5 py-0.5 text-emerald-400">
                        active
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-400">
                    {formatTs(u.created_at)}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-400">
                    {formatTs(u.last_seen)}
                  </td>
                  <td className="px-3 py-2 text-right text-xs">
                    <button
                      type="button"
                      onClick={() => onResetPassword(u)}
                      disabled={rowBusy}
                      className="rounded border border-neutral-800 px-2 py-1 hover:border-neutral-500 disabled:opacity-50"
                    >
                      Reset password
                    </button>
                    <button
                      type="button"
                      onClick={() => onToggleDisabled(u)}
                      disabled={isSelf || rowBusy}
                      className="ml-2 rounded border border-neutral-800 px-2 py-1 hover:border-neutral-500 disabled:opacity-50"
                    >
                      {u.disabled ? "Enable" : "Disable"}
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(u)}
                      disabled={isSelf || rowBusy}
                      className="ml-2 inline-flex items-center gap-1 rounded border border-red-700/40 bg-red-950/20 px-2 py-1 text-red-300 hover:border-red-500 disabled:opacity-50"
                    >
                      <Trash2 size={12} /> Delete
                    </button>
                  </td>
                </tr>
              );
            })}
            {users.length === 0 && !loading ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-3 py-6 text-center text-xs text-neutral-500"
                >
                  No users yet. Click <em>New user</em> to create one.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

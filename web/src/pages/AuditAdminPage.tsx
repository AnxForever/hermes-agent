/**
 * Audit log viewer for admin writes (users, MCP servers, skill toggles,
 * uploads). Reads /api/admin/audit which proxies the audit_log table.
 *
 * Supports filter by action prefix (e.g. `user.`) and actor handle plus
 * limit/offset pagination. The full payload JSON is rendered inline so
 * a forensics walk doesn't need a second tool.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";

import { api, type AdminAuditEvent } from "@/lib/api";
import { usePageHeader } from "@/contexts/usePageHeader";

const PAGE_SIZE = 50;

function formatTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function actionStyle(action: string): string {
  if (action.startsWith("user.delete")) return "text-red-300";
  if (action.startsWith("user.")) return "text-amber-300";
  if (action.startsWith("mcp.")) return "text-cyan-300";
  if (action.startsWith("skill.")) return "text-emerald-300";
  if (action.startsWith("upload.")) return "text-violet-300";
  return "text-neutral-300";
}

export default function AuditAdminPage() {
  const [events, setEvents] = useState<AdminAuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [actionFilter, setActionFilter] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  usePageHeader(
    useMemo(
      () => ({
        title: "Audit log",
        subtitle: "Every admin write captured for forensics",
      }),
      [],
    ),
  );

  const refresh = useCallback(
    async (nextOffset = offset) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.adminListAudit({
          limit: PAGE_SIZE,
          offset: nextOffset,
          action: actionFilter.trim() || undefined,
          actor: actorFilter.trim() || undefined,
        });
        setEvents(res.events);
        setTotal(res.total);
        setOffset(res.offset);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load audit");
      } finally {
        setLoading(false);
      }
    },
    [actionFilter, actorFilter, offset],
  );

  useEffect(() => {
    refresh(0);
    // Intentionally not depending on refresh — we want one fetch on mount,
    // and filters drive their own debounced refresh via onApply below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onApplyFilters = useCallback(() => {
    setOffset(0);
    refresh(0);
  }, [refresh]);

  const onPrev = useCallback(() => {
    const next = Math.max(0, offset - PAGE_SIZE);
    refresh(next);
  }, [offset, refresh]);

  const onNext = useCallback(() => {
    const next = offset + PAGE_SIZE;
    if (next < total) refresh(next);
  }, [offset, total, refresh]);

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs">
          <span className="block text-neutral-400">Action prefix</span>
          <input
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder="e.g. user. or mcp.delete"
            className="mt-1 w-56 rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:border-neutral-500"
          />
        </label>
        <label className="text-xs">
          <span className="block text-neutral-400">Actor handle</span>
          <input
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            placeholder="e.g. admin"
            className="mt-1 w-40 rounded border border-neutral-800 bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:border-neutral-500"
          />
        </label>
        <button
          type="button"
          onClick={onApplyFilters}
          className="rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm hover:border-neutral-500"
        >
          Apply
        </button>
        <button
          type="button"
          onClick={() => refresh()}
          className="inline-flex items-center gap-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm hover:border-neutral-500"
          title="Refresh"
        >
          <RefreshCw size={12} /> Refresh
        </button>
        <div className="ml-auto text-xs text-neutral-400">
          {loading
            ? "Loading…"
            : `${offset + 1}–${Math.min(offset + events.length, total)} of ${total}`}
        </div>
      </div>

      {error ? (
        <div className="rounded border border-red-700/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      ) : null}

      <div className="flex-1 overflow-auto rounded-lg border border-neutral-800">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-neutral-950 text-xs uppercase text-neutral-400">
            <tr>
              <th className="px-3 py-2">When</th>
              <th className="px-3 py-2">Actor</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Target</th>
              <th className="px-3 py-2">Payload</th>
              <th className="px-3 py-2">IP</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr
                key={e.id}
                className="border-t border-neutral-800 hover:bg-neutral-950/60"
              >
                <td className="px-3 py-2 text-xs text-neutral-300">
                  {formatTs(e.ts)}
                </td>
                <td className="px-3 py-2 text-xs">
                  {e.actor_handle ? `@${e.actor_handle}` : "—"}
                </td>
                <td className={`px-3 py-2 text-xs ${actionStyle(e.action)}`}>
                  {e.action}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-neutral-400">
                  {e.target_id ?? "—"}
                </td>
                <td className="px-3 py-2 max-w-xl text-xs">
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] text-neutral-300">
                    {e.payload
                      ? JSON.stringify(e.payload, null, 2)
                      : "—"}
                  </pre>
                </td>
                <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                  {e.ip ?? "—"}
                </td>
              </tr>
            ))}
            {events.length === 0 && !loading ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-3 py-6 text-center text-xs text-neutral-500"
                >
                  No audit events match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onPrev}
          disabled={offset === 0 || loading}
          className="inline-flex items-center gap-1 rounded border border-neutral-700 px-2 py-1 text-xs hover:border-neutral-500 disabled:opacity-40"
        >
          <ChevronLeft size={12} /> Prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={offset + events.length >= total || loading}
          className="inline-flex items-center gap-1 rounded border border-neutral-700 px-2 py-1 text-xs hover:border-neutral-500 disabled:opacity-40"
        >
          Next <ChevronRight size={12} />
        </button>
      </div>
    </div>
  );
}

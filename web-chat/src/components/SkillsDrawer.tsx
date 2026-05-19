import { X } from "lucide-react";

import type { SkillEntry } from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  skills: SkillEntry[];
  onToggle: (name: string, enabled: boolean) => void;
}

export function SkillsDrawer({ open, onClose, skills, onToggle }: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="Close skills"
        onClick={onClose}
        className="absolute inset-0 bg-black/40"
      />
      <aside className="relative z-50 flex h-full w-80 flex-col border-l border-neutral-800 bg-neutral-950 shadow-2xl">
        <header className="flex items-center justify-between border-b border-neutral-800 px-4 py-3">
          <h2 className="text-sm font-semibold">Skills</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-900 hover:text-neutral-100"
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {skills.length === 0 ? (
            <p className="text-xs text-neutral-500">
              No skills found. Either the skill scanner returned an empty
              list, or the request failed silently — open devtools to
              inspect.
            </p>
          ) : (
            <ul className="space-y-2">
              {skills.map((s) => (
                <li
                  key={s.name}
                  className="rounded border border-neutral-800 bg-neutral-900 px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">
                        {s.name}
                      </div>
                      {s.description ? (
                        <div className="mt-0.5 truncate text-xs text-neutral-400">
                          {s.description}
                        </div>
                      ) : null}
                      {s.override ? (
                        <div className="mt-1 text-[10px] uppercase tracking-wide text-amber-400">
                          per-user override
                        </div>
                      ) : null}
                    </div>
                    <Toggle
                      checked={s.enabled}
                      onChange={(v) => onToggle(s.name, v)}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      role="switch"
      aria-checked={checked}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${
        checked ? "bg-white" : "bg-neutral-700"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full transition ${
          checked
            ? "translate-x-4 bg-neutral-900"
            : "translate-x-1 bg-neutral-200"
        }`}
      />
    </button>
  );
}

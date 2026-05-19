import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useNavigate } from "react-router-dom";
import { Paperclip, Send, Settings2, Square } from "lucide-react";

import {
  api,
  clearStoredCredentials,
  getStoredToken,
  getStoredUser,
  streamChatCompletion,
  type ChatMessage,
  type SkillEntry,
  type UserInfo,
} from "@/lib/api";
import { SkillsDrawer } from "@/components/SkillsDrawer";

interface DisplayMessage extends ChatMessage {
  id: string;
  pending?: boolean;
  error?: string;
}

function newMessageId(): string {
  return `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function ChatPage() {
  const navigate = useNavigate();
  const [user, setUser] = useState<UserInfo | null>(() => getStoredUser());
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Bounce to /login if no token, otherwise validate it once.
  useEffect(() => {
    if (!getStoredToken()) {
      navigate("/login", { replace: true });
      return;
    }
    api
      .me()
      .then((u) => setUser({ user_id: u.user_id, handle: u.handle, role: u.role }))
      .catch(() => {
        clearStoredCredentials();
        navigate("/login", { replace: true });
      });
  }, [navigate]);

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const wirePayload = useMemo<ChatMessage[]>(
    () =>
      messages
        .filter((m) => !m.error)
        .map((m) => ({ role: m.role, content: m.content })),
    [messages],
  );

  const onSubmit = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      const text = input.trim();
      if (!text || busy) return;

      const userMsg: DisplayMessage = {
        id: newMessageId(),
        role: "user",
        content: text,
      };
      const assistantMsg: DisplayMessage = {
        id: newMessageId(),
        role: "assistant",
        content: "",
        pending: true,
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const wire: ChatMessage[] = [
          ...wirePayload,
          { role: "user", content: text },
        ];
        for await (const delta of streamChatCompletion(wire, {
          signal: controller.signal,
        })) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, content: m.content + delta }
                : m,
            ),
          );
        }
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, pending: false } : m,
          ),
        );
      } catch (err) {
        if (controller.signal.aborted) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, pending: false, error: "stopped" }
                : m,
            ),
          );
        } else {
          const msg = err instanceof Error ? err.message : "request failed";
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, pending: false, error: msg }
                : m,
            ),
          );
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [busy, input, wirePayload],
  );

  const onStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const onLogout = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await api.logout();
    } catch {
      // ignore
    }
    clearStoredCredentials();
    navigate("/login", { replace: true });
  }, [navigate]);

  const openSkills = useCallback(async () => {
    setSkillsOpen(true);
    try {
      const res = await api.listSkills();
      setSkills(res.skills);
    } catch {
      setSkills([]);
    }
  }, []);

  const onToggleSkill = useCallback(
    async (name: string, enabled: boolean) => {
      setSkills((prev) =>
        prev.map((s) => (s.name === name ? { ...s, enabled } : s)),
      );
      try {
        await api.toggleSkill(name, enabled);
      } catch {
        // revert on failure
        setSkills((prev) =>
          prev.map((s) =>
            s.name === name ? { ...s, enabled: !enabled } : s,
          ),
        );
      }
    },
    [],
  );

  const onPickFile = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const onFileChosen = useCallback(
    async (file: File) => {
      try {
        const result = await api.uploadFile(file);
        setInput((prev) => {
          const tag = `[file: ${result.original_name} → ${result.path}]\n`;
          return prev ? `${prev}\n${tag}` : tag;
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : "upload failed";
        setMessages((prev) => [
          ...prev,
          {
            id: newMessageId(),
            role: "assistant",
            content: "",
            error: `upload failed: ${msg}`,
          },
        ]);
      }
    },
    [],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void onSubmit();
      }
    },
    [onSubmit],
  );

  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-neutral-800 bg-neutral-950 px-6 py-3">
        <div className="text-sm font-semibold">Hermes Chat</div>
        <div className="flex items-center gap-3 text-xs text-neutral-400">
          <button
            type="button"
            onClick={openSkills}
            className="inline-flex items-center gap-1 rounded border border-neutral-700 px-2 py-1 hover:border-neutral-500"
          >
            <Settings2 size={12} /> Skills
          </button>
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

      <section className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.length === 0 ? (
            <div className="rounded-lg border border-dashed border-neutral-800 bg-neutral-950/40 p-6 text-center text-sm text-neutral-500">
              Say hello to Hermes. Streaming responses, file uploads, and
              skill toggles are wired up.
            </div>
          ) : null}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          <div ref={scrollAnchorRef} />
        </div>
      </section>

      <form
        onSubmit={onSubmit}
        className="border-t border-neutral-800 bg-neutral-950 px-4 py-3"
      >
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void onFileChosen(file);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={onPickFile}
            className="flex h-10 w-10 items-center justify-center rounded border border-neutral-800 text-neutral-300 hover:border-neutral-500"
            title="Attach a file"
          >
            <Paperclip size={16} />
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Type your message… (Enter to send, Shift+Enter for newline)"
            className="min-h-[40px] flex-1 resize-none rounded border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
          {busy ? (
            <button
              type="button"
              onClick={onStop}
              className="flex h-10 items-center gap-1 rounded bg-red-600/80 px-3 text-sm font-medium text-white hover:bg-red-600"
            >
              <Square size={14} /> Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="flex h-10 items-center gap-1 rounded bg-white px-3 text-sm font-medium text-neutral-900 transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={14} /> Send
            </button>
          )}
        </div>
      </form>

      <SkillsDrawer
        open={skillsOpen}
        onClose={() => setSkillsOpen(false)}
        skills={skills}
        onToggle={onToggleSkill}
      />
    </main>
  );
}

function MessageBubble({ message }: { message: DisplayMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap break-words rounded-lg px-4 py-2 text-sm ${
          isUser
            ? "bg-white text-neutral-900"
            : "border border-neutral-800 bg-neutral-950 text-neutral-100"
        }`}
      >
        {message.error ? (
          <span className="text-red-400">⚠ {message.error}</span>
        ) : message.content || message.pending ? (
          <>
            {message.content}
            {message.pending ? (
              <span className="ml-1 inline-block h-2 w-2 animate-pulse rounded-full bg-neutral-400 align-middle" />
            ) : null}
          </>
        ) : (
          <span className="text-neutral-500">(empty)</span>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { sendMessage, listConversations, type OutputBlock, type Conversation } from "@/lib/api";
import { wsManager } from "@/lib/ws";
import { OutputBlock as OutputBlockRenderer } from "@/components/blocks/OutputBlock";
import { VoiceInput } from "@/components/chat/VoiceInput";
import { NotificationBell } from "@/components/notifications/NotificationBell";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  blocks: OutputBlock[];
};

// ---------------------------------------------------------------------------
// Sidebar — conversation history
// ---------------------------------------------------------------------------

function Sidebar({
  convs,
  activeId,
  onSelect,
  onNew,
}: {
  convs: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="w-56 bg-zinc-900 border-r border-zinc-800 flex flex-col h-full shrink-0">
      <div className="p-3 border-b border-zinc-800">
        <button
          onClick={onNew}
          className="w-full text-sm py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors"
        >
          + New chat
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        {convs.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full text-left text-xs px-3 py-2 rounded-lg mb-1 truncate transition-colors ${
              c.id === activeId
                ? "bg-indigo-900/60 text-indigo-200"
                : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
            }`}
          >
            {c.title || new Date(c.created_at).toLocaleDateString()}
          </button>
        ))}
      </nav>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------

function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-white text-xs font-bold mr-2 shrink-0 mt-0.5">
          A
        </div>
      )}
      <div className={`max-w-[85%] ${isUser ? "order-first" : ""}`}>
        {isUser ? (
          <div className="bg-zinc-700 text-zinc-100 text-sm px-4 py-2 rounded-2xl rounded-tr-sm">
            {msg.text}
          </div>
        ) : (
          <div className="space-y-1">
            {msg.blocks.length > 0
              ? msg.blocks.map((b, i) => <OutputBlockRenderer key={i} block={b} />)
              : msg.text && <OutputBlockRenderer block={{ type: "text", content: msg.text }} />}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Connect WS on mount
  useEffect(() => {
    wsManager.connect();
    listConversations().then(setConvs).catch(() => {});
    return () => wsManager.disconnect();
  }, []);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleVoiceTranscript = useCallback((text: string) => {
    setInput((prev) => (prev ? `${prev} ${text}` : text));
  }, []);

  const submit = async () => {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    setStreaming(true);

    // Add user message
    const userId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", text, blocks: [] },
    ]);

    // Create assistant message placeholder
    const assistantId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", text: "", blocks: [] },
    ]);

    try {
      for await (const block of sendMessage(text, convId)) {
        if (block.type === "done") {
          const b = block as unknown as { conversation_id?: string };
          if (b.conversation_id && !convId) {
            setConvId(b.conversation_id);
            listConversations().then(setConvs).catch(() => {});
          }
          continue;
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, blocks: [...m.blocks, block] }
              : m
          )
        );
      }
    } catch (err) {
      console.error("Chat error", err);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                blocks: [{ type: "text", content: "⚠️ Connection error. Please try again." }],
              }
            : m
        )
      );
    } finally {
      setStreaming(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const newChat = () => {
    setConvId(null);
    setMessages([]);
    setInput("");
  };

  const selectConversation = (id: string) => {
    setConvId(id);
    setMessages([]);
    import("@/lib/api").then(({ getConversation }) =>
      getConversation(id).then(({ messages: msgs }) => {
        setMessages(
          msgs.map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            text: m.content,
            blocks: m.blocks,
          }))
        );
      })
    );
  };

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <Sidebar
        convs={convs}
        activeId={convId}
        onSelect={selectConversation}
        onNew={newChat}
      />

      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="flex items-center justify-between px-4 py-3 border-b border-zinc-800 shrink-0">
          <h1 className="text-sm font-semibold text-zinc-300">Chiti</h1>
          <NotificationBell />
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-white text-xl font-bold mb-4">
                C
              </div>
              <p className="text-zinc-400 text-sm">What would you like to do?</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
          {streaming && (
            <div className="flex justify-start mb-4">
              <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-white text-xs font-bold mr-2 shrink-0">
                A
              </div>
              <div className="flex gap-1 items-center h-6">
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="px-4 py-3 border-t border-zinc-800 shrink-0">
          <div className="flex items-end gap-2 bg-zinc-800 rounded-2xl px-4 py-2 border border-zinc-700 focus-within:border-indigo-500 transition-colors">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message Chiti… (Shift+Enter for newline)"
              disabled={streaming}
              rows={1}
              className="flex-1 bg-transparent text-sm text-zinc-100 placeholder-zinc-500 resize-none outline-none max-h-48 overflow-y-auto"
              style={{ lineHeight: "1.5rem" }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = `${Math.min(el.scrollHeight, 192)}px`;
              }}
            />
            <div className="flex items-center gap-1 pb-0.5">
              <VoiceInput onTranscript={handleVoiceTranscript} />
              <button
                onClick={submit}
                disabled={!input.trim() || streaming}
                className="p-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30 disabled:cursor-not-allowed text-white transition-colors"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                </svg>
              </button>
            </div>
          </div>
          <p className="text-[10px] text-zinc-600 text-center mt-1">
            Chiti can make mistakes. Verify important information.
          </p>
        </div>
      </div>
    </div>
  );
}

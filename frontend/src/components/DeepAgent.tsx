import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import {
  Brain,
  Send,
  Search,
  FileCheck,
  Network,
  BarChart3,
  Wrench,
  ChevronDown,
  Loader2,
  AlertCircle,
  RotateCcw,
  Square,
  Sparkles,
  User,
} from "lucide-react";

const AGENT_BASE = "/agent";

/* ── Types ─────────────────────────────────────────────────────────── */

interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: "running" | "done" | "error";
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning: string;
  toolCalls: ToolCall[];
  streaming: boolean;
  error?: string;
}

/* ── Static data ───────────────────────────────────────────────────── */

const SUGGESTIONS = [
  "¿Es vigente la Ley 1715 de 2014 sobre acceso a información?",
  "¿Qué dijo la Corte sobre el IVA en servicios digitales?",
  "Resume las normas sobre protección de datos personales",
  "¿Cuáles son las causales de divorcio en Colombia?",
];

const TOOL_META: Record<
  string,
  { icon: typeof Search; label: string; color: string }
> = {
  buscar_normas: { icon: Search, label: "Buscar Normas", color: "#3b82f6" },
  texto_vigente: { icon: FileCheck, label: "Texto Vigente", color: "#10b981" },
  consulta_grafo: { icon: Network, label: "Consulta Grafo", color: "#8b5cf6" },
  estadistica_jurisprudencial: {
    icon: BarChart3,
    label: "Jurimetría",
    color: "#f59e0b",
  },
};

let _msgSeq = 0;
const nextId = () => `m${++_msgSeq}-${Date.now()}`;

/* ── Component ─────────────────────────────────────────────────────── */

export default function DeepAgent() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  /* Auto-scroll on new content */
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  /* Autosize textarea */
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  }, [input]);

  /* ── Core send logic ────────────────────────────────────────────── */

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      setInput("");
      setLoading(true);

      const userMsg: Message = {
        id: nextId(),
        role: "user",
        content: trimmed,
        reasoning: "",
        toolCalls: [],
        streaming: false,
      };
      const assistantId = nextId();
      const assistantMsg: Message = {
        id: assistantId,
        role: "assistant",
        content: "",
        reasoning: "",
        toolCalls: [],
        streaming: true,
      };

      const history = messages
        .filter((m) => m.content && !m.streaming && !m.error)
        .map((m) => ({ role: m.role, content: m.content }));

      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      const controller = new AbortController();
      abortRef.current = controller;

      const patch = (updater: (m: Message) => Message) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? updater(m) : m))
        );

      try {
        const resp = await fetch(`${AGENT_BASE}/api/agent/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: trimmed, history }),
          signal: controller.signal,
        });

        if (!resp.ok || !resp.body) {
          throw new Error(`HTTP ${resp.status} — el agente no respondió`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const raw of lines) {
            const line = raw.trim();
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6).trim();
            if (!payload || payload === "[DONE]") continue;

            try {
              const evt = JSON.parse(payload);
              switch (evt.type) {
                case "reasoning":
                  patch((m) => ({
                    ...m,
                    reasoning: m.reasoning + evt.content,
                  }));
                  break;

                case "reasoning_block":
                  patch((m) => ({
                    ...m,
                    reasoning: evt.content || m.reasoning,
                  }));
                  break;

                case "content":
                  patch((m) => ({ ...m, content: m.content + evt.content }));
                  break;

                case "tool_start":
                  patch((m) => ({
                    ...m,
                    toolCalls: [
                      ...m.toolCalls,
                      {
                        id: `t${Date.now()}-${m.toolCalls.length}`,
                        name: evt.name || "desconocida",
                        args: evt.args || {},
                        status: "running",
                      },
                    ],
                  }));
                  break;

                case "tool_result":
                  patch((m) => {
                    const calls = [...m.toolCalls];
                    for (let i = calls.length - 1; i >= 0; i--) {
                      if (
                        calls[i].name === evt.name &&
                        calls[i].status === "running"
                      ) {
                        calls[i] = {
                          ...calls[i],
                          result: evt.result ?? "",
                          status: "done",
                        };
                        break;
                      }
                    }
                    return { ...m, toolCalls: calls };
                  });
                  break;

                case "error":
                  patch((m) => ({
                    ...m,
                    error: evt.content || "Error desconocido",
                    streaming: false,
                  }));
                  setLoading(false);
                  break;

                case "done":
                  patch((m) => ({ ...m, streaming: false }));
                  setLoading(false);
                  break;
              }
            } catch {
              /* ignore malformed JSON lines */
            }
          }
        }

        patch((m) => ({ ...m, streaming: false }));
        setLoading(false);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const msg =
          err instanceof Error ? err.message : "Error de conexión con el agente";
        patch((m) => ({ ...m, error: msg, streaming: false }));
        setLoading(false);
      } finally {
        abortRef.current = null;
      }
    },
    [messages, loading]
  );

  const handleStop = () => {
    abortRef.current?.abort();
    setLoading(false);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m))
    );
  };

  const handleClear = () => {
    handleStop();
    setMessages([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  /* ── Render ─────────────────────────────────────────────────────── */

  return (
    <div className="da-container">
      {/* Header */}
      <div className="da-header">
        <div className="da-header-left">
          <div className="da-header-icon">
            <Brain size={22} />
          </div>
          <div>
            <h2 className="page-title">LexIA — Agente Jurídico</h2>
            <p className="page-subtitle">
              Razonamiento visible · Herramientas MCP · Respuestas con citas
            </p>
          </div>
        </div>
        {messages.length > 0 && (
          <button className="da-clear-btn" onClick={handleClear} title="Nueva conversación">
            <RotateCcw size={15} />
            <span>Nueva</span>
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="da-messages" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="da-empty">
            <div className="da-empty-icon">
              <Sparkles size={32} />
            </div>
            <h3>Consulta el ordenamiento jurídico colombiano</h3>
            <p className="muted">
              El agente buscará normas, verificará vigencia y citará fuentes
              mostrando su razonamiento paso a paso.
            </p>
            <div className="da-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="da-suggestion"
                  onClick={() => sendMessage(s)}
                >
                  <Sparkles size={13} />
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="da-msg-list">
            {messages.map((m) =>
              m.role === "user" ? (
                <UserBubble key={m.id} text={m.content} />
              ) : (
                <AssistantMessage key={m.id} msg={m} />
              )
            )}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="da-input-area">
        <div className="da-input-wrap">
          <textarea
            ref={taRef}
            className="da-textarea"
            placeholder="Escribe tu consulta jurídica…  (Enter para enviar, Shift+Enter para salto de línea)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={loading}
          />
          {loading ? (
            <button className="da-send-btn da-stop-btn" onClick={handleStop} title="Detener">
              <Square size={17} />
            </button>
          ) : (
            <button
              className="da-send-btn"
              onClick={() => sendMessage(input)}
              disabled={!input.trim()}
              title="Enviar"
            >
              <Send size={17} />
            </button>
          )}
        </div>
        <p className="da-disclaimer">
          LexIA puede cometer errores. Verifica siempre las normas citadas antes
          de usarlas en un caso real.
        </p>
      </div>
    </div>
  );
}

/* ── Sub-components ────────────────────────────────────────────────── */

function UserBubble({ text }: { text: string }) {
  return (
    <div className="da-msg da-msg-user">
      <div className="da-avatar da-avatar-user">
        <User size={15} />
      </div>
      <div className="da-bubble da-bubble-user">{text}</div>
    </div>
  );
}

function AssistantMessage({ msg }: { msg: Message }) {
  return (
    <div className="da-msg da-msg-assistant">
      <div className="da-avatar da-avatar-ai">
        <Brain size={15} />
      </div>
      <div className="da-msg-body">
        {/* Reasoning */}
        {msg.reasoning && (
          <ReasoningPanel text={msg.reasoning} streaming={msg.streaming && !msg.content} />
        )}

        {/* Tool calls */}
        {msg.toolCalls.length > 0 && (
          <div className="da-tool-calls">
            {msg.toolCalls.map((tc) => (
              <ToolCallCard key={tc.id} tc={tc} />
            ))}
          </div>
        )}

        {/* Content */}
        {msg.content ? (
          <div className="da-bubble da-bubble-ai markdown-body">
            <ReactMarkdown>{msg.content}</ReactMarkdown>
            {msg.streaming && <span className="da-cursor" />}
          </div>
        ) : msg.streaming && !msg.reasoning && msg.toolCalls.length === 0 ? (
          <div className="da-thinking">
            <Loader2 size={15} className="da-spin" />
            <span>Pensando…</span>
          </div>
        ) : null}

        {/* Error */}
        {msg.error && (
          <div className="da-error">
            <AlertCircle size={15} />
            <span>{msg.error}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function ReasoningPanel({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(true);

  // Auto-collapse when reasoning finishes (streaming turns false)
  useEffect(() => {
    if (!streaming) {
      const t = setTimeout(() => setOpen(false), 600);
      return () => clearTimeout(t);
    }
  }, [streaming]);

  return (
    <div className={`da-reasoning ${open ? "open" : ""}`}>
      <button
        className="da-reasoning-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        {streaming ? (
          <Loader2 size={13} className="da-spin" />
        ) : (
          <Brain size={13} />
        )}
        <span>Razonamiento</span>
        <ChevronDown size={14} className={`da-chev ${open ? "up" : ""}`} />
      </button>
      {open && (
        <div className="da-reasoning-body">
          {text}
          {streaming && <span className="da-cursor" />}
        </div>
      )}
    </div>
  );
}

function ToolCallCard({ tc }: { tc: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const meta = TOOL_META[tc.name] ?? {
    icon: Wrench,
    label: tc.name,
    color: "#6b7280",
  };
  const Icon = meta.icon;

  return (
    <div className="da-tool-card" style={{ borderLeftColor: meta.color }}>
      <div className="da-tool-head">
        <span className="da-tool-icon" style={{ color: meta.color }}>
          <Icon size={14} />
        </span>
        <span className="da-tool-name">{meta.label}</span>
        {tc.status === "running" ? (
          <span className="da-tool-status running">
            <Loader2 size={11} className="da-spin" /> ejecutando
          </span>
        ) : (
          <span className="da-tool-status done">✓ listo</span>
        )}
      </div>

      {Object.keys(tc.args).length > 0 && (
        <div className="da-tool-args">
          {Object.entries(tc.args).map(([k, v]) => (
            <div key={k} className="da-tool-arg">
              <span className="da-tool-arg-key">{k}</span>
              <span className="da-tool-arg-val">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </span>
            </div>
          ))}
        </div>
      )}

      {tc.result && (
        <div className="da-tool-result-wrap">
          <button
            className="da-tool-result-toggle"
            onClick={() => setExpanded((v) => !v)}
          >
            <ChevronDown size={12} className={`da-chev ${expanded ? "up" : ""}`} />
            {expanded ? "Ocultar resultado" : "Ver resultado"}
          </button>
          {expanded && (
            <pre className="da-tool-result">{tc.result}</pre>
          )}
        </div>
      )}
    </div>
  );
}

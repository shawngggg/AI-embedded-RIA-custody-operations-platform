import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Loading, when } from "../util";

export type Ref = { type: "case" | "account" | "firm"; id: number | string; label: string; case_id?: number | null };
export type Message = {
  id: number; channel: string | null; channel_name: string | null; author: string; author_username: string | null;
  body: string; refs: Ref[]; mentions: { username: string; name: string }[]; mentions_me: boolean; created_at: string;
};
type ChannelRow = { key: string; name: string; description: string; restricted: boolean; unread: number; last: Message | null };

const TOKEN = /(\bcase\s*#?\d{1,6}\b|(?<![\w&])#\d{1,6}\b|\b(?:ACC|FIRM)-\d{3,10}\b|(?<![\w.])@[a-z0-9._-]{3,80})/gi;

/** A message body with its case, account, and person references turned into links. */
export function MessageBody({ m }: { m: Message }) {
  const parts = m.body.split(TOKEN);
  return (
    <p className="msg-body">{parts.map((p, i) => {
      if (i % 2 === 0) return <Fragment key={i}>{p}</Fragment>;
      const num = p.match(/(\d{1,6})$/)?.[1];
      if (/^(case|#)/i.test(p) && num) {
        const ref = m.refs.find((r) => r.type === "case" && String(r.id) === num);
        return ref ? <Link key={i} to={`/cases/${num}`} title={ref.label}>{p}</Link> : <Fragment key={i}>{p}</Fragment>;
      }
      if (p.startsWith("@")) {
        const who = m.mentions.find((x) => x.username === p.slice(1).toLowerCase());
        return who ? <strong key={i} className="mention" title={who.name}>@{who.name}</strong> : <Fragment key={i}>{p}</Fragment>;
      }
      const ref = m.refs.find((r) => String(r.id) === p.toUpperCase());
      return ref?.case_id ? <Link key={i} to={`/cases/${ref.case_id}`} title={ref.label}>{p}</Link> : <Fragment key={i}>{p}</Fragment>;
    })}</p>
  );
}

export default function Messages() {
  const { user, refreshCounts } = useAuth();
  const [params, setParams] = useSearchParams();
  const [channels, setChannels] = useState<ChannelRow[] | null>(null);
  const active = params.get("c") ?? "general";
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [draft, setDraft] = useState(params.get("draft") ?? "");
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const lastId = useRef(0);

  const loadChannels = () => api<{ channels: ChannelRow[] }>("/api/channels").then((r) => setChannels(r.channels)).catch((e) => setError(e.message));
  useEffect(() => { loadChannels(); }, []);
  useEffect(() => {
    setMsgs([]); lastId.current = 0; setError(null);
    const pull = () => api<Message[]>(`/api/channels/${active}/messages?after=${lastId.current}`).then((rows) => {
      if (rows.length) {
        lastId.current = rows[rows.length - 1].id;
        setMsgs((cur) => [...cur, ...rows.filter((r) => !cur.some((c) => c.id === r.id))]);
        loadChannels(); refreshCounts();
      }
    }).catch((e) => setError(e.message));
    pull();
    const t = window.setInterval(pull, 8000);
    return () => window.clearInterval(t);
  }, [active]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [msgs.length]);

  const send = () => {
    const body = draft.trim();
    if (!body) return;
    api<Message>(`/api/channels/${active}/messages`, { body: { body } }).then((m) => {
      lastId.current = Math.max(lastId.current, m.id);
      setMsgs((cur) => [...cur, m]); setDraft("");
      if (params.get("draft")) setParams({ c: active });
    }).catch((e) => setError(e.message));
  };
  const current = channels?.find((c) => c.key === active);

  return (
    <>
      <h1>Messages</h1>
      <div className="chat">
        <nav className="chat-channels" aria-label="Channels">
          {!channels ? <Loading /> : channels.map((c) => (
            <button key={c.key} className={c.key === active ? "on" : ""} onClick={() => setParams({ c: c.key })}>
              <span># {c.name}{c.restricted ? " 🔒" : ""}</span>{c.unread > 0 && <span className="badge alert">{c.unread}</span>}
            </button>))}
          <select aria-label="Channel" className="chat-select" value={active} onChange={(e) => setParams({ c: e.target.value })}>
            {(channels ?? []).map((c) => <option key={c.key} value={c.key}>#{c.name}{c.unread ? ` (${c.unread})` : ""}</option>)}
          </select>
        </nav>
        <section className="chat-pane">
          <header><strong># {current?.name ?? active}</strong> <span className="muted small">{current?.description}</span></header>
          <div className="chat-log" aria-live="polite">
            {error && <p className="error">{error}</p>}
            {msgs.length === 0 && !error && <div className="empty">No messages yet. Start the conversation.</div>}
            {msgs.map((m) => (
              <article key={m.id} className={`msg ${m.mentions_me ? "mine-mention" : ""} ${m.author_username === user.username ? "own" : ""}`}>
                <div className="msg-head"><strong>{m.author}</strong> <span className="muted small">{when(m.created_at)}</span></div>
                <MessageBody m={m} />
              </article>))}
            <div ref={bottom} />
          </div>
          <div className="chat-compose">
            <label htmlFor="compose" className="small muted">Reference a case with #16, an account with ACC-2001, a person with @username. Enter sends; Shift+Enter adds a line.</label>
            <textarea id="compose" value={draft} rows={2} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button onClick={send} disabled={!draft.trim()}>Send</button>
          </div>
        </section>
      </div>
    </>
  );
}

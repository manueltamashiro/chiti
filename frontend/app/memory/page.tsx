"use client";

import { useState, useEffect } from "react";

type Fact = { id: string; subject: string; predicate: string; object: string; confidence: number; created_at: string };
type Pref = { id: string; key: string; value: string; updated_at: string };
type Person = { id: string; name: string; relationship: string; notes: string; updated_at: string };
type EpisodicSummary = { id: string; conversation_id: string; summary: string; timestamp: string };

type Tab = "facts" | "preferences" | "people" | "episodic";

async function deleteMem(type: string, id: string) {
  await fetch(`/api/memory/${type}/${id}`, { method: "DELETE" });
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${color}`}>{label}</span>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-zinc-700 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-zinc-500">{pct}%</span>
    </div>
  );
}

export default function MemoryPage() {
  const [tab, setTab] = useState<Tab>("facts");
  const [search, setSearch] = useState("");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [prefs, setPrefs] = useState<Pref[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [episodic, setEpisodic] = useState<EpisodicSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [f, p, pe, ep] = await Promise.all([
        fetch("/api/memory/facts").then((r) => r.json()),
        fetch("/api/memory/preferences").then((r) => r.json()),
        fetch("/api/memory/people").then((r) => r.json()),
        fetch("/api/memory/episodic").then((r) => r.json()),
      ]);
      setFacts(f.facts ?? []);
      setPrefs(p.preferences ?? []);
      setPeople(pe.people ?? []);
      setEpisodic(ep.summaries ?? []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchAll(); }, []);

  const handleDelete = async (type: string, id: string) => {
    if (!confirm("Permanently delete this memory?")) return;
    await deleteMem(type, id);
    fetchAll();
  };

  const q = search.toLowerCase();

  const filteredFacts = facts.filter(
    (f) => !q || `${f.subject} ${f.predicate} ${f.object}`.toLowerCase().includes(q)
  );
  const filteredPrefs = prefs.filter(
    (p) => !q || `${p.key} ${p.value}`.toLowerCase().includes(q)
  );
  const filteredPeople = people.filter(
    (p) => !q || `${p.name} ${p.relationship} ${p.notes}`.toLowerCase().includes(q)
  );
  const filteredEpisodic = episodic.filter(
    (e) => !q || e.summary.toLowerCase().includes(q)
  );

  const tabs: { id: Tab; label: string; count: number }[] = [
    { id: "facts", label: "Facts", count: facts.length },
    { id: "preferences", label: "Preferences", count: prefs.length },
    { id: "people", label: "People", count: people.length },
    { id: "episodic", label: "Episodic", count: episodic.length },
  ];

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-zinc-100">Memory Browser</h1>
          <a href="/" className="text-sm text-indigo-400 hover:text-indigo-300">← Back to chat</a>
        </div>

        {/* Search */}
        <input
          type="text"
          placeholder="Search memories…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full mb-4 px-4 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-indigo-500"
        />

        {/* Tabs */}
        <div className="flex gap-1 mb-4 bg-zinc-900 rounded-lg p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex-1 py-1.5 text-sm rounded-md font-medium transition-colors ${
                tab === t.id
                  ? "bg-zinc-700 text-zinc-100"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {t.label}
              <span className="ml-1.5 text-[10px] opacity-60">{t.count}</span>
            </button>
          ))}
        </div>

        {loading ? (
          <p className="text-center text-zinc-500 py-12">Loading…</p>
        ) : (
          <>
            {/* Facts */}
            {tab === "facts" && (
              <div className="space-y-2">
                {filteredFacts.length === 0 ? (
                  <p className="text-zinc-500 text-sm text-center py-8">No facts stored yet.</p>
                ) : (
                  filteredFacts.map((f) => (
                    <div key={f.id} className="flex items-center gap-3 p-3 bg-zinc-900 rounded-lg border border-zinc-800">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-zinc-200">
                          <span className="font-medium text-indigo-300">{f.subject}</span>{" "}
                          <span className="text-zinc-500">{f.predicate}</span>{" "}
                          <span className="text-zinc-200">{f.object}</span>
                        </p>
                        <div className="flex items-center gap-3 mt-1">
                          <ConfidenceBar value={f.confidence} />
                          <span className="text-[10px] text-zinc-600">{f.created_at.slice(0, 10)}</span>
                        </div>
                      </div>
                      <button
                        onClick={() => handleDelete("fact", f.id)}
                        className="text-zinc-600 hover:text-rose-400 transition-colors text-xs px-2 py-1"
                      >
                        Delete
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Preferences */}
            {tab === "preferences" && (
              <div className="space-y-2">
                {filteredPrefs.length === 0 ? (
                  <p className="text-zinc-500 text-sm text-center py-8">No preferences stored yet.</p>
                ) : (
                  filteredPrefs.map((p) => (
                    <div key={p.id} className="flex items-center gap-3 p-3 bg-zinc-900 rounded-lg border border-zinc-800">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm">
                          <span className="font-medium text-emerald-300">{p.key}</span>
                          <span className="text-zinc-500 mx-1">→</span>
                          <span className="text-zinc-200">{p.value}</span>
                        </p>
                        <p className="text-[10px] text-zinc-600 mt-0.5">Updated {p.updated_at.slice(0, 10)}</p>
                      </div>
                      <button
                        onClick={() => handleDelete("preference", p.id)}
                        className="text-zinc-600 hover:text-rose-400 transition-colors text-xs px-2 py-1"
                      >
                        Delete
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* People */}
            {tab === "people" && (
              <div className="space-y-2">
                {filteredPeople.length === 0 ? (
                  <p className="text-zinc-500 text-sm text-center py-8">No people stored yet.</p>
                ) : (
                  filteredPeople.map((p) => (
                    <div key={p.id} className="flex items-center gap-3 p-3 bg-zinc-900 rounded-lg border border-zinc-800">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-zinc-200">{p.name}</p>
                        <p className="text-xs text-amber-300">{p.relationship}</p>
                        {p.notes && <p className="text-xs text-zinc-500 mt-0.5 line-clamp-2">{p.notes}</p>}
                      </div>
                      <button
                        onClick={() => handleDelete("person", p.id)}
                        className="text-zinc-600 hover:text-rose-400 transition-colors text-xs px-2 py-1"
                      >
                        Delete
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Episodic */}
            {tab === "episodic" && (
              <div className="space-y-2">
                {filteredEpisodic.length === 0 ? (
                  <p className="text-zinc-500 text-sm text-center py-8">No conversation summaries yet.</p>
                ) : (
                  filteredEpisodic.map((e) => (
                    <div key={e.id} className="p-3 bg-zinc-900 rounded-lg border border-zinc-800">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-[10px] text-zinc-500 mb-1">
                            {e.timestamp.slice(0, 16).replace("T", " ")} UTC · conv {e.conversation_id.slice(0, 8)}…
                          </p>
                          <p className="text-sm text-zinc-300 line-clamp-3">{e.summary}</p>
                        </div>
                        <button
                          onClick={() => handleDelete("episodic", e.id)}
                          className="text-zinc-600 hover:text-rose-400 transition-colors text-xs px-2 py-1 shrink-0"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

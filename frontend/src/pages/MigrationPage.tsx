// MigrationPage v2 — batch processing with progress + migrated/pending tabs.
import { useState } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface PreviewItem {
  hash: string; name: string; current_path: string; current_folder: string;
  target_folder: string | null; target_path: string | null;
  needs_move: boolean; file_mtime: string | null;
}
interface PreviewResponse { items: PreviewItem[]; need_move_count: number; already_ok_count: number; total: number; }
interface ExecResultItem { hash: string; name: string; ok: boolean; error: string | null; action: string | null; }
interface ExecResponse { total: number; succeeded: number; failed: number; dry_run: boolean; results: ExecResultItem[]; }

const BATCH = 50;

export default function MigrationPage() {
  const t = useTheme();
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<ExecResultItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [tab, setTab] = useState<"pending" | "done">("pending");
  const [isDryRun, setIsDryRun] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  async function scan() {
    setBusy(true); setError(null); setResults([]);
    try {
      const r = await api.get<PreviewResponse>("/v1/migration/preview");
      setPreview(r);
      setSelected(new Set(r.items.filter(i => i.needs_move).map(i => i.hash)));
      setTab("pending");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  async function execute(dryRun: boolean) {
    const hashes = [...selected];
    if (hashes.length === 0) return;
    setBusy(true); setError(null); setResults([]); setIsDryRun(dryRun);
    setProgress({ done: 0, total: hashes.length });

    const allResults: ExecResultItem[] = [];
    for (let i = 0; i < hashes.length; i += BATCH) {
      const batch = hashes.slice(i, i + BATCH);
      try {
        const r = await api.post<ExecResponse>("/v1/migration/execute", { hashes: batch, dry_run: dryRun });
        allResults.push(...r.results);
      } catch (e) {
        batch.forEach(h => allResults.push({ hash: h, name: "?", ok: false, error: String(e), action: null }));
      }
      setProgress({ done: Math.min(i + BATCH, hashes.length), total: hashes.length });
      setResults([...allResults]);
    }
    setProgress(null);
    setBusy(false);
    if (!dryRun) {
      try {
        const fresh = await api.get<PreviewResponse>("/v1/migration/preview");
        setPreview(fresh);
        setSelected(new Set(fresh.items.filter(i => i.needs_move).map(i => i.hash)));
      } catch { /* */ }
    }
  }

  async function resumeAll() {
    if (!confirm("Resume all stopped torrents in the watched category?")) return;
    setBusy(true);
    try {
      const r = await api.post<{ resumed: number; total: number }>("/v1/migration/resume-all");
      setSuccessMsg(`Resumed ${r.resumed} of ${r.total} torrents`);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  function toggle(hash: string) { setSelected(s => { const n = new Set(s); n.has(hash) ? n.delete(hash) : n.add(hash); return n; }); }
  function toggleAll() {
    if (!preview) return;
    const movable = preview.items.filter(i => i.needs_move);
    setSelected(selected.size === movable.length ? new Set() : new Set(movable.map(i => i.hash)));
  }

  const pendingItems = preview?.items.filter(i => i.needs_move) ?? [];
  const doneItems = preview?.items.filter(i => !i.needs_move) ?? [];
  const succeeded = results.filter(r => r.ok).length;
  const failed = results.filter(r => !r.ok).length;

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: t.text, marginBottom: 4 }}>Migration Wizard</h1>
      <p style={{ fontSize: 14, color: t.textDim, marginBottom: 20 }}>
        Move existing downloads into the configured folder structure based on file modification dates.
      </p>

      {error && <div style={{ background: t.err + "22", border: `1px solid ${t.err}55`, color: t.err, padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>{error}</div>}
      {successMsg && <div style={{ background: t.ok + "22", border: `1px solid ${t.ok}55`, color: t.ok, padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>{successMsg}</div>}

      {!preview && (
        <Section title="Getting started">
          <div style={{ background: t.warn + "14", border: `1px solid ${t.warn}33`, borderRadius: 10, padding: "14px 18px", marginBottom: 16, fontSize: 13, lineHeight: 1.6, color: t.text2 }}>
            <div style={{ fontWeight: 700, color: t.warn, marginBottom: 6 }}>⚠ Before you start</div>
            <ol style={{ margin: 0, paddingLeft: 20 }}>
              <li style={{ marginBottom: 4 }}><strong>Stop all torrents</strong> in your download client first. This prevents tracker count spikes during the move.</li>
              <li style={{ marginBottom: 4 }}>Wait ~1 minute for the tracker to register the stop.</li>
              <li>Run the migration. Hermeece will <strong>not</strong> auto-resume stopped torrents — use the "Start All" button at the end when you're satisfied.</li>
            </ol>
          </div>
          <Btn variant="primary" onClick={scan} disabled={busy}>{busy ? <Spin size={14} /> : "Scan Torrents"}</Btn>
        </Section>
      )}

      {preview && (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
            <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${t.borderL}` }}>
              <TabBtn active={tab === "pending"} label={`Needs migration (${pendingItems.length})`} onClick={() => setTab("pending")} />
              <TabBtn active={tab === "done"} label={`Already correct (${doneItems.length})`} onClick={() => setTab("done")} />
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: t.textDim }}>{preview.total} total</span>
              <Btn variant="ghost" onClick={scan} disabled={busy}>Re-scan</Btn>
              {tab === "pending" && <Btn variant="ghost" onClick={toggleAll}>{selected.size === pendingItems.length ? "Deselect all" : "Select all"}</Btn>}
            </div>
          </div>

          <Section title={tab === "pending" ? `${pendingItems.length} torrents to migrate` : `${doneItems.length} already correct`}>
            <div style={{ maxHeight: 500, overflowY: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead><tr style={{ textAlign: "left", color: t.textDim, fontWeight: 600, fontSize: 11, textTransform: "uppercase" }}>
                  {tab === "pending" && <th style={{ padding: "6px 4px", width: 30 }}></th>}
                  <th style={{ padding: "6px 4px" }}>Name</th>
                  <th style={{ padding: "6px 4px" }}>Current folder</th>
                  <th style={{ padding: "6px 4px" }}>Target</th>
                  <th style={{ padding: "6px 4px" }}>File date</th>
                </tr></thead>
                <tbody>
                  {(tab === "pending" ? pendingItems : doneItems).map(item => (
                    <tr key={item.hash} style={{ borderTop: `1px solid ${t.borderL}` }}>
                      {tab === "pending" && <td style={{ padding: "6px 4px" }}><input type="checkbox" checked={selected.has(item.hash)} onChange={() => toggle(item.hash)} /></td>}
                      <td style={{ padding: "6px 4px", color: t.text, maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={item.name}>{item.name}</td>
                      <td style={{ padding: "6px 4px", color: item.needs_move ? t.warn : t.ok, fontSize: 11 }}>{item.current_folder}</td>
                      <td style={{ padding: "6px 4px", color: t.accent, fontSize: 11 }}>{item.target_folder || "root"}</td>
                      <td style={{ padding: "6px 4px", color: t.textDim, fontSize: 11 }}>{item.file_mtime?.slice(0, 10) || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          {tab === "pending" && pendingItems.length > 0 && (
            <Section title="Execute" subtitle={`${selected.size} selected · processed in batches of ${BATCH}`}>
              {progress && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: t.textDim, marginBottom: 4 }}>
                    <span>Processing {progress.done} / {progress.total}…</span>
                    <span>{Math.round(progress.done / progress.total * 100)}%</span>
                  </div>
                  <div style={{ height: 6, borderRadius: 3, background: t.bg4, overflow: "hidden" }}>
                    <div style={{ width: `${progress.done / progress.total * 100}%`, height: "100%", background: t.accent, borderRadius: 3, transition: "width 0.3s" }} />
                  </div>
                </div>
              )}
              <div style={{ display: "flex", gap: 10 }}>
                <Btn variant="secondary" onClick={() => execute(true)} disabled={busy || selected.size === 0}>
                  {busy && isDryRun ? <Spin size={14} /> : `🧪 Dry Run (${selected.size})`}
                </Btn>
                <Btn variant="primary" onClick={() => execute(false)} disabled={busy || selected.size === 0}>
                  {busy && !isDryRun ? <Spin size={14} /> : `📦 Migrate ${selected.size}`}
                </Btn>
              </div>
            </Section>
          )}

          {results.length > 0 && (
            <Section title={`${isDryRun ? "🧪 Dry Run" : "Migration"} — ${succeeded} OK, ${failed} failed`} right={<Btn variant="ghost" onClick={() => setResults([])}>Clear</Btn>}>
              {isDryRun && <p style={{ fontSize: 13, color: t.accent, marginBottom: 12 }}>Dry run — no files moved. Review, then run the real migration.</p>}
              <div style={{ maxHeight: 300, overflowY: "auto" }}>
                {results.map((r, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "6px 0", borderBottom: `1px solid ${t.borderL}`, fontSize: 12, gap: 8 }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <span style={{ color: r.ok ? t.text2 : t.err }}>{r.name}</span>
                      {r.action && <span style={{ color: t.textDim, marginLeft: 8, fontFamily: "monospace", fontSize: 11 }}>{r.action}</span>}
                    </div>
                    <span style={{ color: r.ok ? t.ok : t.err, fontWeight: 600, flexShrink: 0 }}>{r.ok ? "✓" : "✗"} {r.error || ""}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {results.length > 0 && !isDryRun && succeeded > 0 && (
            <div style={{ marginTop: 16, padding: "16px 20px", background: t.ok + "12", border: `1px solid ${t.ok}33`, borderRadius: 10, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, color: t.text }}>Migration complete</div>
                <div style={{ fontSize: 12, color: t.textDim, marginTop: 4 }}>{succeeded} torrent(s) relocated. Resume when ready.</div>
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <Btn variant="primary" onClick={resumeAll} disabled={busy}>▶ Start All Torrents</Btn>
                <Btn variant="ghost" onClick={() => { setPreview(null); setResults([]); }}>Done</Btn>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TabBtn({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  const t = useTheme();
  return (
    <button onClick={onClick} style={{
      background: "transparent", border: "none",
      borderBottom: `2px solid ${active ? t.accent : "transparent"}`,
      color: active ? t.accent : t.text2,
      padding: "10px 16px", fontSize: 14, fontWeight: 600,
      cursor: "pointer", marginBottom: -1,
    }}>{label}</button>
  );
}

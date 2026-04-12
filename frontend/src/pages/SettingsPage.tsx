// SettingsPage — AthenaScout-style collapsible sections.
//
// Uses the same SSection/SF/STog patterns as AthenaScout for
// consistent cross-app UX. Settings are loaded into local state,
// edited in-place, and saved with a single "Save Settings" button.
// Credentials are inline with masking + Change/Save/Cancel flow.
import { useEffect, useState, type ReactNode } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

type S = Record<string, unknown>;

// ─── Shared components (outside SettingsPage to avoid re-mount) ──

function SSection({ title, defaultOpen = true, children }: {
  title: string; defaultOpen?: boolean; children: ReactNode;
}) {
  const t = useTheme();
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12, marginBottom: 16 }}>
      <div onClick={() => setOpen(!open)} style={{
        display: "flex", alignItems: "center", gap: 8, padding: "14px 20px",
        cursor: "pointer", userSelect: "none",
      }}>
        <span style={{ transform: open ? "rotate(0)" : "rotate(-90deg)", transition: "transform 0.2s", fontSize: 11, color: t.textDim }}>▼</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: t.text, textTransform: "uppercase", letterSpacing: "0.05em" }}>{title}</span>
      </div>
      {open && <div style={{ padding: "0 20px 16px" }}>{children}</div>}
    </div>
  );
}

function SF({ label, desc, children, warn }: {
  label: string; desc?: string; children: ReactNode; warn?: string;
}) {
  const t = useTheme();
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 0", borderBottom: `1px solid ${t.borderL}`, gap: 16 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: t.text2 }}>{label}</div>
        {desc && <div style={{ fontSize: 12, color: t.textDim, marginTop: 2 }}>{desc}</div>}
        {warn && <div style={{ fontSize: 11, color: t.warn, marginTop: 2 }}>⚠ {warn}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

function STog({ on, onToggle, disabled }: { on: boolean; onToggle: () => void; disabled?: boolean }) {
  const t = useTheme();
  return (
    <div onClick={disabled ? undefined : onToggle} style={{
      width: 44, height: 24, borderRadius: 12, background: on ? t.ok : t.bg4,
      cursor: disabled ? "not-allowed" : "pointer", padding: 3,
      transition: "background 0.2s", opacity: disabled ? 0.5 : 1,
    }}>
      <div style={{ width: 18, height: 18, borderRadius: "50%", background: "#fff", transform: on ? "translateX(20px)" : "translateX(0)", transition: "transform 0.2s" }} />
    </div>
  );
}

// ─── Credential field with masking + Change/Save/Cancel ──

interface CredItem { key: string; label: string; configured: boolean; }

function CredField({ item, onSaved }: { item: CredItem; onSaved: () => void }) {
  const t = useTheme();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!value.trim()) return;
    setBusy(true);
    try {
      await api.post(`/v1/credentials/${item.key}`, { value: value.trim() });
      setEditing(false); setValue(""); onSaved();
    } catch { /* */ }
    finally { setBusy(false); }
  }

  async function clear() {
    if (!confirm(`Clear ${item.label}?`)) return;
    setBusy(true);
    try {
      await api.del(`/v1/credentials/${item.key}`);
      onSaved();
    } catch { /* */ }
    finally { setBusy(false); }
  }

  return (
    <SF label={item.label} desc={item.key}>
      {item.configured && !editing ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 14, color: t.textDim, letterSpacing: "3px" }}>••••••••</span>
          <Btn variant="ghost" onClick={() => { setEditing(true); setValue(""); }}>Change</Btn>
          <Btn variant="danger" onClick={clear} disabled={busy}>Clear</Btn>
        </div>
      ) : editing ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="password" value={value} onChange={e => setValue(e.target.value)}
            placeholder={`Enter ${item.label}…`} autoFocus
            style={{ padding: "6px 10px", background: t.inp, border: `1px solid ${t.border}`, borderRadius: 6, color: t.text2, fontSize: 13, width: 200, outline: "none" }}
          />
          <Btn variant="primary" onClick={save} disabled={busy || !value.trim()}>
            {busy ? <Spin size={14} /> : "Save"}
          </Btn>
          <Btn variant="ghost" onClick={() => { setEditing(false); setValue(""); }}>Cancel</Btn>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, color: t.textDim }}>Not set</span>
          <Btn variant="primary" onClick={() => { setEditing(true); setValue(""); }}>Set</Btn>
        </div>
      )}
    </SF>
  );
}

// ─── Data Management ──

function DataSection() {
  const t = useTheme();
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.get<Record<string, number>>("/v1/data/counts").then(setCounts).catch(() => {}); }, []);

  async function clear(target: string, dangerous = false) {
    const label = target.replace(/_/g, " ");
    if (dangerous) {
      const typed = prompt(`Type "${target}" to confirm clearing all ${label}:`);
      if (typed !== target) return;
    } else if (!confirm(`Clear all ${label}?`)) return;

    setBusy(true);
    try {
      const r = await api.post<{ rows_deleted: number }>(`/v1/data/clear/${target}`, dangerous ? { confirm: target } : {});
      setMsg(`Cleared ${r.rows_deleted} ${label} rows`);
      const fresh = await api.get<Record<string, number>>("/v1/data/counts"); setCounts(fresh);
    } catch (e) { setMsg(String(e)); }
    finally { setBusy(false); setTimeout(() => setMsg(""), 3000); }
  }

  function DataRow({ target, label, count, dangerous }: { target: string; label: string; count: number; dangerous?: boolean }) {
    return (
      <SF label={label} desc={`${count} rows`}>
        <Btn variant={dangerous ? "danger" : "ghost"} onClick={() => clear(target, dangerous)} disabled={busy || count === 0}>
          {dangerous ? "⚠ Clear" : "Clear"}
        </Btn>
      </SF>
    );
  }

  return (
    <>
      {msg && <div style={{ fontSize: 12, color: t.ok, marginBottom: 8 }}>{msg}</div>}
      <DataRow target="tentative_torrents" label="Tentative torrents" count={counts.tentative_torrents ?? 0} />
      <DataRow target="book_review_queue" label="Pending reviews" count={counts.book_review_queue ?? 0} />
      <DataRow target="ignored_torrents_seen" label="Ignored history" count={counts.ignored_torrents_seen ?? 0} />
      <DataRow target="announces" label="Announce log" count={counts.announces ?? 0} />
      <DataRow target="authors_tentative_review" label="Tentative-review authors" count={counts.authors_tentative_review ?? 0} />
      <DataRow target="calibre_additions" label="Calibre additions log" count={counts.calibre_additions ?? 0} />
      <DataRow target="authors_allowed" label="Allowed authors" count={counts.authors_allowed ?? 0} dangerous />
      <DataRow target="authors_ignored" label="Ignored authors" count={counts.authors_ignored ?? 0} dangerous />
      <DataRow target="grabs" label="All grabs" count={counts.grabs ?? 0} dangerous />
    </>
  );
}


// ─── Main Settings Page ──

export default function SettingsPage() {
  const t = useTheme();
  const [s, setS] = useState<S | null>(null);
  const [creds, setCreds] = useState<CredItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => { api.get<S>("/v1/settings").then(setS).catch(() => {}); }, []);
  const loadCreds = () => api.get<{ items: CredItem[] }>("/v1/credentials").then(r => setCreds(r.items)).catch(() => {});
  useEffect(() => { loadCreds(); }, []);

  if (!s) return <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spin /></div>;

  const upd = (k: string, v: unknown) => setS(o => o ? { ...o, [k]: v } : o);
  const ist = { padding: "8px 12px", background: t.inp, border: `1px solid ${t.border}`, borderRadius: 6, color: t.text2, fontSize: 13 } as const;
  const nist = { ...ist, width: 80 } as const;

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      await api.patch("/v1/settings", s);
      setMsg("Saved!"); const fresh = await api.get<S>("/v1/settings"); setS(fresh);
      setTimeout(() => setMsg(""), 2000);
    } catch { setMsg("Error"); }
    setSaving(false);
  };

  // Group creds by service
  const mamCreds = creds.filter(c => c.key.startsWith("mam_"));
  const qbitCreds = creds.filter(c => c.key.startsWith("qbit_"));
  const otherCreds = creds.filter(c => !c.key.startsWith("mam_") && !c.key.startsWith("qbit_"));

  return (
    <div style={{ paddingBottom: 40 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: t.text, margin: 0 }}>Settings</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {msg && <span style={{ fontSize: 13, fontWeight: 600, color: msg === "Error" ? t.err : t.ok }}>{msg}</span>}
          <Btn variant="primary" onClick={save} disabled={saving}>{saving ? <Spin size={14} /> : "Save Settings"}</Btn>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>

        {/* ── Pipeline Controls ── */}
        <SSection title="Pipeline">
          <SF label="IRC Listener" desc="Watch MAM #announce for new torrents. Disable to pause all automatic grabbing.">
            <STog on={(s.mam_irc_enabled as boolean) ?? true} onToggle={() => upd("mam_irc_enabled", !(s.mam_irc_enabled ?? true))} />
          </SF>
          <SF label="qBittorrent Watcher" desc="Poll qBit for download completions and budget reconciliation.">
            <STog on={(s.pipeline_qbit_watcher_enabled as boolean) ?? true} onToggle={() => upd("pipeline_qbit_watcher_enabled", !(s.pipeline_qbit_watcher_enabled ?? true))} />
          </SF>
          <SF label="Auto-Train Authors" desc="Add co-authors to the allow list when a book is grabbed.">
            <STog on={(s.pipeline_auto_train_enabled as boolean) ?? true} onToggle={() => upd("pipeline_auto_train_enabled", !(s.pipeline_auto_train_enabled ?? true))} />
          </SF>
          <SF label="Notifications" desc="Daily/weekly digests and per-event ntfy pings.">
            <STog on={(s.pipeline_notifications_enabled as boolean) ?? true} onToggle={() => upd("pipeline_notifications_enabled", !(s.pipeline_notifications_enabled ?? true))} />
          </SF>
          <SF label="Dry Run" desc="Run filter + policy but never fetch .torrent files or talk to qBit." warn={s.dry_run ? "Active — no torrents will be downloaded" : undefined}>
            <STog on={!!s.dry_run} onToggle={() => upd("dry_run", !s.dry_run)} />
          </SF>
        </SSection>

        {/* ── Review & Enrichment ── */}
        <SSection title="Review & Enrichment">
          <SF label="Manual Review Queue" desc="Every downloaded book goes through approval before Calibre delivery.">
            <STog on={(s.review_queue_enabled as boolean) ?? true} onToggle={() => upd("review_queue_enabled", !(s.review_queue_enabled ?? true))} />
          </SF>
          <SF label="Metadata Enrichment" desc="Scrape Goodreads, Amazon, Hardcover, etc. for covers and rich metadata.">
            <STog on={!!s.metadata_enrichment_enabled} onToggle={() => upd("metadata_enrichment_enabled", !s.metadata_enrichment_enabled)} />
          </SF>
          <SF label="Review Timeout (days)" desc="Auto-add undecided items after this many days with basic metadata.">
            <input type="number" min={1} value={s.metadata_review_timeout_days as number ?? 14}
              onChange={e => upd("metadata_review_timeout_days", parseInt(e.target.value) || 14)} style={nist} />
          </SF>
          <SF label="Match Confidence" desc="Enricher short-circuits when a source scores ≥ this. Range 0.0–1.0.">
            <input type="number" min={0} max={1} step={0.05} value={s.metadata_accept_confidence as number ?? 0.8}
              onChange={e => upd("metadata_accept_confidence", parseFloat(e.target.value) || 0.8)} style={nist} />
          </SF>
        </SSection>

        {/* ── Snatch Budget ── */}
        <SSection title="Snatch Budget">
          <SF label="Budget Cap" desc="Max active snatches before new grabs queue or delay.">
            <input type="number" min={1} value={s.snatch_budget_cap as number ?? 200}
              onChange={e => upd("snatch_budget_cap", parseInt(e.target.value) || 200)} style={nist} />
          </SF>
          <SF label="Queue Max" desc="Pending queue size before FIFO eviction to the delayed folder.">
            <input type="number" min={1} value={s.snatch_queue_max as number ?? 200}
              onChange={e => upd("snatch_queue_max", parseInt(e.target.value) || 200)} style={nist} />
          </SF>
          <SF label="Excluded Uploaders" desc="MAM usernames whose uploads are never grabbed. One per line.">
            <textarea
              value={((s.excluded_uploaders as string[]) ?? []).join("\n")}
              onChange={e => upd("excluded_uploaders", e.target.value.split("\n").map(s => s.trim()).filter(Boolean))}
              rows={2} placeholder="Turtles81"
              style={{ ...ist, width: 180, resize: "vertical", fontFamily: "inherit" }}
            />
          </SF>
        </SSection>

        {/* ── Policy ── */}
        <SSection title="Grab Policy">
          <SF label="Always Grab VIP" desc="VIP torrents bypass ratio checks and wedge logic.">
            <STog on={(s.policy_vip_always_grab as boolean) ?? true} onToggle={() => upd("policy_vip_always_grab", !(s.policy_vip_always_grab ?? true))} />
          </SF>
          <SF label="Free Only" desc="Only grab free torrents (VIP, global FL, personal FL, or wedge).">
            <STog on={!!s.policy_free_only} onToggle={() => upd("policy_free_only", !s.policy_free_only)} />
          </SF>
          <SF label="Use Wedges" desc="Spend freeleech wedges to make non-free torrents free.">
            <STog on={!!s.policy_use_wedge} onToggle={() => upd("policy_use_wedge", !s.policy_use_wedge)} />
          </SF>
          <SF label="Ratio Floor" desc="Skip non-free torrents when your ratio drops below this value. 0 = disabled.">
            <input type="number" min={0} step={0.1} value={s.policy_ratio_floor as number ?? 0}
              onChange={e => upd("policy_ratio_floor", parseFloat(e.target.value) || 0)} style={nist} />
          </SF>
        </SSection>

        {/* ── Notifications ── */}
        <SSection title="Notifications">
          <SF label="Daily Digest" desc="Summary of accepted, tentative, and ignored activity.">
            <STog on={(s.daily_digest_enabled as boolean) ?? true} onToggle={() => upd("daily_digest_enabled", !(s.daily_digest_enabled ?? true))} />
          </SF>
          <SF label="Digest Hour" desc="Local time (0–23) when the daily digest fires.">
            <input type="number" min={0} max={23} value={s.daily_digest_hour as number ?? 9}
              onChange={e => upd("daily_digest_hour", parseInt(e.target.value) || 9)} style={nist} />
          </SF>
          <SF label="Per-Event Pings" desc="ntfy notification on every grab and every download completion.">
            <STog on={!!s.per_event_notifications} onToggle={() => upd("per_event_notifications", !s.per_event_notifications)} />
          </SF>
        </SSection>

        {/* ── MAM Credentials ── */}
        <SSection title="MyAnonamouse">
          {mamCreds.map(c => <CredField key={c.key} item={c} onSaved={loadCreds} />)}
        </SSection>

        {/* ── qBit Credentials ── */}
        <SSection title="qBittorrent">
          {qbitCreds.map(c => <CredField key={c.key} item={c} onSaved={loadCreds} />)}
          <SF label="Watch Category" desc="qBit category for Hermeece-managed torrents.">
            <input value={(s.qbit_watch_category as string) || "[mam-reseed]"}
              onChange={e => upd("qbit_watch_category", e.target.value)}
              style={{ ...ist, width: 160 }} />
          </SF>
          <SF label="Download Path" desc="Base download directory as seen by qBit (e.g. /data/[mam-complete]).">
            <input value={(s.qbit_download_path as string) || ""}
              onChange={e => upd("qbit_download_path", e.target.value)}
              placeholder="/data/[mam-complete]" style={{ ...ist, width: 200 }} />
          </SF>
          <SF label="Monthly Folders" desc="Organize downloads into [YYYY-MM] subfolders.">
            <STog on={(s.monthly_download_folders as boolean) ?? true} onToggle={() => upd("monthly_download_folders", !(s.monthly_download_folders ?? true))} />
          </SF>
        </SSection>

        {/* ── Other Credentials ── */}
        <SSection title="API Keys & Services" defaultOpen={false}>
          {otherCreds.map(c => <CredField key={c.key} item={c} onSaved={loadCreds} />)}
          <SF label="ntfy Topic" desc="Topic name for ntfy.sh notifications.">
            <input value={(s.ntfy_topic as string) || "hermeece"}
              onChange={e => upd("ntfy_topic", e.target.value)}
              style={{ ...ist, width: 160 }} />
          </SF>
          <SF label="Default Sink" desc="Where approved books go: cwa, calibre, folder, or audiobookshelf.">
            <select value={(s.default_sink as string) || "cwa"}
              onChange={e => upd("default_sink", e.target.value)}
              style={{ ...ist, width: 140, cursor: "pointer", appearance: "auto" }}>
              <option value="cwa">CWA</option>
              <option value="calibre">Calibre</option>
              <option value="folder">Folder</option>
              <option value="audiobookshelf">Audiobookshelf</option>
            </select>
          </SF>
        </SSection>

        {/* ── Operational ── */}
        <SSection title="Operational" defaultOpen={false}>
          <SF label="Verbose Logging" desc="Enable DEBUG-level log output.">
            <STog on={!!s.verbose_logging} onToggle={() => upd("verbose_logging", !s.verbose_logging)} />
          </SF>
        </SSection>

        {/* ── Data Management ── */}
        <SSection title="Data Management" defaultOpen={false}>
          <DataSection />
        </SSection>

      </div>
    </div>
  );
}

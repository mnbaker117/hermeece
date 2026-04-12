// SettingsPage — operator-facing knobs.
//
// Reads the current settings from /api/v1/settings (secrets redacted)
// and exposes a curated subset as form controls. Edits are sent as
// sparse PATCH requests — only the dirty fields are included.
//
// Secret fields (MAM cookie, qBit password, ntfy URL) are NOT
// edited here. They show as read-only "configured / not configured"
// badges. A dedicated Credentials page (v1.0) will own secret entry
// via the future secret store.
import { useEffect, useState, type ReactNode } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme, type Theme } from "../theme";

type SettingsMap = Record<string, unknown>;

interface PatchResponse {
  ok: boolean;
  updated: string[];
  rejected: string[];
}

export default function SettingsPage() {
  const theme = useTheme();
  const [current, setCurrent] = useState<SettingsMap | null>(null);
  const [draft, setDraft] = useState<SettingsMap>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const r = await api.get<SettingsMap>("/v1/settings");
      setCurrent(r);
      setDraft({});
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  const effective: SettingsMap = { ...(current ?? {}), ...draft };

  function setField(key: string, value: unknown) {
    setDraft((d) => {
      const next = { ...d, [key]: value };
      if (current && current[key] === value) delete next[key];
      return next;
    });
    setOk(null);
  }

  async function save() {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    setError(null);
    setOk(null);
    try {
      const r = await api.patch<PatchResponse>("/v1/settings", draft);
      if (r.rejected.length > 0) {
        setError(`Rejected keys: ${r.rejected.join(", ")}`);
      } else {
        setOk(`Updated ${r.updated.length} field(s).`);
      }
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (current === null) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
        <Spin />
      </div>
    );
  }

  const dirty = Object.keys(draft).length;

  return (
    <div>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 700,
          color: theme.text,
          marginBottom: 4,
        }}
      >
        Settings
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 24 }}>
        Operator knobs. Changes save to <code>settings.json</code> and
        rebuild the dispatcher in place — no restart required.
      </p>

      {error && <Banner tone="err">{error}</Banner>}
      {ok && <Banner tone="ok">{ok}</Banner>}

      <Section
        title="Review &amp; enrichment"
        subtitle="Manual-review queue and Tier 4 metadata scraping."
      >
        <BoolField
          label="Review queue enabled"
          hint="Every downloaded book goes through manual approval before delivery."
          field="review_queue_enabled"
          value={effective.review_queue_enabled as boolean}
          onChange={(v) => setField("review_queue_enabled", v)}
        />
        <BoolField
          label="Metadata enrichment enabled"
          hint="Hit Goodreads etc. for covers + rich metadata."
          field="metadata_enrichment_enabled"
          value={effective.metadata_enrichment_enabled as boolean}
          onChange={(v) => setField("metadata_enrichment_enabled", v)}
        />
        <NumberField
          label="Review timeout (days)"
          hint="Auto-add undecided items after this many days."
          field="metadata_review_timeout_days"
          value={effective.metadata_review_timeout_days as number}
          onChange={(v) => setField("metadata_review_timeout_days", v)}
        />
        <NumberField
          label="Enrichment accept confidence"
          hint="First provider whose match scores ≥ this short-circuits. 0.0–1.0."
          field="metadata_accept_confidence"
          value={effective.metadata_accept_confidence as number}
          step={0.05}
          onChange={(v) => setField("metadata_accept_confidence", v)}
        />
      </Section>

      <Section
        title="Uploader exclusion"
        subtitle="MAM usernames whose uploads should never be grabbed. Prevents downloading your own torrents."
      >
        <ListField
          label="Excluded uploaders"
          hint="One username per line. Case-insensitive."
          field="excluded_uploaders"
          value={(effective.excluded_uploaders as string[]) ?? []}
          onChange={(v) => setField("excluded_uploaders", v)}
        />
      </Section>

      <Section
        title="Snatch budget"
        subtitle="Rate limiting for the MAM active-snatches cap."
      >
        <NumberField
          label="Budget cap"
          hint="Max active snatches before new grabs get queued or delayed."
          field="snatch_budget_cap"
          value={effective.snatch_budget_cap as number}
          onChange={(v) => setField("snatch_budget_cap", v)}
        />
        <NumberField
          label="Queue max"
          hint="How many grabs can wait in the pending queue before FIFO eviction to the delayed folder."
          field="snatch_queue_max"
          value={effective.snatch_queue_max as number}
          onChange={(v) => setField("snatch_queue_max", v)}
        />
      </Section>

      <Section
        title="Policy"
        subtitle="VIP, freeleech, and ratio guards applied after the filter."
      >
        <BoolField
          label="Always grab VIP torrents"
          field="policy_vip_always_grab"
          value={effective.policy_vip_always_grab as boolean}
          onChange={(v) => setField("policy_vip_always_grab", v)}
        />
        <BoolField
          label="Only grab free torrents"
          hint="VIP, global FL, personal FL, or wedge-applied."
          field="policy_free_only"
          value={effective.policy_free_only as boolean}
          onChange={(v) => setField("policy_free_only", v)}
        />
        <NumberField
          label="Ratio floor"
          hint="Skip non-free torrents when ratio is below this. 0 = disabled."
          field="policy_ratio_floor"
          value={effective.policy_ratio_floor as number}
          step={0.1}
          onChange={(v) => setField("policy_ratio_floor", v)}
        />
      </Section>

      <Section
        title="Notifications"
        subtitle="ntfy digests and per-event pings."
      >
        <BoolField
          label="Daily digest enabled"
          field="daily_digest_enabled"
          value={effective.daily_digest_enabled as boolean}
          onChange={(v) => setField("daily_digest_enabled", v)}
        />
        <NumberField
          label="Daily digest hour (local)"
          hint="0–23. Fires once per day at this hour."
          field="daily_digest_hour"
          value={effective.daily_digest_hour as number}
          onChange={(v) => setField("daily_digest_hour", v)}
        />
        <BoolField
          label="Per-event notifications"
          hint="Send a ntfy for every grab and every completion. Off by default — can be noisy."
          field="per_event_notifications"
          value={effective.per_event_notifications as boolean}
          onChange={(v) => setField("per_event_notifications", v)}
        />
      </Section>

      <Section
        title="Pipeline controls"
        subtitle="Enable or disable stages of the pipeline. Useful for testing, maintenance, or pausing while you're away."
      >
        <BoolField
          label="IRC listener"
          hint="Watch MAM #announce for new torrents. Disable to pause all automatic grabbing."
          field="pipeline_irc_enabled"
          value={(effective.mam_irc_enabled as boolean) ?? true}
          onChange={(v) => setField("mam_irc_enabled", v)}
        />
        <BoolField
          label="qBit watcher"
          hint="Poll qBittorrent for download completions + budget reconciliation."
          field="pipeline_qbit_watcher_enabled"
          value={(effective.pipeline_qbit_watcher_enabled as boolean) ?? true}
          onChange={(v) => setField("pipeline_qbit_watcher_enabled", v)}
        />
        <BoolField
          label="Auto-train authors"
          hint="Add co-authors to the allow list when a book is grabbed."
          field="pipeline_auto_train_enabled"
          value={(effective.pipeline_auto_train_enabled as boolean) ?? true}
          onChange={(v) => setField("pipeline_auto_train_enabled", v)}
        />
        <BoolField
          label="Notifications"
          hint="Send daily/weekly digests and per-event ntfy notifications."
          field="pipeline_notifications_enabled"
          value={(effective.pipeline_notifications_enabled as boolean) ?? true}
          onChange={(v) => setField("pipeline_notifications_enabled", v)}
        />
        <BoolField
          label="Dry run"
          hint="Run the filter + policy but never actually fetch .torrent files or talk to qBit."
          field="dry_run"
          value={effective.dry_run as boolean}
          onChange={(v) => setField("dry_run", v)}
        />
      </Section>

      <Section
        title="Sink configuration"
        subtitle="Where approved books are delivered."
      >
        <ListField
          label="Default sink"
          hint="cwa, calibre, folder, or audiobookshelf."
          field="default_sink"
          value={[(effective.default_sink as string) || "cwa"]}
          onChange={(v) => setField("default_sink", v[0] || "cwa")}
        />
      </Section>

      <CredentialsSection />

      <div
        style={{
          position: "sticky",
          bottom: 20,
          display: "flex",
          justifyContent: "flex-end",
          gap: 10,
          background: theme.bg + "ee",
          backdropFilter: "blur(8px)",
          padding: "12px 0",
          borderTop: `1px solid ${theme.borderL}`,
          marginTop: 20,
        }}
      >
        <span style={{ fontSize: 13, color: theme.textDim, alignSelf: "center" }}>
          {dirty > 0 ? `${dirty} unsaved change(s)` : "No unsaved changes"}
        </span>
        <Btn
          variant="ghost"
          disabled={dirty === 0 || saving}
          onClick={() => setDraft({})}
        >
          Discard
        </Btn>
        <Btn
          variant="primary"
          disabled={dirty === 0 || saving}
          onClick={save}
        >
          {saving ? <Spin size={14} /> : "Save"}
        </Btn>
      </div>
    </div>
  );
}

function BoolField({
  label,
  hint,
  field,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  field: string;
  value: boolean | undefined;
  onChange: (v: boolean) => void;
}) {
  const theme = useTheme();
  return (
    <FieldShell label={label} hint={hint} field={field} theme={theme}>
      <button
        onClick={() => onChange(!value)}
        aria-pressed={!!value}
        style={{
          width: 44,
          height: 24,
          borderRadius: 12,
          border: `1px solid ${theme.border}`,
          background: value ? theme.accent : theme.bg3,
          position: "relative",
          cursor: "pointer",
          transition: "background 0.15s",
        }}
      >
        <span
          style={{
            display: "block",
            width: 18,
            height: 18,
            borderRadius: "50%",
            background: theme.bg2,
            position: "absolute",
            top: 2,
            left: value ? 22 : 2,
            transition: "left 0.15s",
          }}
        />
      </button>
    </FieldShell>
  );
}

function NumberField({
  label,
  hint,
  field,
  value,
  onChange,
  step,
}: {
  label: string;
  hint?: string;
  field: string;
  value: number | undefined;
  onChange: (v: number) => void;
  step?: number;
}) {
  const theme = useTheme();
  return (
    <FieldShell label={label} hint={hint} field={field} theme={theme}>
      <input
        type="number"
        value={value ?? 0}
        step={step ?? 1}
        onChange={(e) => {
          const n = e.target.value === "" ? 0 : Number(e.target.value);
          if (!Number.isNaN(n)) onChange(n);
        }}
        style={{
          width: 120,
          padding: "6px 10px",
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          background: theme.inp,
          color: theme.text,
          fontSize: 14,
          outline: "none",
          textAlign: "right",
        }}
      />
    </FieldShell>
  );
}

function ListField({
  label,
  hint,
  field,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  field: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const theme = useTheme();
  const text = value.join("\n");
  return (
    <FieldShell label={label} hint={hint} field={field} theme={theme}>
      <textarea
        value={text}
        onChange={(e) => {
          const lines = e.target.value
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
          onChange(lines);
        }}
        rows={3}
        placeholder="one per line"
        style={{
          width: 220,
          padding: "8px 10px",
          borderRadius: 8,
          border: `1px solid ${theme.border}`,
          background: theme.inp,
          color: theme.text,
          fontSize: 13,
          fontFamily: "inherit",
          resize: "vertical",
          outline: "none",
        }}
      />
    </FieldShell>
  );
}

function FieldShell({
  label,
  hint,
  field,
  theme,
  children,
}: {
  label: string;
  hint?: string;
  field: string;
  theme: Theme;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 16,
        padding: "12px 0",
        borderBottom: `1px solid ${theme.borderL}`,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <label
          htmlFor={field}
          style={{
            fontSize: 14,
            color: theme.text,
            fontWeight: 600,
            display: "block",
          }}
        >
          {label}
        </label>
        {hint && (
          <p
            style={{
              fontSize: 12,
              color: theme.textDim,
              marginTop: 2,
              lineHeight: 1.4,
            }}
          >
            {hint}
          </p>
        )}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

// Credentials section — loads from /api/v1/credentials and provides
// inline set/clear for each secret key, grouped by category.
function CredentialsSection() {
  const theme = useTheme();
  interface CredItem { key: string; label: string; configured: boolean; }
  const [items, setItems] = useState<CredItem[]>([]);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [credBusy, setCredBusy] = useState(false);
  const [credMsg, setCredMsg] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ items: CredItem[] }>("/v1/credentials")
      .then((r) => setItems(r.items))
      .catch(() => {});
  }, []);

  async function saveCred(key: string) {
    if (!editValue.trim()) return;
    setCredBusy(true);
    try {
      await api.post(`/v1/credentials/${key}`, { value: editValue.trim() });
      setCredMsg(`${key} saved.`);
      setEditKey(null);
      setEditValue("");
      const r = await api.get<{ items: CredItem[] }>("/v1/credentials");
      setItems(r.items);
    } catch (e) { setCredMsg(String(e)); }
    finally { setCredBusy(false); }
  }

  async function clearCred(key: string) {
    setCredBusy(true);
    try {
      await api.del(`/v1/credentials/${key}`);
      setCredMsg(`${key} cleared.`);
      const r = await api.get<{ items: CredItem[] }>("/v1/credentials");
      setItems(r.items);
    } catch (e) { setCredMsg(String(e)); }
    finally { setCredBusy(false); }
  }

  const groups: Record<string, CredItem[]> = {
    "MAM": items.filter(i => i.key.startsWith("mam_")),
    "qBittorrent": items.filter(i => i.key.startsWith("qbit_")),
    "Notifications": items.filter(i => i.key === "ntfy_url"),
    "API keys": items.filter(i => i.key === "hardcover_api_key"),
  };

  return (
    <>
      {credMsg && <Banner tone="ok">{credMsg}</Banner>}
      {Object.entries(groups).map(([groupName, groupItems]) => (
        groupItems.length > 0 && (
          <Section key={groupName} title={`${groupName} credentials`} subtitle="Stored encrypted. Values are never shown after saving.">
            {groupItems.map(item => (
              <div key={item.key} style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, padding: "10px 0", borderBottom: `1px solid ${theme.borderL}` }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: theme.text }}>{item.label}</div>
                  <div style={{ fontSize: 11, color: theme.textDim }}>{item.key}</div>
                  {editKey === item.key && (
                    <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                      <input
                        type="password"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        placeholder={`Enter ${item.label}…`}
                        autoFocus
                        style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: `1px solid ${theme.accent}55`, background: theme.bg3, color: theme.text, fontSize: 12, outline: "none" }}
                      />
                      <Btn variant="primary" disabled={credBusy || !editValue.trim()} onClick={() => saveCred(item.key)}>Save</Btn>
                      <Btn variant="ghost" onClick={() => { setEditKey(null); setEditValue(""); }}>Cancel</Btn>
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
                  <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 99, background: item.configured ? theme.ok + "22" : theme.textDim + "22", color: item.configured ? theme.ok : theme.textDim, fontWeight: 700 }}>
                    {item.configured ? "SET" : "NOT SET"}
                  </span>
                  {editKey !== item.key && (
                    <Btn variant="ghost" onClick={() => { setEditKey(item.key); setEditValue(""); setCredMsg(null); }}>
                      {item.configured ? "Update" : "Set"}
                    </Btn>
                  )}
                  {item.configured && editKey !== item.key && (
                    <Btn variant="danger" disabled={credBusy} onClick={() => clearCred(item.key)}>Clear</Btn>
                  )}
                </div>
              </div>
            ))}
          </Section>
        )
      ))}
    </>
  );
}

function Banner({
  tone,
  children,
}: {
  tone: "ok" | "err";
  children: ReactNode;
}) {
  const theme = useTheme();
  const color = tone === "ok" ? theme.ok : theme.err;
  return (
    <div
      style={{
        background: color + "22",
        border: `1px solid ${color}55`,
        color,
        padding: "10px 14px",
        borderRadius: 8,
        fontSize: 13,
        marginBottom: 16,
      }}
    >
      {children}
    </div>
  );
}

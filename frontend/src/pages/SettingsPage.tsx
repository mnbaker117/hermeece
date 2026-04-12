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
      const r = await api.post<PatchResponse>("/v1/settings", draft);
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
        title="Credentials"
        subtitle="Secrets are entered via the server configuration for now. Dedicated editor coming before v1.0."
      >
        <SecretRow label="MAM session cookie" configured={!!effective.mam_session_id_configured} />
        <SecretRow label="qBit password" configured={!!effective.qbit_password_configured} />
        <SecretRow label="ntfy URL" configured={!!effective.ntfy_url_configured} />
      </Section>

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

function SecretRow({
  label,
  configured,
}: {
  label: string;
  configured: boolean;
}) {
  const theme = useTheme();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 0",
        borderBottom: `1px solid ${theme.borderL}`,
        fontSize: 13,
      }}
    >
      <span style={{ color: theme.text2 }}>{label}</span>
      <span
        style={{
          fontSize: 11,
          padding: "3px 10px",
          borderRadius: 99,
          background: configured ? theme.ok + "22" : theme.textDim + "22",
          color: configured ? theme.ok : theme.textDim,
          fontWeight: 600,
        }}
      >
        {configured ? "CONFIGURED" : "NOT SET"}
      </span>
    </div>
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

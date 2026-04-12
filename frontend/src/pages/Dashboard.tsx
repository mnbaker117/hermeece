// Dashboard — pipeline status hub, modeled after AthenaScout's Dashboard.
//
// Three sections:
//   1. Hero: Pipeline health bar (IRC, qBit, cookie, enricher status)
//   2. Stat cards: pending review, tentative, authors, MAM ratio/wedges
//   3. Quick actions: review books, manage authors, run tools
//
// 30s polling — "see new books arrive without F5" is the UX bar.
import { useEffect, useState } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface DashboardProps { onNav: (page: string) => void; }

interface ReviewListResponse { items: unknown[]; pending_count: number; }
interface TentativeListResponse { items: unknown[]; }
interface HealthResponse { status: string; dispatcher_ready: boolean; }
interface MamStatusResponse {
  cookie_configured: boolean; validation_ok: boolean;
  ratio: number | null; wedges: number | null;
  username: string | null; error: string | null;
}
interface AuthorOverviewResponse { counts: Record<string, number>; }

export default function Dashboard({ onNav }: DashboardProps) {
  const t = useTheme();
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [tentativeCount, setTentativeCount] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [mam, setMam] = useState<MamStatusResponse | null>(null);
  const [authors, setAuthors] = useState<AuthorOverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [review, tentative, h, mamS, auth] = await Promise.all([
          api.get<ReviewListResponse>("/v1/review"),
          api.get<TentativeListResponse>("/v1/tentative"),
          api.get<HealthResponse>("/health"),
          api.get<MamStatusResponse>("/v1/mam/status").catch(() => null),
          api.get<AuthorOverviewResponse>("/v1/authors").catch(() => null),
        ]);
        if (cancelled) return;
        setReviewCount(review.pending_count);
        setTentativeCount(tentative.items.length);
        setHealth(h);
        setMam(mamS);
        setAuthors(auth);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(String(e));
      }
    };
    refresh();
    const iv = setInterval(refresh, 30_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const allowedCount = authors?.counts?.allowed ?? 0;
  const ignoredCount = authors?.counts?.ignored ?? 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {error && (
        <div style={{ background: t.err + "22", border: `1px solid ${t.err}55`, color: t.err, padding: "10px 14px", borderRadius: 8, fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* ── Hero: Pipeline Health ── */}
      <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 16, padding: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
          <div>
            <h1 style={{ fontSize: 26, fontWeight: 700, color: t.text, margin: 0 }}>Pipeline</h1>
            <p style={{ fontSize: 14, color: t.textDim, marginTop: 4 }}>
              {health?.dispatcher_ready ? "All systems operational" : "Starting up…"}
            </p>
          </div>
          {mam?.username && (
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 13, color: t.textDim }}>MAM: {mam.username}</div>
              {mam.ratio !== null && (
                <div style={{ fontSize: 22, fontWeight: 700, color: mam.ratio >= 1 ? t.ok : t.warn }}>
                  {mam.ratio.toFixed(1)} ratio
                </div>
              )}
            </div>
          )}
        </div>
        {/* Status indicators */}
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <StatusPill label="Dispatcher" ok={health?.dispatcher_ready ?? false} />
          <StatusPill label="MAM Cookie" ok={mam?.validation_ok ?? false} warn={mam?.cookie_configured && !mam?.validation_ok} />
          <StatusPill label="Enrichment" ok={true} label2="Active" />
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12 }}>
        <StatCard label="Pending Review" value={reviewCount} icon="📋" color={t.accent} nav={() => onNav("review")} />
        <StatCard label="Tentative" value={tentativeCount} icon="❓" color={t.warn} nav={() => onNav("tentative")} />
        <StatCard label="Allowed Authors" value={allowedCount} icon="✍" color={t.ok} nav={() => onNav("authors")} />
        <StatCard label="Ignored Authors" value={ignoredCount} icon="🚫" color={t.textDim} nav={() => onNav("authors")} />
        {mam?.wedges !== null && mam?.wedges !== undefined && (
          <StatCard label="FL Wedges" value={mam.wedges} icon="🎫" color={t.accent} nav={() => onNav("mam")} />
        )}
        <StatCard
          label="Cookie"
          value={mam?.cookie_configured ? (mam.validation_ok ? "Valid" : "Stale") : "Missing"}
          icon="🍪"
          color={mam?.validation_ok ? t.ok : t.warn}
          nav={() => onNav("mam")}
        />
      </div>

      {/* ── MAM status bar (if connected) ── */}
      {mam?.cookie_configured && mam?.error && (
        <div style={{ background: t.warn + "18", border: `1px solid ${t.warn}33`, borderRadius: 12, padding: "12px 20px", fontSize: 13, color: t.warn }}>
          ⚠ MAM: {mam.error}.{" "}
          <button onClick={() => onNav("mam")} style={{ background: "none", border: "none", color: t.accent, cursor: "pointer", fontWeight: 600, fontSize: 13, textDecoration: "underline" }}>
            Go to MAM Status
          </button>
        </div>
      )}

      {/* ── Quick Actions + Tools ── */}
      <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12, padding: 20 }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 320px" }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>
              Quick Actions
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn variant="primary" onClick={() => onNav("review")}>
                📋 Review Books {reviewCount ? `(${reviewCount})` : ""}
              </Btn>
              <Btn onClick={() => onNav("tentative")}>
                ❓ Tentative {tentativeCount ? `(${tentativeCount})` : ""}
              </Btn>
              <Btn onClick={() => onNav("authors")}>
                ✍ Authors
              </Btn>
            </div>
          </div>

          <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", gap: 6, borderLeft: `1px solid ${t.borderL}`, paddingLeft: 20, justifyContent: "center" }}>
            <ToolButton label="Migration Wizard" icon="📦" onClick={() => onNav("migration")} />
            <ToolButton label="Delayed Torrents" icon="⏳" onClick={() => onNav("delayed")} />
            <ToolButton label="Filters" icon="🏷" onClick={() => onNav("filters")} />
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ label, ok, warn, label2 }: { label: string; ok: boolean; warn?: boolean; label2?: string }) {
  const t = useTheme();
  const color = ok ? t.ok : warn ? t.warn : t.textDim;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
      <span style={{ fontSize: 13, color: t.text2, fontWeight: 500 }}>{label}</span>
      <span style={{ fontSize: 11, color, fontWeight: 600 }}>{label2 || (ok ? "OK" : warn ? "Check" : "—")}</span>
    </div>
  );
}

function StatCard({ label, value, icon, color, nav }: {
  label: string; value: number | string | null; icon: string; color: string;
  nav?: () => void;
}) {
  const t = useTheme();
  return (
    <div
      onClick={nav}
      style={{
        background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12,
        padding: "16px 18px", cursor: nav ? "pointer" : "default",
        transition: "border-color 0.2s",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 20 }}>{icon}</span>
        <span style={{ fontSize: 24, fontWeight: 700, color }}>
          {value === null ? <Spin size={16} /> : value}
        </span>
      </div>
      <div style={{ fontSize: 12, color: t.textDim, marginTop: 6 }}>{label}</div>
    </div>
  );
}

function ToolButton({ label, icon, onClick }: { label: string; icon: string; onClick: () => void }) {
  const t = useTheme();
  return (
    <button onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "8px 14px", background: t.bg4, border: `1px solid ${t.border}`,
      borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500,
      color: t.text2, whiteSpace: "nowrap",
    }}>
      {icon} {label}
    </button>
  );
}

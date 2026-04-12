// Dashboard — pipeline control center.
//
// Modeled after AthenaScout's dashboard: hero status, stat grid,
// pipeline health, quick actions with context, and tools sidebar.
import { useEffect, useState } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";
import { fmtNum, fmtBytes, fmtRatio } from "../lib/format";

interface DashboardProps { onNav: (page: string) => void; }

interface ReviewListResponse { items: unknown[]; pending_count: number; }
interface TentativeListResponse { items: unknown[]; }
interface HealthResponse { status: string; dispatcher_ready: boolean; }
interface MamStatusResponse {
  cookie_configured: boolean; validation_ok: boolean;
  ratio: number | null; wedges: number | null; seedbonus: number | null;
  username: string | null; classname: string | null;
  uploaded_bytes: number | null; downloaded_bytes: number | null;
  error: string | null;
}
interface AuthorOverviewResponse { counts: Record<string, number>; }
interface DataCounts { [key: string]: number; }

export default function Dashboard({ onNav }: DashboardProps) {
  const t = useTheme();
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [tentativeCount, setTentativeCount] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [mam, setMam] = useState<MamStatusResponse | null>(null);
  const [authors, setAuthors] = useState<AuthorOverviewResponse | null>(null);
  const [counts, setCounts] = useState<DataCounts | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [review, tentative, h, mamS, auth, cnt] = await Promise.all([
          api.get<ReviewListResponse>("/v1/review"),
          api.get<TentativeListResponse>("/v1/tentative"),
          api.get<HealthResponse>("/health"),
          api.get<MamStatusResponse>("/v1/mam/status").catch(() => null),
          api.get<AuthorOverviewResponse>("/v1/authors").catch(() => null),
          api.get<DataCounts>("/v1/data/counts").catch(() => null),
        ]);
        if (cancelled) return;
        setReviewCount(review.pending_count);
        setTentativeCount(tentative.items.length);
        setHealth(h); setMam(mamS); setAuthors(auth); setCounts(cnt);
        setError(null);
      } catch (e) { if (!cancelled) setError(String(e)); }
    };
    refresh();
    const iv = setInterval(refresh, 30_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const allowed = authors?.counts?.allowed ?? 0;
  const ignored = authors?.counts?.ignored ?? 0;
  const grabs = counts?.grabs ?? 0;
  const calibreAdds = counts?.calibre_additions ?? 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {error && (
        <div style={{ background: t.err + "22", border: `1px solid ${t.err}55`, color: t.err, padding: "10px 14px", borderRadius: 8, fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* ── Hero: Pipeline Status ── */}
      <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 16, padding: "28px 32px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 20 }}>
          <div>
            <h1 style={{ fontSize: 30, fontWeight: 700, color: t.text, margin: 0 }}>Pipeline Status</h1>
            <p style={{ fontSize: 15, color: t.textDim, marginTop: 6 }}>
              {health?.dispatcher_ready
                ? `${fmtNum(grabs)} total grabs · ${fmtNum(calibreAdds)} books added to Calibre`
                : "Starting up…"}
            </p>
            {/* Status pills row */}
            <div style={{ display: "flex", gap: 20, marginTop: 16, flexWrap: "wrap" }}>
              <StatusPill label="Dispatcher" ok={health?.dispatcher_ready ?? false} />
              <StatusPill label="IRC Listener" ok={health?.dispatcher_ready ?? false} />
              <StatusPill label="MAM Cookie" ok={mam?.validation_ok ?? false} warn={mam?.cookie_configured === true && !mam?.validation_ok} />
              <StatusPill label="Budget Watcher" ok={health?.dispatcher_ready ?? false} />
            </div>
          </div>

          {/* MAM account summary */}
          {mam?.username && (
            <div style={{ background: t.bg3, borderRadius: 12, padding: "16px 20px", minWidth: 200, textAlign: "right" }}>
              <div style={{ fontSize: 12, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
                MAM · {mam.username}
              </div>
              {mam.classname && <div style={{ fontSize: 11, color: t.textDim, marginTop: 2 }}>{mam.classname}</div>}
              <div style={{ marginTop: 10, display: "flex", gap: 20, justifyContent: "flex-end" }}>
                {mam.ratio !== null && (
                  <div>
                    <div style={{ fontSize: 28, fontWeight: 700, color: mam.ratio >= 1 ? t.ok : t.warn }}>{fmtRatio(mam.ratio)}</div>
                    <div style={{ fontSize: 11, color: t.textDim }}>Ratio</div>
                  </div>
                )}
                {mam.wedges !== null && (
                  <div>
                    <div style={{ fontSize: 28, fontWeight: 700, color: t.accent }}>{fmtNum(mam.wedges)}</div>
                    <div style={{ fontSize: 11, color: t.textDim }}>Wedges</div>
                  </div>
                )}
              </div>
              {(mam.uploaded_bytes || mam.downloaded_bytes) && (
                <div style={{ fontSize: 11, color: t.textDim, marginTop: 8 }}>
                  ↑ {fmtBytes(mam.uploaded_bytes)} · ↓ {fmtBytes(mam.downloaded_bytes)}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* MAM warning banner */}
      {mam?.cookie_configured && mam?.error && (
        <div style={{ background: t.warn + "18", border: `1px solid ${t.warn}33`, borderRadius: 10, padding: "12px 20px", fontSize: 13, color: t.warn, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>⚠ MAM: {mam.error}</span>
          <button onClick={() => onNav("mam")} style={{ background: "none", border: "none", color: t.accent, cursor: "pointer", fontWeight: 600, fontSize: 13, textDecoration: "underline" }}>
            Fix →
          </button>
        </div>
      )}

      {/* ── Stat Cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 14 }}>
        <StatCard label="Books to Review" value={reviewCount} icon="📚" color={(reviewCount ?? 0) > 0 ? t.accent : t.textDim} nav={() => onNav("review")} highlight={(reviewCount ?? 0) > 0} />
        <StatCard label="New Authors" value={tentativeCount} icon="🔎" color={(tentativeCount ?? 0) > 0 ? t.warn : t.textDim} nav={() => onNav("tentative")} highlight={(tentativeCount ?? 0) > 0} />
        <StatCard label="Allowed" value={allowed} icon="✅" color={t.ok} nav={() => onNav("authors")} />
        <StatCard label="Ignored" value={ignored} icon="⛔" color={t.textDim} nav={() => onNav("authors")} />
        <StatCard label="To Calibre" value={calibreAdds} icon="📖" color={t.ok} />
        <StatCard label="Total Grabs" value={grabs} icon="📥" color={t.text2} />
      </div>

      {/* ── Quick Actions + Tools ── */}
      <div style={{ background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 12, padding: 24 }}>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          {/* Quick Actions */}
          <div style={{ flex: "1 1 340px" }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 14 }}>
              Quick Actions
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Btn variant="primary" onClick={() => onNav("review")}>
                📚 Review Books {reviewCount ? `(${reviewCount})` : ""}
              </Btn>
              <Btn onClick={() => onNav("tentative")}>
                🔎 New Authors {tentativeCount ? `(${tentativeCount})` : ""}
              </Btn>
              <Btn onClick={() => onNav("authors")}>
                👤 Author Lists
              </Btn>
              <Btn onClick={() => onNav("filters")}>
                🎯 Edit Filters
              </Btn>
            </div>
          </div>

          {/* Tools */}
          <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", gap: 8, borderLeft: `1px solid ${t.borderL}`, paddingLeft: 24, justifyContent: "center" }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: t.textDim, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
              Tools
            </div>
            <ToolBtn label="Migration Wizard" icon="📦" onClick={() => onNav("migration")} />
            <ToolBtn label="Delayed Torrents" icon="⏳" onClick={() => onNav("delayed")} />
            <ToolBtn label="MAM Account" icon="📡" onClick={() => onNav("mam")} />
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ label, ok, warn }: { label: string; ok: boolean; warn?: boolean }) {
  const t = useTheme();
  const color = ok ? t.ok : warn ? t.warn : t.textDim;
  const text = ok ? "Online" : warn ? "Check" : "Offline";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ width: 10, height: 10, borderRadius: "50%", background: color, boxShadow: ok ? `0 0 6px ${color}66` : "none" }} />
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: t.text2 }}>{label}</div>
        <div style={{ fontSize: 11, color }}>{text}</div>
      </div>
    </div>
  );
}

function StatCard({ label, value, icon, color, nav, highlight }: {
  label: string; value: number | string | null; icon: string; color: string;
  nav?: () => void; highlight?: boolean;
}) {
  const t = useTheme();
  return (
    <div
      onClick={nav}
      style={{
        background: t.bg2, border: `1px solid ${highlight ? color + "55" : t.border}`,
        borderRadius: 12, padding: "20px 22px",
        cursor: nav ? "pointer" : "default",
        transition: "border-color 0.2s, transform 0.1s",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 26 }}>{icon}</span>
        <span style={{ fontSize: 30, fontWeight: 700, color }}>
          {value === null ? <Spin size={20} /> : typeof value === "number" ? fmtNum(value) : value}
        </span>
      </div>
      <div style={{ fontSize: 13, color: t.textDim, marginTop: 10, fontWeight: 500 }}>{label}</div>
    </div>
  );
}

function ToolBtn({ label, icon, onClick }: { label: string; icon: string; onClick: () => void }) {
  const t = useTheme();
  return (
    <button onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "8px 14px", background: t.bg4, border: `1px solid ${t.border}`,
      borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500,
      color: t.text2, whiteSpace: "nowrap", transition: "border-color 0.15s",
    }}>
      <span style={{ fontSize: 16 }}>{icon}</span> {label}
    </button>
  );
}

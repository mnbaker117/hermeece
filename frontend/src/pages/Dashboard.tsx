// Dashboard — high-level snapshot of the live state.
//
// Pulls four counters in parallel on mount:
//   - pending review queue items
//   - pending tentative torrents
//   - the dispatcher health (just /api/health for now)
//
// Cheap polling at 30s — the user spends most of their time here so
// "see new books arrive without F5" is the entire UX bar to clear.
import { useEffect, useState } from "react";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { Btn } from "../components/Btn";
import { api } from "../api";
import { useTheme } from "../theme";

interface DashboardProps {
  onNav: (page: string) => void;
}

interface ReviewListResponse {
  items: unknown[];
  pending_count: number;
}

interface TentativeListResponse {
  items: unknown[];
}

interface HealthResponse {
  status: string;
  service: string;
  dispatcher_ready: boolean;
}

interface MamStatusResponse {
  cookie_configured: boolean;
  validation_ok: boolean;
  ratio: number | null;
  wedges: number | null;
  username: string | null;
  error: string | null;
}

export default function Dashboard({ onNav }: DashboardProps) {
  const theme = useTheme();
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [tentativeCount, setTentativeCount] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [mam, setMam] = useState<MamStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [review, tentative, h, mamS] = await Promise.all([
          api.get<ReviewListResponse>("/v1/review"),
          api.get<TentativeListResponse>("/v1/tentative"),
          api.get<HealthResponse>("/health"),
          api.get<MamStatusResponse>("/v1/mam/status").catch(() => null),
        ]);
        if (cancelled) return;
        setReviewCount(review.pending_count);
        setTentativeCount(tentative.items.length);
        setHealth(h);
        setMam(mamS);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(String(e));
      }
    };
    refresh();
    const iv = setInterval(refresh, 30_000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);

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
        Dashboard
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 24 }}>
        Live snapshot of the Hermeece pipeline.
      </p>

      {error && (
        <div
          style={{
            background: theme.err + "22",
            border: `1px solid ${theme.err}55`,
            color: theme.err,
            padding: "10px 14px",
            borderRadius: 8,
            fontSize: 13,
            marginBottom: 16,
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
          marginBottom: 24,
        }}
      >
        <StatCard
          label="Pending review"
          value={reviewCount}
          onClick={() => onNav("review")}
          highlight={(reviewCount ?? 0) > 0}
        />
        <StatCard
          label="Tentative torrents"
          value={tentativeCount}
          onClick={() => onNav("tentative")}
          highlight={(tentativeCount ?? 0) > 0}
        />
        <StatCard
          label="Dispatcher"
          value={health?.dispatcher_ready ? "Ready" : "—"}
          tone={health?.dispatcher_ready ? "ok" : "dim"}
        />
        <StatCard
          label="MAM ratio"
          value={mam?.ratio !== null && mam?.ratio !== undefined ? mam.ratio.toFixed(2) : "—"}
          onClick={() => onNav("mam")}
          tone={mam?.ratio !== null && mam?.ratio !== undefined ? (mam.ratio >= 1 ? "ok" : undefined) : "dim"}
        />
        <StatCard
          label="Wedges"
          value={mam?.wedges ?? "—"}
          onClick={() => onNav("mam")}
        />
        <StatCard
          label="Cookie"
          value={mam?.cookie_configured ? (mam.validation_ok ? "Valid" : "Stale") : "Missing"}
          onClick={() => onNav("mam")}
          tone={mam?.validation_ok ? "ok" : mam?.cookie_configured ? undefined : "dim"}
          highlight={mam?.cookie_configured === true && !mam?.validation_ok}
        />
      </div>

      <Section
        title="Quick actions"
        subtitle="Most common operator tasks."
      >
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Btn variant="primary" onClick={() => onNav("review")}>
            Review downloaded books
          </Btn>
          <Btn onClick={() => onNav("tentative")}>
            Tentative torrents
          </Btn>
        </div>
      </Section>
    </div>
  );
}

function StatCard({
  label,
  value,
  onClick,
  highlight,
  tone,
}: {
  label: string;
  value: number | string | null;
  onClick?: () => void;
  highlight?: boolean;
  tone?: "ok" | "dim";
}) {
  const theme = useTheme();
  const valueColor =
    tone === "ok"
      ? theme.ok
      : tone === "dim"
        ? theme.textDim
        : highlight
          ? theme.accent
          : theme.text;
  return (
    <div
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      style={{
        background: theme.bg2,
        border: `1px solid ${highlight ? theme.accent + "55" : theme.borderL}`,
        borderRadius: 12,
        padding: 16,
        cursor: onClick ? "pointer" : "default",
        transition: "border-color 0.15s",
      }}
    >
      <div
        style={{
          fontSize: 12,
          color: theme.textDim,
          textTransform: "uppercase",
          letterSpacing: 0.4,
          fontWeight: 600,
        }}
      >
        {label}
      </div>
      <div
        style={{
          marginTop: 8,
          fontSize: 28,
          fontWeight: 700,
          color: valueColor,
          minHeight: 36,
          display: "flex",
          alignItems: "center",
        }}
      >
        {value === null ? <Spin size={20} /> : value}
      </div>
    </div>
  );
}

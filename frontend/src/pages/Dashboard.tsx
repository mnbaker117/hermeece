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
import { theme } from "../theme";

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

export default function Dashboard({ onNav }: DashboardProps) {
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [tentativeCount, setTentativeCount] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [review, tentative, h] = await Promise.all([
          api.get<ReviewListResponse>("/v1/review"),
          api.get<TentativeListResponse>("/v1/tentative"),
          api.get<HealthResponse>("/health"),
        ]);
        if (cancelled) return;
        setReviewCount(review.pending_count);
        setTentativeCount(tentative.items.length);
        setHealth(h);
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

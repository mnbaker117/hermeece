// LogsPage — live log viewer with All / Announces tab filter.
//
// Reads from the in-memory log buffer via /api/v1/logs. Two tabs:
//   - All: full application log (dispatcher, budget, IRC, pipeline)
//   - Announces: only IRC announce events + dispatcher decisions
//
// Auto-refreshes every 5 seconds while the tab is visible. Pauses
// when the user scrolls up (reading older entries) to avoid jumping.
import { useEffect, useRef, useState } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  message: string;
  is_announce: boolean;
}

interface LogsResponse {
  entries: LogEntry[];
  total_buffered: number;
}

type Tab = "all" | "announces";

export default function LogsPage() {
  const theme = useTheme();
  const [tab, setTab] = useState<Tab>("all");
  const [entries, setEntries] = useState<LogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      const filter = tab === "announces" ? "announces" : undefined;
      const params = new URLSearchParams({ lines: "500" });
      if (filter) params.set("filter", filter);
      const r = await api.get<LogsResponse>(`/v1/logs?${params}`);
      setEntries(r.entries);
      setTotal(r.total_buffered);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
    const iv = setInterval(() => {
      if (!document.hidden && autoScroll) load();
    }, 5000);
    return () => clearInterval(iv);
  }, [tab, autoScroll]);

  const levelColor = (level: string) => {
    switch (level) {
      case "ERROR":
        return theme.err;
      case "WARNING":
        return theme.warn;
      case "DEBUG":
        return theme.textDim;
      default:
        return theme.text2;
    }
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <h1 style={{ fontSize: 24, fontWeight: 700, color: theme.text }}>
          Logs
        </h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: theme.textDim }}>
            {total} buffered
          </span>
          <Btn variant="ghost" onClick={load}>
            Refresh
          </Btn>
        </div>
      </div>

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
          display: "flex",
          gap: 4,
          marginBottom: 12,
          borderBottom: `1px solid ${theme.borderL}`,
        }}
      >
        {(["all", "announces"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t);
              setEntries(null);
            }}
            style={{
              background: "transparent",
              border: "none",
              borderBottom: `2px solid ${t === tab ? theme.accent : "transparent"}`,
              color: t === tab ? theme.accent : theme.text2,
              padding: "10px 16px",
              fontSize: 14,
              fontWeight: 600,
              cursor: "pointer",
              marginBottom: -1,
              textTransform: "capitalize",
            }}
          >
            {t === "all" ? "All logs" : "Announces"}
          </button>
        ))}
      </div>

      {entries === null ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : entries.length === 0 ? (
        <p style={{ color: theme.textDim, fontSize: 13 }}>No log entries yet.</p>
      ) : (
        <div
          style={{
            background: theme.bg2,
            border: `1px solid ${theme.borderL}`,
            borderRadius: 8,
            padding: 12,
            maxHeight: "70vh",
            overflowY: "auto",
            fontFamily:
              "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace",
            fontSize: 12,
            lineHeight: 1.6,
          }}
          onScroll={(e) => {
            const el = e.currentTarget;
            const nearBottom =
              el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            setAutoScroll(nearBottom);
          }}
        >
          {entries.map((entry, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 8,
                padding: "2px 0",
                borderBottom:
                  i < entries.length - 1
                    ? `1px solid ${theme.borderL}`
                    : "none",
              }}
            >
              <span style={{ color: theme.textDim, flexShrink: 0, width: 150 }}>
                {entry.ts}
              </span>
              <span
                style={{
                  color: levelColor(entry.level),
                  flexShrink: 0,
                  width: 55,
                  fontWeight: 700,
                }}
              >
                {entry.level}
              </span>
              <span style={{ color: theme.text2, wordBreak: "break-word" }}>
                {entry.message}
              </span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

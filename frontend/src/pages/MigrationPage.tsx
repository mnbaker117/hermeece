// MigrationPage — move existing downloads into monthly [YYYY-MM] folders.
//
// Two-step wizard:
//   1. Preview: scans qBit torrents, reads file mtime to determine
//      target month, shows a table of what would move and what's
//      already in the right place.
//   2. Execute: for each selected torrent, runs the qBit
//      pause → setLocation → recheck → poll → resume cycle.
//
// The preview is non-destructive (just reads). Execute is the only
// step that touches qBit state.
import { useState } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface PreviewItem {
  hash: string;
  name: string;
  current_path: string;
  target_month: string | null;
  target_path: string | null;
  needs_move: boolean;
  file_mtime: string | null;
}

interface PreviewResponse {
  items: PreviewItem[];
  need_move_count: number;
  already_ok_count: number;
}

interface ExecuteResultItem {
  hash: string;
  name: string;
  ok: boolean;
  error: string | null;
  action: string | null;
}

interface ExecuteResponse {
  total: number;
  succeeded: number;
  failed: number;
  dry_run: boolean;
  results: ExecuteResultItem[];
}

export default function MigrationPage() {
  const theme = useTheme();
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<ExecuteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<"idle" | "preview" | "running" | "done">(
    "idle",
  );

  async function runPreview() {
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const r = await api.get<PreviewResponse>("/v1/migration/preview");
      setPreview(r);
      // Auto-select everything that needs a move.
      setSelected(
        new Set(r.items.filter((i) => i.needs_move).map((i) => i.hash)),
      );
      setStep("preview");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runExecute(dryRun: boolean = false) {
    if (selected.size === 0) return;
    setBusy(true);
    setError(null);
    setStep("running");
    try {
      const r = await api.post<ExecuteResponse>("/v1/migration/execute", {
        hashes: [...selected],
        dry_run: dryRun,
      });
      setResults(r);
      setStep("done");
    } catch (e) {
      setError(String(e));
      setStep("preview");
    } finally {
      setBusy(false);
    }
  }

  function toggleHash(hash: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(hash)) next.delete(hash);
      else next.add(hash);
      return next;
    });
  }

  function toggleAll() {
    if (!preview) return;
    const movable = preview.items.filter((i) => i.needs_move);
    if (selected.size === movable.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(movable.map((i) => i.hash)));
    }
  }

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
        Migration wizard
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 20 }}>
        Move existing downloads into monthly <code>[YYYY-MM]</code> folders
        based on file modification dates. qBit torrents are paused, relocated,
        rechecked, and resumed automatically.
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

      {step === "idle" && (
        <Section
          title="Step 1: Preview"
          subtitle="Scan your qBit torrents and compute where each should live."
        >
          <Btn variant="primary" onClick={runPreview} disabled={busy}>
            {busy ? <Spin size={14} /> : "Scan torrents"}
          </Btn>
        </Section>
      )}

      {step === "preview" && preview && (
        <>
          <Section
            title={`Step 1: Preview — ${preview.need_move_count} to move, ${preview.already_ok_count} already OK`}
            right={
              <div style={{ display: "flex", gap: 8 }}>
                <Btn variant="ghost" onClick={toggleAll}>
                  {selected.size ===
                  preview.items.filter((i) => i.needs_move).length
                    ? "Deselect all"
                    : "Select all"}
                </Btn>
                <Btn variant="ghost" onClick={runPreview} disabled={busy}>
                  Re-scan
                </Btn>
              </div>
            }
          >
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12,
              }}
            >
              <thead>
                <tr
                  style={{
                    textAlign: "left",
                    color: theme.textDim,
                    fontWeight: 600,
                    fontSize: 11,
                    textTransform: "uppercase",
                  }}
                >
                  <th style={{ padding: "6px 4px", width: 30 }}></th>
                  <th style={{ padding: "6px 4px" }}>Name</th>
                  <th style={{ padding: "6px 4px" }}>Current</th>
                  <th style={{ padding: "6px 4px" }}>Target</th>
                  <th style={{ padding: "6px 4px" }}>Mtime</th>
                </tr>
              </thead>
              <tbody>
                {preview.items.map((item) => (
                  <tr
                    key={item.hash}
                    style={{
                      borderTop: `1px solid ${theme.borderL}`,
                      opacity: item.needs_move ? 1 : 0.5,
                    }}
                  >
                    <td style={{ padding: "6px 4px" }}>
                      {item.needs_move && (
                        <input
                          type="checkbox"
                          checked={selected.has(item.hash)}
                          onChange={() => toggleHash(item.hash)}
                        />
                      )}
                      {!item.needs_move && "✓"}
                    </td>
                    <td
                      style={{
                        padding: "6px 4px",
                        color: theme.text,
                        maxWidth: 300,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={item.name}
                    >
                      {item.name}
                    </td>
                    <td
                      style={{
                        padding: "6px 4px",
                        color: theme.textDim,
                        fontSize: 11,
                      }}
                    >
                      {item.current_path}
                    </td>
                    <td
                      style={{
                        padding: "6px 4px",
                        color: item.needs_move
                          ? theme.accent
                          : theme.textDim,
                        fontSize: 11,
                      }}
                    >
                      {item.target_month || "—"}
                    </td>
                    <td
                      style={{
                        padding: "6px 4px",
                        color: theme.textDim,
                        fontSize: 11,
                      }}
                    >
                      {item.file_mtime
                        ? item.file_mtime.slice(0, 10)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          <Section
            title="Step 2: Execute"
            subtitle={`${selected.size} torrent(s) selected for migration.`}
          >
            <p style={{ fontSize: 13, color: theme.textDim, marginBottom: 12, lineHeight: 1.5 }}>
              <strong>Dry Run</strong> validates paths and shows what would happen without touching qBit.
              {" "}<strong>Migrate</strong> runs the full pause → relocate → recheck → resume cycle (~5s per torrent).
            </p>
            <div style={{ display: "flex", gap: 10 }}>
              <Btn
                variant="secondary"
                onClick={() => runExecute(true)}
                disabled={busy || selected.size === 0}
              >
                {busy ? <Spin size={14} /> : `🧪 Dry Run (${selected.size})`}
              </Btn>
              <Btn
                variant="primary"
                onClick={() => runExecute(false)}
                disabled={busy || selected.size === 0}
              >
                {busy ? <Spin size={14} /> : `📦 Migrate ${selected.size} torrent(s)`}
              </Btn>
            </div>
          </Section>
        </>
      )}

      {step === "running" && (
        <Section title="Migration in progress…">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: 20,
            }}
          >
            <Spin />
            <span style={{ color: theme.text2 }}>
              Processing {selected.size} torrent(s)… please wait.
            </span>
          </div>
        </Section>
      )}

      {step === "done" && results && (
        <Section
          title={`${results.dry_run ? "🧪 Dry Run" : "Done"} — ${results.succeeded} succeeded, ${results.failed} failed`}
          right={
            <div style={{ display: "flex", gap: 8 }}>
              {results.dry_run && (
                <Btn
                  variant="primary"
                  onClick={() => {
                    setResults(null);
                    setStep("preview");
                  }}
                >
                  Proceed with real migration
                </Btn>
              )}
              <Btn
                variant="ghost"
                onClick={() => {
                  setStep("idle");
                  setPreview(null);
                  setResults(null);
                }}
              >
                Start over
              </Btn>
            </div>
          }
        >
          {results.dry_run && (
            <p style={{ fontSize: 13, color: theme.accent, marginBottom: 12, fontWeight: 500 }}>
              This was a dry run — no files were moved. Review the actions below, then click "Proceed with real migration" if everything looks correct.
            </p>
          )}
          {results.results.map((r) => (
            <div
              key={r.hash}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                padding: "8px 0",
                borderBottom: `1px solid ${theme.borderL}`,
                fontSize: 13,
                gap: 12,
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ color: r.ok ? theme.text2 : theme.err, fontWeight: 500 }}>
                  {r.name}
                </div>
                {r.action && (
                  <div style={{ fontSize: 11, color: theme.textDim, marginTop: 2, fontFamily: "ui-monospace, monospace" }}>
                    {r.action}
                  </div>
                )}
              </div>
              <div style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                <span style={{ color: r.ok ? theme.ok : theme.err, fontWeight: 600, fontSize: 12 }}>
                  {r.ok ? "✓ OK" : "✗ Failed"}
                </span>
                {r.error && (
                  <span style={{ fontSize: 11, color: theme.err }}>{r.error}</span>
                )}
              </div>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

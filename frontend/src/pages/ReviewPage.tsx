// ReviewPage — list pending book_review_queue items + approve/reject.
//
// The list view is the daily-driver UX: as books finish downloading
// and the enricher pulls covers + descriptions, they show up here.
// Each card has the cover, the merged metadata, and two buttons.
//
// Approve hits POST /api/v1/review/{id}/approve, which:
//   1. moves the patched epub into the configured sink (CWA/Calibre)
//   2. records a calibre_additions counter row
//   3. cleans up the staging dir
// Reject hits POST /api/v1/review/{id}/reject, which:
//   1. deletes the staging dir (the seeding original is untouched)
//   2. marks the row rejected with the user's note
//
// Polling cadence: 30s. Approval/rejection refreshes immediately so
// the list shrinks on user action without waiting for the next tick.
import { useEffect, useState } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface ReviewItem {
  id: number;
  grab_id: number;
  staged_path: string;
  book_filename: string;
  book_format: string | null;
  metadata: Record<string, unknown> & {
    title?: string;
    author?: string;
    series?: string;
    series_index?: number;
    description?: string;
    isbn?: string;
    publisher?: string;
    pub_date?: string;
    page_count?: number;
    enriched?: {
      title?: string;
      authors?: string[];
      description?: string;
      series?: string;
      series_index?: number;
      isbn?: string;
      publisher?: string;
      pub_date?: string;
      page_count?: number;
      cover_url?: string;
      source?: string;
      source_url?: string;
      confidence?: number;
      source_log?: { source: string; confidence: number | null; status: string }[];
    };
  };
  cover_path: string | null;
  status: string;
  created_at: string;
}

interface ReviewListResponse {
  items: ReviewItem[];
  pending_count: number;
}

export default function ReviewPage() {
  const theme = useTheme();
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function refresh() {
    try {
      const r = await api.get<ReviewListResponse>("/v1/review");
      setItems(r.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 30_000);
    return () => clearInterval(iv);
  }, []);

  async function approve(id: number, metadata?: Record<string, unknown>) {
    setBusyId(id);
    try {
      await api.post(`/v1/review/${id}/approve`, {
        metadata: metadata || null,
      });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function reject(id: number) {
    setBusyId(id);
    try {
      await api.post(`/v1/review/${id}/reject`, { note: "rejected via UI" });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
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
        Review queue
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 24 }}>
        Books waiting on your approval before delivery to Calibre.
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

      {items === null ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <Section title="Nothing pending" subtitle="The queue is empty.">
          <p style={{ fontSize: 13, color: theme.textDim }}>
            New downloads land here automatically once they finish and the
            metadata enricher returns.
          </p>
        </Section>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {items.map((item) => (
            <ReviewCard
              key={item.id}
              item={item}
              busy={busyId === item.id}
              onApprove={(meta) => approve(item.id, meta)}
              onReject={() => reject(item.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ReviewCard({
  item,
  busy,
  onApprove,
  onReject,
}: {
  item: ReviewItem;
  busy: boolean;
  onApprove: (metadata?: Record<string, unknown>) => void;
  onReject: () => void;
}) {
  const theme = useTheme();
  const m = item.metadata;
  const e = m.enriched;
  const [editing, setEditing] = useState(false);

  // Resolved display values (enriched > embedded > fallback).
  const resolvedTitle = e?.title || m.title || item.book_filename;
  const resolvedAuthors =
    (e?.authors && e.authors.length > 0 ? e.authors.join(", ") : m.author) ||
    "Unknown author";
  const resolvedSeries = e?.series || m.series || "";
  const resolvedSeriesIndex = e?.series_index ?? m.series_index;
  const resolvedDescription = e?.description || m.description || "";
  const resolvedIsbn = e?.isbn || m.isbn || "";
  const resolvedPublisher = e?.publisher || m.publisher || "";
  const resolvedPubDate = e?.pub_date || m.pub_date || "";
  const resolvedPageCount = e?.page_count || m.page_count;

  // Edit state — initialized from resolved values when edit mode opens.
  const [editTitle, setEditTitle] = useState(resolvedTitle);
  const [editAuthors, setEditAuthors] = useState(resolvedAuthors);
  const [editSeries, setEditSeries] = useState(resolvedSeries);
  const [editSeriesIndex, setEditSeriesIndex] = useState(String(resolvedSeriesIndex ?? ""));
  const [editIsbn, setEditIsbn] = useState(resolvedIsbn);
  const [editPublisher, setEditPublisher] = useState(resolvedPublisher);

  function startEdit() {
    setEditTitle(resolvedTitle);
    setEditAuthors(resolvedAuthors);
    setEditSeries(resolvedSeries);
    setEditSeriesIndex(String(resolvedSeriesIndex ?? ""));
    setEditIsbn(resolvedIsbn);
    setEditPublisher(resolvedPublisher);
    setEditing(true);
  }

  function approveWithEdits() {
    if (!editing) {
      onApprove();
      return;
    }
    const edits: Record<string, unknown> = {
      title: editTitle,
      author: editAuthors,
      series: editSeries || null,
      series_index: editSeriesIndex ? parseFloat(editSeriesIndex) : null,
      isbn: editIsbn || null,
      publisher: editPublisher || null,
    };
    onApprove(edits);
  }

  // Display values for non-edit mode.
  const title = resolvedTitle;
  const authors = resolvedAuthors;
  const series = resolvedSeries;
  const seriesIndex = resolvedSeriesIndex;
  const description = resolvedDescription;
  const isbn = resolvedIsbn;
  const publisher = resolvedPublisher;
  const pubDate = resolvedPubDate;
  const pageCount = resolvedPageCount;
  const sourceLog = (e?.source_log as { source: string; confidence: number | null; status: string }[] | undefined) ?? [];
  const sourceLabel = e?.source ? `via ${e.source}` : null;
  const confidence = e?.confidence;

  return (
    <article
      style={{
        background: theme.bg2,
        border: `1px solid ${theme.borderL}`,
        borderRadius: 12,
        padding: 16,
        display: "grid",
        gridTemplateColumns: "120px 1fr auto",
        gap: 16,
        animation: "slide-up 0.2s ease-out",
      }}
    >
      <CoverThumb item={item} />

      <div style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          {editing ? (
            <EditInput value={editTitle} onChange={setEditTitle} placeholder="Title" style={{ fontSize: 17, fontWeight: 700 }} />
          ) : (
            <h3 style={{ fontSize: 17, fontWeight: 700, color: theme.text, wordBreak: "break-word" }}>
              {title}
            </h3>
          )}
          {sourceLog.length > 0 ? (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {sourceLog.map((sl) => (
                <span key={sl.source} style={{
                  fontSize: 10, padding: "2px 7px", borderRadius: 99,
                  background: sl.status === "matched" ? theme.bg3 : theme.bg4,
                  color: sl.status === "matched"
                    ? (sl.confidence !== null && sl.confidence >= 0.8 ? theme.ok : theme.text2)
                    : theme.textDim,
                  fontWeight: 500,
                }}>
                  {sl.source}
                  {sl.confidence !== null ? ` ${(sl.confidence * 100).toFixed(0)}%` : " —"}
                </span>
              ))}
            </div>
          ) : sourceLabel ? (
            <span style={{ fontSize: 11, color: theme.textDim, background: theme.bg3, padding: "2px 8px", borderRadius: 99 }}>
              {sourceLabel}
              {confidence !== undefined && ` · ${(confidence * 100).toFixed(0)}%`}
            </span>
          ) : null}
        </div>
        {editing ? (
          <EditInput value={editAuthors} onChange={setEditAuthors} placeholder="Author(s)" style={{ fontSize: 14 }} />
        ) : (
          <div style={{ fontSize: 14, color: theme.text2, marginTop: 2 }}>{authors}</div>
        )}
        {editing ? (
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <EditInput value={editSeries} onChange={setEditSeries} placeholder="Series" style={{ flex: 1, fontSize: 13 }} />
            <EditInput value={editSeriesIndex} onChange={setEditSeriesIndex} placeholder="#" style={{ width: 50, fontSize: 13 }} />
          </div>
        ) : series ? (
          <div style={{ fontSize: 13, color: theme.textDim, marginTop: 4 }}>
            {series}{seriesIndex !== undefined && seriesIndex !== null && ` #${seriesIndex}`}
          </div>
        ) : null}

        <dl
          style={{
            marginTop: 10,
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: "4px 12px",
            fontSize: 12,
          }}
        >
          {pubDate && <Field label="Published">{pubDate}</Field>}
          {editing ? (
            <Field label="Publisher"><EditInput value={editPublisher} onChange={setEditPublisher} placeholder="Publisher" style={{ fontSize: 12, width: "100%" }} /></Field>
          ) : publisher ? (
            <Field label="Publisher">{publisher}</Field>
          ) : null}
          {pageCount && <Field label="Pages">{pageCount}</Field>}
          {editing ? (
            <Field label="ISBN"><EditInput value={editIsbn} onChange={setEditIsbn} placeholder="ISBN" style={{ fontSize: 12, width: "100%" }} /></Field>
          ) : isbn ? (
            <Field label="ISBN">{isbn}</Field>
          ) : null}
          <Field label="File">{item.book_filename}</Field>
          <Field label="Grab">#{item.grab_id}</Field>
        </dl>

        {description && (
          <p
            style={{
              marginTop: 10,
              fontSize: 13,
              color: theme.text2,
              lineHeight: 1.5,
              maxHeight: 130,
              overflow: "hidden",
              display: "-webkit-box",
              WebkitLineClamp: 6,
              WebkitBoxOrient: "vertical",
            }}
          >
            {description}
          </p>
        )}
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          alignItems: "stretch",
          minWidth: 110,
        }}
      >
        <Btn
          variant="primary"
          disabled={busy}
          onClick={approveWithEdits}
        >
          {busy ? <Spin size={14} /> : editing ? "Save & Approve" : "Approve"}
        </Btn>
        <Btn
          variant={editing ? "ghost" : "secondary"}
          disabled={busy}
          onClick={() => editing ? setEditing(false) : startEdit()}
        >
          {editing ? "Cancel edit" : "Edit"}
        </Btn>
        <Btn variant="danger" disabled={busy} onClick={onReject}>
          Reject
        </Btn>
      </div>
    </article>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  const theme = useTheme();
  return (
    <>
      <dt
        style={{
          color: theme.textDim,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: 0.3,
        }}
      >
        {label}
      </dt>
      <dd style={{ color: theme.text2, wordBreak: "break-word" }}>{children}</dd>
    </>
  );
}

function EditInput({
  value,
  onChange,
  placeholder,
  style,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  style?: React.CSSProperties;
}) {
  const theme = useTheme();
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        padding: "4px 8px",
        borderRadius: 6,
        border: `1px solid ${theme.accent}55`,
        background: theme.bg3,
        color: theme.text,
        outline: "none",
        width: "100%",
        ...style,
      }}
    />
  );
}


function CoverThumb({ item }: { item: ReviewItem }) {
  const theme = useTheme();
  const mamCover = item.metadata.cover_mam as string | null;
  const enrichedCover = item.metadata.cover_enriched as string | null;
  const covers = [mamCover, enrichedCover, item.cover_path].filter(Boolean) as string[];
  // Deduplicate.
  const uniqueCovers = [...new Set(covers)];
  const [activeIdx, setActiveIdx] = useState(0);
  const activeCover = uniqueCovers[activeIdx] || null;

  if (activeCover) {
    const coverUrl = `/api/v1/covers/${encodeURIComponent(activeCover)}`;
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 4, width: 120 }}>
        <img
          src={coverUrl}
          alt="Cover"
          style={{
            width: 120,
            height: 180,
            objectFit: "cover",
            borderRadius: 6,
            border: `1px solid ${theme.borderL}`,
            background: theme.bg3,
          }}
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
        {uniqueCovers.length > 1 && (
          <div style={{ display: "flex", justifyContent: "center", gap: 4 }}>
            {uniqueCovers.map((_, i) => (
              <button
                key={i}
                onClick={() => setActiveIdx(i)}
                style={{
                  width: 14, height: 14, borderRadius: "50%",
                  border: i === activeIdx ? `2px solid ${theme.accent}` : `2px solid ${theme.bg4}`,
                  background: i === activeIdx ? theme.accent : "transparent",
                  cursor: "pointer", padding: 0,
                }}
              />
            ))}
            <span style={{ fontSize: 9, color: theme.textDim, marginLeft: 2 }}>
              {activeIdx === 0 ? "MAM" : "Enriched"}
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        width: 120,
        height: 180,
        background: theme.bg3,
        border: `1px solid ${theme.borderL}`,
        borderRadius: 6,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: theme.textDim,
        fontSize: 36,
        fontWeight: 700,
      }}
    >
      {(item.metadata.title || item.book_filename).slice(0, 1).toUpperCase()}
    </div>
  );
}

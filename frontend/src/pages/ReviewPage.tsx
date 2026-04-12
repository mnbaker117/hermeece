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
import { theme } from "../theme";

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

  async function approve(id: number) {
    setBusyId(id);
    try {
      await api.post(`/v1/review/${id}/approve`, {});
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
              onApprove={() => approve(item.id)}
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
  onApprove: () => void;
  onReject: () => void;
}) {
  const m = item.metadata;
  const e = m.enriched;
  // Prefer enriched fields when available; fall back to embedded values.
  const title = e?.title || m.title || item.book_filename;
  const authors =
    (e?.authors && e.authors.length > 0 ? e.authors.join(", ") : m.author) ||
    "Unknown author";
  const series = e?.series || m.series;
  const seriesIndex = e?.series_index ?? m.series_index;
  const description = e?.description || m.description;
  const isbn = e?.isbn || m.isbn;
  const publisher = e?.publisher || m.publisher;
  const pubDate = e?.pub_date || m.pub_date;
  const pageCount = e?.page_count || m.page_count;
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
          <h3
            style={{
              fontSize: 17,
              fontWeight: 700,
              color: theme.text,
              wordBreak: "break-word",
            }}
          >
            {title}
          </h3>
          {sourceLabel && (
            <span
              style={{
                fontSize: 11,
                color: theme.textDim,
                background: theme.bg3,
                padding: "2px 8px",
                borderRadius: 99,
              }}
            >
              {sourceLabel}
              {confidence !== undefined &&
                ` · ${(confidence * 100).toFixed(0)}%`}
            </span>
          )}
        </div>
        <div style={{ fontSize: 14, color: theme.text2, marginTop: 2 }}>
          {authors}
        </div>
        {series && (
          <div style={{ fontSize: 13, color: theme.textDim, marginTop: 4 }}>
            {series}
            {seriesIndex !== undefined && seriesIndex !== null && ` #${seriesIndex}`}
          </div>
        )}

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
          {publisher && <Field label="Publisher">{publisher}</Field>}
          {pageCount && <Field label="Pages">{pageCount}</Field>}
          {isbn && <Field label="ISBN">{isbn}</Field>}
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
          onClick={onApprove}
        >
          {busy ? <Spin size={14} /> : "Approve"}
        </Btn>
        <Btn variant="danger" disabled={busy} onClick={onReject}>
          Reject
        </Btn>
      </div>
    </article>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
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

function CoverThumb({ item }: { item: ReviewItem }) {
  // The cover lives on disk under review_staging_path. We don't have a
  // dedicated /api/v1/review/{id}/cover endpoint yet — that's a Phase 5b
  // task. For now we render a placeholder block when there's no cover
  // and the title initial when there is, so the layout doesn't shift
  // when covers eventually arrive.
  const hasCover = !!item.cover_path;
  return (
    <div
      style={{
        width: 120,
        height: 180,
        background: hasCover ? theme.bg3 : theme.bg3,
        border: `1px solid ${theme.borderL}`,
        borderRadius: 6,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: theme.textDim,
        fontSize: 36,
        fontWeight: 700,
      }}
      title={hasCover ? `Cover at ${item.cover_path}` : "No cover yet"}
    >
      {(item.metadata.title || item.book_filename).slice(0, 1).toUpperCase()}
    </div>
  );
}

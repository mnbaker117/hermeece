// DatabasePage — read-only SQLite browser.
//
// Power-user escape hatch for inspecting Hermeece's database without
// SSH-ing into the container. List of tables on the left with row
// counts; paginated grid of the selected table on the right with a
// search box that matches against every TEXT column.
//
// v1.1 scope is read-only. Inline cell editing / insert / delete is
// deferred to v1.2 — the backend router has matching scope.
import { useEffect, useState } from "react";
import { Btn } from "../components/Btn";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface TableEntry {
  name: string;
  row_count: number;
}

interface TablesResponse {
  tables: TableEntry[];
}

interface RowsResponse {
  table: string;
  total: number;
  page: number;
  per_page: number;
  rows: Record<string, unknown>[];
}

const PER_PAGE = 50;

export default function DatabasePage() {
  const t = useTheme();
  const [tables, setTables] = useState<TableEntry[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get<TablesResponse>("/v1/db/tables")
      .then((r) => {
        setTables(r.tables);
        // Auto-select the first non-empty table for a meaningful
        // landing view instead of an empty right pane.
        const firstNonEmpty = r.tables.find((x) => x.row_count > 0);
        setSelected(firstNonEmpty?.name || (r.tables[0]?.name ?? ""));
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    const params = new URLSearchParams({
      page: String(page),
      per_page: String(PER_PAGE),
    });
    if (search.trim()) params.set("search", search.trim());
    api.get<RowsResponse>(`/v1/db/table/${selected}?${params}`)
      .then((r) => {
        setRows(r.rows);
        setTotal(r.total);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [selected, page, search]);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));
  const columns = rows && rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: t.text }}>Database</h1>
        <span style={{ fontSize: 12, color: t.textDim }}>
          Read-only browser · v1.2 will add inline editing
        </span>
      </div>

      {error && (
        <div style={{ background: t.err + "22", border: `1px solid ${t.err}55`, color: t.err, padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>
          {error}
        </div>
      )}

      {!tables ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spin /></div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 16 }}>
          {/* Table list */}
          <div style={{ background: t.bg2, border: `1px solid ${t.borderL}`, borderRadius: 8, padding: 8, alignSelf: "start", maxHeight: "75vh", overflowY: "auto" }}>
            {tables.map((tbl) => (
              <button
                key={tbl.name}
                onClick={() => { setSelected(tbl.name); setPage(1); setSearch(""); }}
                style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  width: "100%", padding: "8px 10px", margin: "1px 0",
                  background: selected === tbl.name ? t.bg4 : "transparent",
                  color: selected === tbl.name ? t.accent : t.text2,
                  border: "none", borderRadius: 6,
                  fontSize: 13, fontFamily: "ui-monospace, Consolas, monospace",
                  cursor: "pointer", textAlign: "left",
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{tbl.name}</span>
                <span style={{ fontSize: 11, color: t.textDim, flexShrink: 0 }}>
                  {tbl.row_count.toLocaleString()}
                </span>
              </button>
            ))}
          </div>

          {/* Right pane: rows */}
          <div>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
              <input
                type="search"
                placeholder="Search text columns…"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                style={{ padding: "7px 10px", fontSize: 12, background: t.bg2, border: `1px solid ${t.borderL}`, borderRadius: 6, color: t.text2, minWidth: 240, fontFamily: "inherit" }}
              />
              <span style={{ fontSize: 12, color: t.textDim }}>
                {total.toLocaleString()} row{total === 1 ? "" : "s"}
                {search && ` matching “${search}”`}
              </span>
              <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
                <Btn variant="ghost" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading}>←</Btn>
                <span style={{ fontSize: 12, color: t.textDim, padding: "0 8px" }}>
                  {page} / {totalPages}
                </span>
                <Btn variant="ghost" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages || loading}>→</Btn>
              </div>
            </div>

            {loading && !rows ? (
              <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spin /></div>
            ) : rows && rows.length === 0 ? (
              <p style={{ color: t.textDim, fontSize: 13 }}>
                {search ? `No rows match “${search}”.` : "No rows in this table."}
              </p>
            ) : rows ? (
              <div style={{ background: t.bg2, border: `1px solid ${t.borderL}`, borderRadius: 8, overflow: "auto", maxHeight: "70vh" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: "ui-monospace, Consolas, monospace" }}>
                  <thead style={{ position: "sticky", top: 0, background: t.bg3, zIndex: 1 }}>
                    <tr>
                      {columns.map((c) => (
                        <th key={c} style={{ padding: "8px 10px", textAlign: "left", fontWeight: 600, color: t.textDim, borderBottom: `1px solid ${t.borderL}`, whiteSpace: "nowrap" }}>
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i} style={{ borderBottom: i < rows.length - 1 ? `1px solid ${t.borderL}` : "none" }}>
                        {columns.map((c) => {
                          const v = row[c];
                          const display = v === null || v === undefined
                            ? <span style={{ color: t.textDim, fontStyle: "italic" }}>NULL</span>
                            : typeof v === "object"
                              ? JSON.stringify(v)
                              : String(v);
                          return (
                            <td key={c} style={{ padding: "6px 10px", color: t.text2, verticalAlign: "top", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={typeof display === "string" ? display : undefined}>
                              {display}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

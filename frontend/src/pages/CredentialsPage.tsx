// CredentialsPage — manage encrypted credentials stored in the auth DB.
//
// Never shows the actual secret values. Each credential is either
// "configured" (green badge) or "not set" (dim badge). The user can
// enter a new value or clear an existing one.
//
// Values are sent to POST /api/v1/credentials/{key} and stored
// Fernet-encrypted in hermeece_auth.db. The dispatcher rebuilds
// immediately after each save.
import { useEffect, useState } from "react";
import { Btn } from "../components/Btn";
import { Section } from "../components/Section";
import { Spin } from "../components/Spin";
import { api } from "../api";
import { useTheme } from "../theme";

interface CredentialStatus {
  key: string;
  label: string;
  configured: boolean;
}

export default function CredentialsPage() {
  const theme = useTheme();
  const [items, setItems] = useState<CredentialStatus[] | null>(null);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await api.get<{ items: CredentialStatus[] }>("/v1/credentials");
      setItems(r.items);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => { load(); }, []);

  async function save(key: string) {
    if (!editValue.trim()) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await api.post(`/v1/credentials/${key}`, { value: editValue.trim() });
      setOk(`${key} saved successfully.`);
      setEditKey(null);
      setEditValue("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(key: string) {
    setBusy(true);
    setError(null);
    try {
      await api.del(`/v1/credentials/${key}`);
      setOk(`${key} removed.`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: theme.text, marginBottom: 4 }}>
        Credentials
      </h1>
      <p style={{ fontSize: 14, color: theme.textDim, marginBottom: 20 }}>
        Secrets are stored Fernet-encrypted in a separate auth database.
        Values are never shown after saving — only the configured/not-set
        status is visible.
      </p>

      {error && (
        <div style={{ background: theme.err + "22", border: `1px solid ${theme.err}55`, color: theme.err, padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>
          {error}
        </div>
      )}
      {ok && (
        <div style={{ background: theme.ok + "22", border: `1px solid ${theme.ok}55`, color: theme.ok, padding: "10px 14px", borderRadius: 8, fontSize: 13, marginBottom: 16 }}>
          {ok}
        </div>
      )}

      {items === null ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 40 }}><Spin /></div>
      ) : (
        <Section title="All credentials">
          {items.map((item) => (
            <div
              key={item.key}
              style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                gap: 16,
                padding: "12px 0",
                borderBottom: `1px solid ${theme.borderL}`,
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: theme.text }}>
                  {item.label}
                </div>
                <div style={{ fontSize: 12, color: theme.textDim, marginTop: 2 }}>
                  {item.key}
                </div>

                {editKey === item.key && (
                  <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                    <input
                      type="password"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      placeholder={`Enter ${item.label}…`}
                      autoFocus
                      style={{
                        flex: 1,
                        padding: "8px 10px",
                        borderRadius: 8,
                        border: `1px solid ${theme.accent}55`,
                        background: theme.bg3,
                        color: theme.text,
                        fontSize: 13,
                        outline: "none",
                      }}
                    />
                    <Btn
                      variant="primary"
                      disabled={busy || !editValue.trim()}
                      onClick={() => save(item.key)}
                    >
                      {busy ? <Spin size={14} /> : "Save"}
                    </Btn>
                    <Btn
                      variant="ghost"
                      onClick={() => { setEditKey(null); setEditValue(""); }}
                    >
                      Cancel
                    </Btn>
                  </div>
                )}
              </div>

              <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
                <span
                  style={{
                    fontSize: 11,
                    padding: "3px 10px",
                    borderRadius: 99,
                    background: item.configured ? theme.ok + "22" : theme.textDim + "22",
                    color: item.configured ? theme.ok : theme.textDim,
                    fontWeight: 700,
                  }}
                >
                  {item.configured ? "SET" : "NOT SET"}
                </span>

                {editKey !== item.key && (
                  <Btn
                    variant="secondary"
                    onClick={() => {
                      setEditKey(item.key);
                      setEditValue("");
                      setOk(null);
                    }}
                  >
                    {item.configured ? "Update" : "Set"}
                  </Btn>
                )}
                {item.configured && editKey !== item.key && (
                  <Btn variant="danger" disabled={busy} onClick={() => remove(item.key)}>
                    Clear
                  </Btn>
                )}
              </div>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

// Hermeece app shell.
//
// Auth gating:
//   loading                       → spinner
//   !authenticated && first_run   → LoginPage in setup mode
//   !authenticated && !first_run  → LoginPage in sign-in mode
//   authenticated                 → main shell with nav + page slot
//
// Routing is handled with a `page` state variable rather than
// react-router. The page count is small and a hash router would only
// add weight; this matches the AthenaScout pattern. We persist the
// last page in localStorage so reloads land on the same screen.
//
// The `hermeece:auth-required` window event is dispatched by api.ts
// on any 401 response. We catch it here and drop back to login,
// covering the "session expired while you were on the page" case
// without each call site having to handle 401s itself.
import { useEffect, useState } from "react";
import { api } from "./api";
import { ThemeProvider, useTheme, useThemeControls } from "./theme";
import { Spin } from "./components/Spin";
import LoginPage from "./pages/LoginPage";
import AuthorsPage from "./pages/AuthorsPage";
import Dashboard from "./pages/Dashboard";
import DelayedPage from "./pages/DelayedPage";
import FiltersPage from "./pages/FiltersPage";
import MamPage from "./pages/MamPage";
import MigrationPage from "./pages/MigrationPage";
import ReviewPage from "./pages/ReviewPage";
import SettingsPage from "./pages/SettingsPage";
import TentativePage from "./pages/TentativePage";

interface AuthState {
  loading: boolean;
  authenticated: boolean;
  firstRun: boolean;
  username?: string;
}

interface CheckResponse {
  authenticated: boolean;
  first_run: boolean;
  username?: string;
}

const NAV: { id: string; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "review", label: "Review queue" },
  { id: "tentative", label: "Tentative" },
  { id: "authors", label: "Authors" },
  { id: "filters", label: "Filters" },
  { id: "delayed", label: "Delayed" },
  { id: "migration", label: "Migration" },
  { id: "mam", label: "MAM" },
  { id: "settings", label: "Settings" },
];

function loadSavedPage(): string {
  try {
    return localStorage.getItem("hermeece_page") || "dashboard";
  } catch {
    return "dashboard";
  }
}

export default function App() {
  return (
    <ThemeProvider>
      <AppInner />
    </ThemeProvider>
  );
}

function AppInner() {
  const theme = useTheme();
  const [auth, setAuth] = useState<AuthState>({
    loading: true,
    authenticated: false,
    firstRun: false,
  });
  const [page, setPage] = useState<string>(loadSavedPage);

  async function checkAuth() {
    try {
      const r = await api.get<CheckResponse>("/auth/check");
      setAuth({
        loading: false,
        authenticated: !!r.authenticated,
        firstRun: !!r.first_run,
        username: r.username,
      });
    } catch {
      setAuth({
        loading: false,
        authenticated: false,
        firstRun: false,
      });
    }
  }

  useEffect(() => {
    checkAuth();
    const onAuthRequired = () => {
      setAuth((s) =>
        s.authenticated
          ? { loading: false, authenticated: false, firstRun: false }
          : s,
      );
    };
    window.addEventListener("hermeece:auth-required", onAuthRequired);
    return () =>
      window.removeEventListener("hermeece:auth-required", onAuthRequired);
  }, []);

  function nav(p: string) {
    setPage(p);
    try {
      localStorage.setItem("hermeece_page", p);
    } catch {
      /* ignore */
    }
    window.scrollTo(0, 0);
  }

  async function logout() {
    try {
      await api.post("/auth/logout");
    } catch {
      /* ignore */
    }
    setAuth({
      loading: false,
      authenticated: false,
      firstRun: false,
    });
  }

  if (auth.loading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: theme.bg,
        }}
      >
        <Spin />
      </div>
    );
  }

  if (!auth.authenticated) {
    return (
      <LoginPage
        firstRun={auth.firstRun}
        onLoginSuccess={() => checkAuth()}
      />
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: theme.bg }}>
      <nav
        style={{
          position: "sticky",
          top: 0,
          zIndex: 50,
          background: theme.bg + "ee",
          backdropFilter: "blur(12px)",
          borderBottom: `1px solid ${theme.borderL}`,
        }}
      >
        <div
          style={{
            maxWidth: 1120,
            margin: "0 auto",
            padding: "0 20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 56,
            gap: 12,
          }}
        >
          <button
            onClick={() => nav("dashboard")}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 18,
              fontWeight: 700,
              color: theme.accent,
              padding: 0,
            }}
          >
            Hermeece
          </button>
          <div style={{ display: "flex", gap: 4, flex: 1, marginLeft: 16 }}>
            {NAV.map((n) => (
              <button
                key={n.id}
                onClick={() => nav(n.id)}
                style={{
                  padding: "8px 14px",
                  borderRadius: 8,
                  fontSize: 14,
                  fontWeight: 500,
                  border: "none",
                  cursor: "pointer",
                  background: page === n.id ? theme.bg4 : "transparent",
                  color: page === n.id ? theme.accent : theme.text2,
                }}
              >
                {n.label}
              </button>
            ))}
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 13,
              color: theme.textDim,
            }}
          >
            <ThemeToggleButton />
            <span>{auth.username}</span>
            <button
              onClick={logout}
              style={{
                background: "transparent",
                border: `1px solid ${theme.border}`,
                color: theme.text2,
                padding: "6px 12px",
                borderRadius: 8,
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </nav>

      <main
        style={{
          maxWidth: 1120,
          margin: "0 auto",
          padding: "28px 20px",
        }}
      >
        <div key={page} style={{ animation: "fade-in 0.2s ease-out" }}>
          {page === "dashboard" && <Dashboard onNav={nav} />}
          {page === "review" && <ReviewPage />}
          {page === "tentative" && <TentativePage />}
          {page === "authors" && <AuthorsPage />}
          {page === "filters" && <FiltersPage />}
          {page === "delayed" && <DelayedPage />}
          {page === "migration" && <MigrationPage />}
          {page === "mam" && <MamPage />}
          {page === "settings" && <SettingsPage />}
        </div>
      </main>
    </div>
  );
}

// Small sun/moon/cloud glyph button that cycles through dark → dim → light.
// The icon matches the CURRENT theme so the user knows which one they're in.
function ThemeToggleButton() {
  const theme = useTheme();
  const { themeName, cycle } = useThemeControls();
  const icon =
    themeName === "dark" ? "🌙" : themeName === "dim" ? "⛅" : "☀️";
  const next =
    themeName === "dark" ? "Dim" : themeName === "dim" ? "Light" : "Dark";
  return (
    <button
      onClick={cycle}
      title={`Theme: ${theme.name} — click for ${next}`}
      aria-label="Cycle theme"
      style={{
        background: "transparent",
        border: `1px solid ${theme.border}`,
        color: theme.text2,
        width: 32,
        height: 32,
        borderRadius: 8,
        fontSize: 14,
        lineHeight: 1,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
      }}
    >
      {icon}
    </button>
  );
}

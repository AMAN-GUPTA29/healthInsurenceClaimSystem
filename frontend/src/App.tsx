/**
 * App Shell — root layout with navigation and routing.
 */

import { BrowserRouter, Navigate, Route, Routes, NavLink } from 'react-router-dom'
import { Dashboard } from './pages/Dashboard'
import { ClaimSubmission } from './pages/ClaimSubmission'
import { ClaimDetail } from './pages/ClaimDetail'
import { ClaimHistory } from './pages/ClaimHistory'
import { Reports } from './pages/Reports'

// ── Global CSS Variables & Reset ──────────────────────────────────────────────
const globalStyles = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --color-bg: #0a0f1e;
    --color-surface: #0f172a;
    --color-border: rgba(51, 65, 85, 0.6);
    --color-primary: #6366f1;
    --color-primary-light: #a5b4fc;
    --color-text: #e2e8f0;
    --color-text-muted: #64748b;
    --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  }

  html, body, #root {
    height: 100%;
    background-color: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
  }

  a { color: inherit; text-decoration: none; }
  code {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 0.875em;
    background: rgba(99,102,241,0.15);
    padding: 2px 6px;
    border-radius: 4px;
    color: #a5b4fc;
  }

  /* Subtle grid background */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(99, 102, 241, 0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(99, 102, 241, 0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }
`

// ── Sidebar Nav ───────────────────────────────────────────────────────────────
function Sidebar() {
  const navStyle = {
    display: 'flex' as const,
    flexDirection: 'column' as const,
    gap: '4px',
  }

  const linkStyle = (isActive: boolean) => ({
    display: 'flex' as const,
    alignItems: 'center' as const,
    gap: '10px',
    padding: '10px 14px',
    borderRadius: '10px',
    fontSize: '14px',
    fontWeight: 500 as const,
    color: isActive ? '#a5b4fc' : '#64748b',
    background: isActive ? 'rgba(99, 102, 241, 0.12)' : 'transparent',
    border: `1px solid ${isActive ? 'rgba(99,102,241,0.25)' : 'transparent'}`,
    transition: 'all 0.2s',
    cursor: 'pointer' as const,
  })

  return (
    <aside style={{
      width: '220px',
      flexShrink: 0,
      borderRight: '1px solid rgba(51, 65, 85, 0.5)',
      padding: '24px 16px',
      display: 'flex',
      flexDirection: 'column' as const,
      gap: '32px',
      position: 'relative' as const,
      zIndex: 1,
    }}>
      {/* Logo */}
      <div style={{ padding: '0 4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
          <div style={{
            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            width: '32px', height: '32px',
            borderRadius: '8px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '16px',
            boxShadow: '0 4px 12px rgba(99,102,241,0.4)',
          }}>
            🏥
          </div>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: '#e2e8f0', lineHeight: '1.2' }}>Claims</div>
            <div style={{ fontSize: '11px', color: '#64748b', lineHeight: '1.2' }}>AI Pipeline</div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav style={navStyle}>
        <div style={{ fontSize: '11px', color: '#475569', fontWeight: 600, letterSpacing: '0.08em', padding: '0 4px', marginBottom: '4px', textTransform: 'uppercase' as const }}>
          Navigation
        </div>
        <NavLink to="/" end>
          {({ isActive }) => (
            <div style={linkStyle(isActive)}>
              <span>🏠</span> Dashboard
            </div>
          )}
        </NavLink>
        <NavLink to="/claims/new">
          {({ isActive }) => (
            <div style={linkStyle(isActive)}>
              <span>📝</span> Submit Claim
            </div>
          )}
        </NavLink>
        <NavLink to="/claims" end>
          {({ isActive }) => (
            <div style={linkStyle(isActive)}>
              <span>📋</span> Claim History
            </div>
          )}
        </NavLink>
        <NavLink to="/reports">
          {({ isActive }) => (
            <div style={linkStyle(isActive)}>
              <span>📊</span> Evaluation Report
            </div>
          )}
        </NavLink>
      </nav>

      {/* System status */}
      <div style={{
        marginTop: 'auto',
        background: 'rgba(34,197,94,0.08)',
        border: '1px solid rgba(34,197,94,0.2)',
        borderRadius: '10px',
        padding: '12px',
        fontSize: '12px',
        color: '#64748b',
      }}>
        <div style={{ color: '#86efac', fontWeight: 600, marginBottom: '4px' }}>✅ Full Pipeline Active</div>
        Claim Validation → Explanation, all 10 stages live
      </div>
    </aside>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <>
      <style>{globalStyles}</style>
      <BrowserRouter>
        <div style={{
          display: 'flex',
          height: '100vh',
          overflow: 'hidden',
          position: 'relative' as const,
          zIndex: 1,
        }}>
          <Sidebar />
          <main style={{
            flex: 1,
            overflowY: 'auto' as const,
            padding: '32px 40px',
          }}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/claims" element={<ClaimHistory />} />
              <Route path="/claims/new" element={<ClaimSubmission />} />
              <Route path="/claims/:claimId" element={<ClaimDetail />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </>
  )
}

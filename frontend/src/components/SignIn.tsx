import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ApiError, UNAUTHORIZED_EVENT, auth } from '../api/client';
import { Button } from './ui';

type Gate = 'checking' | 'open' | 'signin';

/**
 * Shows the dashboard only to a signed-in operator when the server requires an access token.
 * The session is an HttpOnly cookie set by the server, so the token never lives in page storage and
 * camera pictures, evidence video and the push channel are authorised without extra code.
 * If the server cannot be asked (offline, older backend), the dashboard opens and reports its own errors.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>('checking');

  const check = useCallback(() => {
    auth.status()
      .then((s) => setGate(s.auth_required && !s.authenticated ? 'signin' : 'open'))
      .catch(() => setGate('open'));
  }, []);

  useEffect(() => {
    check();
    const onExpired = () => setGate('signin');      // any API call answered 401: session expired or revoked
    window.addEventListener(UNAUTHORIZED_EVENT, onExpired);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onExpired);
  }, [check]);

  if (gate === 'checking') {
    return <div className="h-full flex items-center justify-center text-fg-muted" data-testid="boot-loading">Loading site, cameras and incidents…</div>;
  }
  if (gate === 'signin') return <SignIn onDone={() => setGate('open')} />;
  return <>{children}</>;
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.login(token.trim());
      setToken('');
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="h-full min-h-screen flex items-center justify-center bg-ink-950 text-fg p-4">
      <form onSubmit={submit} className="w-full max-w-sm border border-ink-700 bg-ink-900 rounded-sm p-5 space-y-4"
            aria-labelledby="signin-title">
        <div>
          <h1 id="signin-title" className="text-sm font-bold tracking-wide">Office Vision Twin</h1>
          <p className="text-xs text-fg-muted mt-1">Operator sign-in. Ask your administrator for the access token.</p>
        </div>
        <label className="block text-xs">
          <span className="text-fg-muted">Access token</span>
          <input type="password" autoComplete="current-password" required value={token} autoFocus
                 onChange={(e) => setToken(e.target.value)}
                 className="mt-1 w-full h-8 bg-ink-800 border border-ink-600 rounded-sm px-2 text-xs text-fg" />
        </label>
        {error && <p role="alert" className="text-xs text-sev-critical">{error}</p>}
        <Button type="submit" tone="primary" busy={busy} disabled={!token.trim()}>Sign in</Button>
      </form>
    </main>
  );
}

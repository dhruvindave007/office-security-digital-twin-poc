// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import App from '../App';
import { HttpError, installApi } from './fakeApi';

describe('sign-in gate', () => {
  it('asks for the token when the server requires one, then opens the dashboard', async () => {
    let signedIn = false;
    const server = installApi({
      'GET /api/auth/status': () => ({ auth_required: true, authenticated: signedIn }),
      'POST /api/auth/login': (_u, init) => {
        if (JSON.parse(String(init?.body)).token !== 'right-token') throw new HttpError(401, 'Invalid access token');
        signedIn = true;
        return { authenticated: true };
      },
    });
    render(<App />);
    const input = await screen.findByLabelText('Access token');
    expect(server.calls.some((c) => c.path.startsWith('/api/twin'))).toBe(false);   // nothing loaded before sign-in
    fireEvent.change(input, { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Invalid access token');
    fireEvent.change(input, { target: { value: 'right-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await screen.findByRole('banner');
  });

  it('opens straight away when the server needs no token', async () => {
    installApi();
    render(<App />);
    await screen.findByRole('banner');
    expect(screen.queryByLabelText('Access token')).toBeNull();
  });
});

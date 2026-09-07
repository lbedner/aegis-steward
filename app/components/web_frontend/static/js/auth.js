// Auth plumbing for the web frontend.
//
// Session auth lives in the `aegis_session` HttpOnly cookie the server sets
// during /login and /register. The browser attaches it automatically on
// same-origin requests, so this file is mostly about handling the
// session-expired case uniformly:
//
// 1. `fetchAuth(path, opts)` - fetch() wrapper that sends credentials,
//    refreshes once on 401, and bounces to /login (or /verify-pending on an
//    email-not-verified 403).
// 2. Global htmx hook - mirrors the same 401/403 handling for hx-get/post.

function _loginUrlWithNext() {
  // Preserve the current page path as ?next= so a successful sign-in returns
  // the user to where they were. Mirrors the server-side gate.
  const here = window.location.pathname;
  if (!here || here === '/login' || !here.startsWith('/')) return '/login';
  return '/login?next=' + encodeURIComponent(here);
}

// Single-flight refresh. The server rotates refresh tokens with reuse
// detection: replaying an already-revoked token revokes the whole family.
// A page can fire many authenticated requests in parallel, so when the
// access token expires they all 401 together - each calling /refresh
// independently would have call #2 replay the token call #1 just rotated
// and drop the session. One shared in-flight promise; everyone awaits it.
let _refreshInFlight = null;
function _tryRefresh() {
  if (!_refreshInFlight) {
    _refreshInFlight = fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin',
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        _refreshInFlight = null;
      });
  }
  return _refreshInFlight;
}

async function fetchAuth(path, opts = {}) {
  let resp = await fetch(path, { ...opts, credentials: 'same-origin' });
  if (resp.status === 401) {
    // Access token expired - try the refresh cookie before giving up. One
    // retry only; a second 401 means the refresh failed or the new session
    // is also rejected, so bounce to login.
    if (await _tryRefresh()) {
      resp = await fetch(path, { ...opts, credentials: 'same-origin' });
    }
  }
  if (resp.status === 401) {
    window.location = _loginUrlWithNext();
    return;
  }
  if (resp.status === 403) {
    // Peek at the body without consuming it, so callers can still read it if
    // they want to handle the error themselves.
    try {
      const clone = resp.clone();
      const body = await clone.json();
      if (body && body.detail === 'email_not_verified') {
        window.location = '/verify-pending';
        return;
      }
    } catch (_) {
      /* non-JSON body - fall through */
    }
  }
  return resp;
}

// If htmx gets a 401 from a protected endpoint, kick to /login. If it gets a
// 403 with `email_not_verified`, kick to /verify-pending so the user can
// resend the verification email.
document.addEventListener('htmx:responseError', (evt) => {
  const xhr = evt.detail.xhr;
  if (xhr.status === 401) {
    // Same refresh-first flow as fetchAuth. On success reload the page rather
    // than replaying the htmx request - the swap target may be mid-transition,
    // and a full reload re-renders the current URL with the fresh session.
    _tryRefresh().then((ok) => {
      if (ok) {
        window.location.reload();
      } else {
        window.location = _loginUrlWithNext();
      }
    });
    return;
  }
  if (xhr.status === 403) {
    try {
      const body = JSON.parse(xhr.responseText || '{}');
      if (body.detail === 'email_not_verified') {
        window.location = '/verify-pending';
      }
    } catch (_) {
      /* non-JSON body - leave as-is */
    }
  }
});

window.fetchAuth = fetchAuth;

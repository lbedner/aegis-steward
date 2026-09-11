/* htmx lifecycle hooks and the toast component.

   Fragments carry no inline scripts (plan decision 7), so nothing here
   re-executes swapped <script> tags. Behaviour that needs JS is registered
   once, on this file's load, and keyed off DOM events. */

// Toasts (pattern 6). A route sets `HX-Trigger: {"toast": {"text", "tone"}}`;
// htmx raises a `toast` DOM event; the region in base.html pushes it here.
document.addEventListener('alpine:init', () => {
  Alpine.data('toasts', () => ({
    items: [],
    _seq: 0,
    push(detail) {
      const id = ++this._seq;
      const tone = detail.tone || 'ok';
      this.items.push({ id, text: detail.text, tone });
      // Errors linger; confirmations get out of the way.
      setTimeout(() => this.dismiss(id), tone === 'error' ? 8000 : 4000);
    },
    dismiss(id) {
      this.items = this.items.filter((item) => item.id !== id);
    },
  }));
});

// The app shell's local state (layouts/app_shell.html): the mobile
// drawer, and the Illiana drawer beside every page. The Chat page IS the
// chat surface, so while it shows the drawer empties and its doors step
// aside (one instance of the surface at a time).
document.addEventListener('alpine:init', () => {
  Alpine.data('shell', (chatPath, onChat) => ({
    mobileOpen: false,
    illiana: false,
    onChat,
    settled() {
      this.onChat = !!document.querySelector('#app-content #chat');
      if (this.onChat) { this.illiana = false; this.$refs.illianaBody.innerHTML = ''; }
    },
    openIlliana() {
      this.illiana = true;
      if (!this.$refs.illianaBody.children.length) htmx.ajax('GET', `${chatPath}/drawer`, { target: '#illiana-body', swap: 'innerHTML' });
    },
  }));
});

function toast(text, tone) {
  window.dispatchEvent(new CustomEvent('toast', { detail: { text, tone } }));
}

// Server and network failures never swap (see the htmx-config
// responseHandling rules in base.html); they surface here as one error
// toast instead of a silently unchanged page.
document.body.addEventListener('htmx:responseError', (event) => {
  toast(`Request failed (${event.detail.xhr.status})`, 'error');
});
document.body.addEventListener('htmx:sendError', () => {
  toast('Network error. Check the connection and try again.', 'error');
});

// Keep the sidebar's aria-current in step with the URL. The server sets it
// on a full load; htmx swaps only #app-content, so after a pushed URL (and
// on back/forward) the link matching the new path takes over.
function markCurrentSection() {
  const path = window.location.pathname;
  document.querySelectorAll('#sidebar nav a[href]').forEach((a) => {
    if (a.getAttribute('href') === path) {
      a.setAttribute('aria-current', 'page');
    } else {
      a.removeAttribute('aria-current');
    }
  });
}
document.body.addEventListener('htmx:pushedIntoHistory', markCurrentSection);
window.addEventListener('popstate', markCurrentSection);

// The one modal (pattern 4). Any swap into #dialog-body opens the native
// <dialog>; closing it clears the body (see the dialog macro).
document.body.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target.id === 'dialog-body') {
    const dialog = document.getElementById('dialog');
    if (dialog && !dialog.open) dialog.showModal();
  }
});
// Sent as HX-Trigger-After-Settle (rendering.close_dialog), so it lands
// after the response's own swap has finished with #dialog-body.
document.body.addEventListener('dialog:close', () => {
  const dialog = document.getElementById('dialog');
  if (dialog && dialog.open) dialog.close();
});

// A select that names a <template> of options carries only what it needs
// to READ: its own value and the blank. The rest arrives the first time
// it is opened, cloned from the one copy on the page.
//
// The register's category cell is why. Inline, its 270 options were
// 17.5 KB of a 20.8 KB row, so fifty rows sent 1,041 KB of a 1,097 KB
// response - and the filter above re-sends the whole register on every
// keystroke, which is what made searching an account feel slow.
//
// Delegated and idempotent: rows arrive by out-of-band swap all the time,
// and a select that has already been filled is left alone.
function fillOptions(select) {
  if (!select || select.dataset.filled) return;
  const source = document.getElementById(select.dataset.options);
  if (!source) return;
  const keep = select.value;
  const blank = select.querySelector('option[value=""]');
  select.replaceChildren();
  if (blank) select.append(blank);
  select.append(source.content.cloneNode(true));
  select.dataset.filled = '1';
  select.value = keep;
}
for (const type of ['focusin', 'pointerdown']) {
  document.addEventListener(type, (event) => {
    fillOptions(event.target.closest?.('select[data-options]'));
  });
}

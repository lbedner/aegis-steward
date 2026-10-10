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
  // ``drawerUrl``: the surface the drawer opens, when not the Chat page's
  // (the Overseer's own).
  Alpine.data('shell', (chatPath, onChat, drawerUrl = `${chatPath}/drawer`) => ({
    mobileOpen: false,
    illiana: false,
    onChat,
    settled() {
      this.onChat = !!document.querySelector('#app-content #chat');
      if (this.onChat) { this.illiana = false; this.$refs.illianaBody.innerHTML = ''; }
    },
    openIlliana() {
      this.illiana = true;
      if (!this.$refs.illianaBody.children.length) htmx.ajax('GET', drawerUrl, { target: '#illiana-body', swap: 'innerHTML' });
    },
  }));
});

// A dropdown (macros/layout.html) opens on the side its macro asked for.
// A button that wrapped to the other edge of a phone would open it off
// the screen, so on opening it takes the other side, then slides back
// whatever still overhangs. ``toggle`` does not bubble, so it is caught
// on the way down.
function placeDropdown(details) {
  const menu = details.lastElementChild;
  menu.style.left = menu.style.right = menu.style.translate = '';
  if (!details.open) return;
  // The page's width, not innerWidth: a phone zooms out to show a menu
  // that overhangs, which widens innerWidth until it "fits". The gutter
  // is the macro's ``max-w-[calc(100vw-2rem)]``.
  const gutter = 16;
  const width = document.documentElement.clientWidth;
  const overhang = () => {
    const r = menu.getBoundingClientRect();
    if (r.left < gutter) return gutter - r.left;
    if (r.right > width - gutter) return width - gutter - r.right;
    return 0;
  };
  if (!overhang()) return;
  const [side, other] = menu.classList.contains('right-0') ? ['right', 'left'] : ['left', 'right'];
  menu.style[side] = 'auto';
  menu.style[other] = '0';
  const shift = overhang();
  if (shift) menu.style.translate = `${shift}px 0`;
}
document.addEventListener('toggle', (event) => {
  if (event.target.matches?.('details[data-dropdown]')) placeDropdown(event.target);
}, true);
// A click anywhere else closes an open dropdown, and so does picking one
// of its items; a panel's controls (the account filter) leave it open.
document.addEventListener('click', (event) => {
  for (const details of document.querySelectorAll('details[data-dropdown][open]')) {
    if (!details.contains(event.target) || event.target.closest('[role=menu] button')) details.removeAttribute('open');
  }
});

// Markup a script needs is cloned from a <template> in its partial, so
// styling has one home (the template), never a JS string.
function clone(id) {
  return document.getElementById(id).content.firstElementChild.cloneNode(true);
}

// The chat surface's parts that both chat.js and voice.js reach: the
// thread (streamed into, reloaded mid-call), the conversation it is, and
// the composer's box.
function chatThread() {
  return document.getElementById('chat-thread');
}
function chatConversation() {
  return document.getElementById('chat-conversation');
}
// The conversation's id, or null for a new one; and a new id kept.
function conversationId() {
  return chatConversation()?.value || null;
}
function setConversation(id) {
  const field = chatConversation();
  if (id && field) field.value = id;
}
function chatComposer() {
  return document.getElementById('chat-composer');
}
function composerBox() {
  return chatComposer()?.querySelector('textarea');
}
// Text put into a box Alpine owns through x-model: it never sees a
// direct assignment, so the input event tells it.
function setBoxText(box, text) {
  box.value = text;
  box.dispatchEvent(new Event('input', { bubbles: true }));
}

function toast(text, tone) {
  window.dispatchEvent(new CustomEvent('toast', { detail: { text, tone } }));
}

// The one copier: any ``data-copy`` button puts its text on the clipboard.
// navigator.clipboard exists only in a secure context, which a stack served
// over plain http to anything but localhost is not, so the old execCommand
// path keeps every copy button working on a LAN address.
function copyText(text) {
  if (navigator.clipboard) return navigator.clipboard.writeText(text);
  const box = document.createElement('textarea');
  box.value = text;
  box.setAttribute('readonly', '');
  box.style.cssText = 'position:fixed;top:-1000px;opacity:0';
  document.body.appendChild(box);
  box.select();
  const ok = document.execCommand('copy');
  box.remove();
  return ok ? Promise.resolve() : Promise.reject(new Error('copy refused'));
}
// The clipboard says nothing back, so the button does: one with a tick of
// its own (``copy_icon``) shows it for a beat; any other says so in a toast.
const COPIED_MS = 1200;
function showCopied(button, on) {
  button.querySelector('[data-copy-idle]')?.classList.toggle('hidden', on);
  button.querySelector('[data-copy-done]')?.classList.toggle('hidden', !on);
  if (on) button.dataset.copied = 'true';
  else delete button.dataset.copied;
}
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button || !button.dataset.copy) return;
  const ticks = button.querySelector('[data-copy-done]');
  copyText(button.dataset.copy).then(
    () => {
      if (!ticks) return toast('Copied to clipboard', 'ok');
      showCopied(button, true);
      clearTimeout(button._settle);
      button._settle = setTimeout(() => showCopied(button, false), COPIED_MS);
    },
    () => toast('Could not copy - select the text instead', 'error'),
  );
});

// Any ``data-scroll-to`` button brings the element its selector names into
// view (a list's first or last row), scrolling only what it must.
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-scroll-to]');
  if (!button) return;
  document.querySelector(button.dataset.scrollTo)
    ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
});

// Server and network failures never swap (see the htmx-config
// responseHandling rules in base.html); they surface here as one error
// toast instead of a silently unchanged page.
document.body.addEventListener('htmx:responseError', (event) => {
  if (event.detail.elt.dataset.apiDone !== undefined) return; // told below
  toast(`Request failed (${event.detail.xhr.status})`, 'error');
});

// A button that calls the JSON API directly (the Overseer's Authentication
// actions) says what it did in `data-api-done`. The API answers with JSON,
// not HTML, so nothing swaps: on success the dialog closes, the toast
// shows, and the page's main area is re-requested to show the change; on
// failure the API's own `detail` is the toast.
document.body.addEventListener('htmx:afterRequest', (event) => {
  const done = event.detail.elt.dataset.apiDone;
  if (done === undefined) return;
  if (!event.detail.successful) {
    toast(apiDetail(event.detail.xhr), 'error');
    return;
  }
  document.body.dispatchEvent(new Event('dialog:close'));
  toast(done, 'ok');
  // The whole URL: a page's state (a filter, the open folder) lives in
  // its query string.
  htmx.ajax('GET', window.location.pathname + window.location.search, {
    target: '#overseer-main', select: '#overseer-main', swap: 'outerHTML',
  });
});

function apiDetail(xhr) {
  try {
    const detail = JSON.parse(xhr.responseText).detail;
    if (typeof detail === 'string') return detail;
  } catch (_) { /* not JSON */ }
  return `Request failed (${xhr.status})`;
}
document.body.addEventListener('htmx:sendError', () => {
  toast('Network error. Check the connection and try again.', 'error');
});

// Keep a sidebar's aria-current in step with the URL. The server sets it
// on a full load; htmx swaps only what is right of the sidebar, so after a
// pushed URL (and on back/forward) the mark moves to the link whose page
// holds the address: the longest such link, so a home link marks only
// the home page (the server's rule, ``nav_item``).
function markCurrent(links, path) {
  const holds = (href) => path === href || path.startsWith(href + '/');
  const best = [...links].filter((link) => holds(link.getAttribute('href')))
    .sort((a, b) => b.getAttribute('href').length - a.getAttribute('href').length)[0];
  for (const link of links) {
    if (link === best) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}
function markCurrentSection() {
  const path = window.location.pathname;
  markCurrent(document.querySelectorAll('#sidebar nav a[href]'), path);
  markCurrent(document.querySelectorAll('#overseer-nav a[href]'), path);
  // Overseer > Code swaps only its file: the tree's link is the address.
  markCurrent(document.querySelectorAll('[data-code-tree] a[href]'), path + window.location.search);
}
document.body.addEventListener('htmx:pushedIntoHistory', markCurrentSection);
window.addEventListener('popstate', markCurrentSection);

// Cmd-P (Ctrl-P) on Overseer > Code jumps to a file by name, not print.
document.addEventListener('keydown', (event) => {
  const jump = document.querySelector('[data-code-jump]');
  if (!jump || event.key !== 'p' || !(event.metaKey || event.ctrlKey)) return;
  event.preventDefault();
  jump.select();
});

// A name clicked in an Overseer > Code Python file peeks beside it (the
// ``#code-peek`` popover): its definition and references. The line is the
// clicked line's address (``data-line-prefix`` and its number), the column
// how far into the line's source (its number left out) the name starts.
function sourceColumn(line, token) {
  const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
  let column = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (token.contains(node)) return column;
    if (!node.parentElement.closest('.linenos')) column += node.length;
  }
  return null;
}
document.addEventListener('click', (event) => {
  const pane = event.target.closest && event.target.closest('[data-code-symbol]');
  const token = pane && event.target.closest('.highlight :is(.n, .nf, .nc, .nn)');
  const prefix = pane && pane.dataset.linePrefix;
  const line = token && token.closest(`[id^="${prefix}"]`);
  if (!line || getSelection().toString()) return;
  const column = sourceColumn(line, token);
  if (column === null) return;
  // The symbol URL names the open file; the click adds where in it.
  const query = new URLSearchParams({ line: line.id.slice(prefix.length), col: column });
  const peek = document.getElementById('code-peek');
  htmx.ajax('GET', `${pane.dataset.codeSymbol}&${query}`, { target: peek, swap: 'innerHTML' }).then(() => {
    Object.assign(peek.style, { position: 'fixed', inset: 'auto', margin: '0' });
    peek.showPopover();
    // Measured once shown: below the name, or above it near the bottom.
    const at = token.getBoundingClientRect();
    const below = at.bottom + 4 + peek.offsetHeight <= window.innerHeight;
    const left = Math.max(8, Math.min(at.left, window.innerWidth - peek.offsetWidth - 8));
    peek.style.top = `${below ? at.bottom + 4 : Math.max(8, at.top - 4 - peek.offsetHeight)}px`;
    peek.style.left = `${left}px`;
  });
});

// The one modal (pattern 4). Any swap into #dialog-body opens the native
// <dialog>, as does a script that fills it itself (chat's image viewer);
// closing it clears the body (see the dialog macro).
function openDialog() {
  const dialog = document.getElementById('dialog');
  if (dialog && !dialog.open) dialog.showModal();
}
// A swap into #drawer-body (``hx_drawer``) opens the side drawer the same way.
document.body.addEventListener('htmx:afterSwap', (event) => {
  // A live stream's swap (the SSE extension) carries no target.
  const id = event.detail.target?.id;
  if (id === 'dialog-body') openDialog();
  if (id === 'drawer-body') {
    const drawer = document.getElementById('drawer');
    if (drawer && !drawer.open) drawer.show();
  }
});
// A dialog's address opened directly arrives here as ?dialog=<its path>
// (rendering.dialog): open it over the page, and drop the param so a
// reload does not open it again. Same-origin paths only.
{
  const named = new URLSearchParams(window.location.search).get('dialog');
  if (named?.startsWith('/') && !named.startsWith('//')) {
    const url = new URL(window.location.href);
    url.searchParams.delete('dialog');
    window.history.replaceState(window.history.state, '', url);
    htmx.ajax('GET', named, '#dialog-body');
  }
}
// The one drawer. A list renders a ``drawer_sync`` marker naming what is
// open, so the address bar is the state: a row click navigates the list
// with ``?document=12``, a reload or a shared link opens the same item,
// and a list without one (after a delete, say) closes it. Closing drops
// the parameter from the address without a request.
function syncDrawer(root) {
  const marker = root.querySelector && root.querySelector('[data-drawer-sync]');
  const drawer = document.getElementById('drawer');
  if (!drawer) return;
  if (!marker) {
    // Another section took the page: its item is not this one.
    const page = root.id === 'overseer-main' || (root.querySelector && root.querySelector('#overseer-main'));
    if (page && drawer.open) drawer.close();
    return;
  }
  drawer.dataset.param = marker.dataset.drawerParam;
  const url = marker.dataset.drawerUrl;
  if (!url) {
    if (drawer.open) drawer.close();
    return;
  }
  htmx.ajax('GET', url, { target: '#drawer-body', swap: 'innerHTML' })
    .then(() => { if (!drawer.open) drawer.show(); });
}
document.body.addEventListener('htmx:load', (event) => syncDrawer(event.detail.elt));

// Light dismiss for the drawer and the modal. A click outside (the modal's
// backdrop) or Escape closes it; a click on a row that opens another item
// switches the drawer to it; typed-but-unsaved work holds either open with
// a toast. An X or a Cancel (``data-dialog-close``) still closes on
// purpose. A form inside is dirty once typed in, and clean again when it
// saves or the panel reloads.
function outsideClick(panel, target) {
  if (!panel.open || panel.contains(target)) return 'ignore';
  if (target.closest('dialog[open]')) return 'ignore'; // the modal, over the drawer
  if (panel.dataset.dirty) return 'hold';
  const param = panel.dataset.param;
  const opensItem = `[href*="${param}="], [hx-get*="${param}="]`; // a row, "New post"
  return param && target.closest(opensItem) ? 'ignore' : 'close';
}
function dismiss(panel) {
  if (panel.dataset.dirty) {
    toast('Unsaved changes: save or close', 'warn');
    return false;
  }
  panel.close();
  return true;
}
function markClean(panel) {
  if (panel) delete panel.dataset.dirty;
}
document.addEventListener('click', (event) => {
  const drawer = document.getElementById('drawer');
  const modal = document.getElementById('dialog');
  const closer = event.target.closest('[data-dialog-close]');
  if (closer) {
    const panel = closer.closest('dialog');
    markClean(panel);
    panel?.close();
    return;
  }
  if (modal && modal.open) {
    // A modal's own clicks land inside its content; one on the dialog
    // element itself is its backdrop.
    if (event.target === modal) dismiss(modal);
    return; // the modal, over the drawer
  }
  if (!drawer) return;
  const verdict = outsideClick(drawer, event.target);
  if (verdict === 'close') drawer.close();
  if (verdict === 'hold') {
    event.preventDefault();
    event.stopPropagation();
    dismiss(drawer);
  }
}, true);
document.addEventListener('keydown', (event) => {
  const drawer = document.getElementById('drawer');
  const modal = document.getElementById('dialog');
  if (event.key !== 'Escape' || (modal && modal.open)) return; // its cancel, below
  if (drawer && drawer.open) dismiss(drawer);
});
document.addEventListener('input', (event) => {
  const panel = event.target.closest && event.target.closest('#drawer, #dialog');
  if (panel && event.target.closest('form')) panel.dataset.dirty = '1';
});
document.body.addEventListener('htmx:afterRequest', (event) => {
  const form = event.detail.elt.closest && event.detail.elt.closest('form');
  if (!event.detail.successful || !form) return;
  let said = {};
  try { said = JSON.parse(event.detail.xhr.getResponseHeader('HX-Trigger') || '{}'); } catch { said = {}; }
  if (said.toast && said.toast.tone === 'error') return; // refused: still unsaved
  markClean(form.closest('#drawer, #dialog'));
});
document.body.addEventListener('htmx:afterSwap', (event) => {
  if (['drawer-body', 'dialog-body'].includes(event.detail.target?.id)) {
    markClean(event.detail.target.closest('dialog'));
  }
});
// ``close`` does not bubble; the capture phase still sees it.
document.addEventListener('close', (event) => {
  const modal = event.target;
  if (modal.id === 'dialog') {
    markClean(modal);
    modal.querySelector('#dialog-body').innerHTML = '';
    return;
  }
  const drawer = event.target;
  if (drawer.id !== 'drawer') return;
  markClean(drawer);
  drawer.querySelector('#drawer-body').innerHTML = '';
  const url = new URL(window.location.href);
  if (drawer.dataset.param && url.searchParams.has(drawer.dataset.param)) {
    url.searchParams.delete(drawer.dataset.param);
    history.replaceState(history.state, '', url);
  }
}, true);
// Sent as HX-Trigger-After-Settle (rendering.close_dialog), so it lands
// after the response's own swap has finished with #dialog-body.
document.body.addEventListener('dialog:close', () => {
  const dialog = document.getElementById('dialog');
  if (dialog?.open) dialog.close();
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

// The progress bar along the top (#page-progress in base.html): on while
// htmx has a request out, while part of the page is still on its way
// (``data-pending``: a section whose data a live stream brings), and from
// a click on a link that loads another page here until that page replaces
// this one. A link htmx took, a new tab, another site, a download or an
// anchor never starts it.
let requestsOut = 0;
let leaving = false;
function syncProgress() {
  const on = leaving || requestsOut > 0 || document.querySelector('[data-pending]') !== null;
  document.getElementById('page-progress')?.classList.toggle('is-loading', on);
}
function navigates(event) {
  const modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
  if (event.button !== 0 || modified || event.defaultPrevented) return false;
  const link = event.target.closest && event.target.closest('a[href]');
  if (!link || link.target || link.hasAttribute('download')) return false;
  return link.origin === window.location.origin && !link.getAttribute('href').startsWith('#');
}
document.addEventListener('click', (event) => {
  if (!navigates(event)) return;
  leaving = true;
  syncProgress();
});
// Counted down when the request ends, not on htmx:afterRequest: that fires
// on the element that sent it, and an element a live frame swapped out while
// its request was out (a Restart button in a row the stream re-sends) is no
// longer in the page, so the event never reaches here.
document.body.addEventListener('htmx:beforeRequest', (event) => {
  requestsOut += 1;
  syncProgress();
  event.detail.xhr.addEventListener('loadend', () => {
    requestsOut = Math.max(0, requestsOut - 1);
    syncProgress();
  }, { once: true });
});
// The page's first paint, and every swap after it (a stream's included).
document.body.addEventListener('htmx:load', syncProgress);
// Back to this page from the browser's cache: nothing is loading.
window.addEventListener('pageshow', () => {
  leaving = false;
  syncProgress();
});

// A range strip (``data-range``: the Logs volume): a click on a bar narrows
// to it (its own link), and a drag across several narrows to all of them:
// the first bar's request, every filter it carries, with the last bar's
// end (``data-to``), whichever way the drag ran.
function rangeUrl(href, to) {
  const url = new URL(href, 'http://local');
  url.searchParams.set('to', to);
  return url.pathname + url.search;
}
function spanned(bars, a, b) {
  const [i, j] = [bars.indexOf(a), bars.indexOf(b)].sort((x, y) => x - y);
  return bars.slice(i, j + 1);
}
let drag = null;
// The click that ends a drag is not a click on the bar under it.
let dragged = false;
function rangeBar(event) {
  return event.target.closest && event.target.closest('[data-range] [data-to]');
}
function markDrag(on) {
  const covered = new Set(on ? spanned(drag.bars, drag.first, drag.last) : []);
  drag.bars.forEach((bar) => {
    bar.toggleAttribute('data-selecting', covered.has(bar));
  });
}
document.addEventListener('pointerdown', (event) => {
  const bar = rangeBar(event);
  if (!bar || event.button !== 0) return;
  const bars = [...bar.closest('[data-range]').querySelectorAll('[data-to]')];
  drag = { bars, first: bar, last: bar };
  markDrag(true);
});
document.addEventListener('pointerover', (event) => {
  const bar = drag && rangeBar(event);
  if (!bar || !drag.bars.includes(bar)) return;
  drag.last = bar;
  markDrag(true);
});
document.addEventListener('pointerup', () => {
  if (!drag) return;
  const covered = spanned(drag.bars, drag.first, drag.last);
  markDrag(false);
  drag = null;
  if (covered.length < 2) return; // a click: the bar's own link
  const [from, to] = [covered[0], covered[covered.length - 1]];
  dragged = true;
  htmx.ajax('GET', rangeUrl(from.getAttribute('href'), to.dataset.to), { source: from });
});
document.addEventListener('click', (event) => {
  if (!dragged) return;
  dragged = false;
  if (!event.target.closest || !event.target.closest('[data-range]')) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);

// The bars behind what is on screen: a strip that names a list
// (``data-range-of``, its rows' times in ``data-at``) marks the bars from
// its oldest to its newest visible line, as it scrolls and as lines arrive.
function inView(bars, lo, hi) {
  if (lo === null || hi === null) return [];
  return bars.filter((bar) => Number(bar.dataset.from) <= hi && Number(bar.dataset.to) > lo);
}
// The rows on screen in the lists a strip or a search follows, kept by one
// IntersectionObserver, so nothing measures every row on a scroll or as a
// search shows hundreds again; a hidden row is not on screen.
const onScreen = new Set();
const screenWatch = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) onScreen.add(entry.target);
    else onScreen.delete(entry.target);
  });
  queueScreen();
});
function watchedLists() {
  const ranged = [...document.querySelectorAll('[data-range-of]')].map((strip) => document.getElementById(strip.dataset.rangeOf));
  const searched = [...document.querySelectorAll('input[data-filter]')].map((input) => document.querySelector(input.dataset.filter));
  return [...new Set([...ranged, ...searched].filter(Boolean))];
}
// Each list is watched once, and then only the rows that arrive (a stream's
// batch) are: nothing re-walks a list that only grows.
const watched = new WeakSet();
const rowsAdded = typeof MutationObserver === 'undefined' ? null : new MutationObserver((records) => {
  records.forEach((record) => {
    const rows = [...record.addedNodes].filter((node) => node.nodeType === 1);
    rows.forEach((row) => {
      screenWatch?.observe(row);
    });
    filterRows(rows, searchOf(record.target));
  });
  queueScreen();
});
function watchLists() {
  watchedLists().forEach((list) => {
    if (watched.has(list)) return;
    watched.add(list);
    for (const row of list.children) screenWatch?.observe(row);
    rowsAdded?.observe(list, { childList: true });
    filterRows([...list.children], searchOf(list));
  });
}
function rowsOnScreen(list) {
  return [...onScreen].filter((row) => list.contains(row));
}
function markInView() {
  document.querySelectorAll('[data-range-of]').forEach((strip) => {
    const list = document.getElementById(strip.dataset.rangeOf);
    const times = list ? rowsOnScreen(list).map((row) => Number(row.dataset.at)).filter(Boolean) : [];
    const span = times.length ? [Math.min(...times), Math.max(...times)] : [null, null];
    const bars = [...strip.querySelectorAll('[data-to]')];
    const seen = new Set(inView(bars, ...span));
    bars.forEach((bar) => {
      bar.toggleAttribute('data-in-view', seen.has(bar));
    });
  });
}
let screenFrame = 0;
function queueScreen() {
  cancelAnimationFrame(screenFrame);
  screenFrame = requestAnimationFrame(() => {
    onScreen.forEach((row) => {
      if (!row.isConnected) onScreen.delete(row);
    });
    markInView();
    highlightOnScreen();
  });
}
// The first paint and every swap: a new list is watched (and searched, its
// box filled from the URL), and what is on screen marked.
document.body.addEventListener('htmx:load', () => {
  watchLists();
  queueScreen();
});

// A search over what is on the page (the ``filter_input`` macro): typing
// narrows its target's rows (``data-filter``) to those holding the text,
// any case, and marks each match; rows that arrive later follow it; the
// text rides in the URL. No request: whatever is there, at once.
// splitMatches is split_matches' twin (app.core.formatting), held to one
// table of cases (tests/test_formatting.py MATCH_CASES).
function splitMatches(text, query) {
  if (!query) return [[text, false]];
  const runs = [];
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  let at = 0;
  for (let hit = lower.indexOf(needle); hit !== -1; hit = lower.indexOf(needle, at)) {
    if (hit > at) runs.push([text.slice(at, hit), false]);
    runs.push([text.slice(hit, hit + needle.length), true]);
    at = hit + needle.length;
  }
  if (at < text.length) runs.push([text.slice(at), false]);
  return runs.length ? runs : [[text, false]];
}
// Each row's text, lowercased once: a row's lines do not change.
const rowText = new WeakMap();
function textOf(row) {
  let text = rowText.get(row);
  if (text === undefined) {
    text = row.textContent.toLowerCase();
    rowText.set(row, text);
  }
  return text;
}
// The matches on screen, painted with the CSS Highlight API
// (``::highlight(found)`` in input.css): no element added or removed, and
// only the rows showing, re-painted as they scroll. Without the API the
// rows still filter, unmarked.
function rangesIn(row, query) {
  const ranges = [];
  const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement.closest('button, svg') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  while (walker.nextNode()) {
    const node = walker.currentNode;
    let at = 0;
    splitMatches(node.data, query).forEach(([run, hit]) => {
      if (hit) {
        const range = new Range();
        range.setStart(node, at);
        range.setEnd(node, at + run.length);
        ranges.push(range);
      }
      at += run.length;
    });
  }
  return ranges;
}
function highlightOnScreen() {
  if (typeof CSS === 'undefined' || !CSS.highlights) return;
  const ranges = [];
  document.querySelectorAll('input[data-filter]').forEach((input) => {
    const query = input.value.trim();
    const list = document.querySelector(input.dataset.filter);
    if (query && list) rowsOnScreen(list).forEach((row) => {
      ranges.push(...rangesIn(row, query));
    });
  });
  if (ranges.length) CSS.highlights.set('found', new Highlight(...ranges));
  else CSS.highlights.delete('found');
}
let urlTimer = 0;
function keepInUrl(input, query) {
  clearTimeout(urlTimer);
  urlTimer = setTimeout(() => {
    const url = new URL(window.location.href);
    if (query) url.searchParams.set(input.name, query);
    else url.searchParams.delete(input.name);
    window.history.replaceState(window.history.state, '', url);
  }, 300);
}
// The search box over ``list``, if it has one.
function searchOf(list) {
  return [...document.querySelectorAll('input[data-filter]')].find((input) => list.matches(input.dataset.filter));
}
// ``rows`` shown or hidden by ``input``'s text, touching only those that change.
function filterRows(rows, input) {
  const needle = input ? input.value.trim().toLowerCase() : '';
  rows.forEach((row) => {
    const hide = Boolean(needle) && !textOf(row).includes(needle);
    if (row.hidden !== hide) row.hidden = hide;
  });
}
// Typing faster than a frame applies once, with the latest text.
let filterFrame = 0;
document.addEventListener('input', (event) => {
  if (!event.target.matches || !event.target.matches('[data-filter]')) return;
  const input = event.target;
  cancelAnimationFrame(filterFrame);
  filterFrame = requestAnimationFrame(() => {
    const list = document.querySelector(input.dataset.filter);
    if (!list) return;
    filterRows([...list.children], input);
    keepInUrl(input, input.value.trim());
    queueScreen();
  });
});

// The map (_overview_map.html): hovering a node keeps it, its lines and the
// nodes they join lit, and dims the rest, so "who uses the Database" reads
// at a glance; a click pins it while the pointer moves on, and Escape or a
// click on the empty canvas lets it go. Kept on the stream's wrapper
// (#overview-stack, outside what each frame swaps), so a live frame keeps it.
function mapFocus(lines, key) {
  const lit = new Set([key]);
  lines.forEach(({ from, to }) => {
    if (from === key) lit.add(to);
    if (to === key) lit.add(from);
  });
  return lit;
}
function applyMapFocus(root) {
  const map = root && root.querySelector('[data-map]');
  if (!map) return;
  const key = root.dataset.pinned || root.dataset.hover;
  const lines = [...map.querySelectorAll('path[data-link]')];
  const lit = key ? mapFocus(lines.map((line) => line.dataset), key) : new Set();
  lines.forEach((line) => {
    line.toggleAttribute('data-dim', Boolean(key) && line.dataset.from !== key && line.dataset.to !== key);
  });
  map.querySelectorAll('[data-node]').forEach((node) => {
    node.toggleAttribute('data-dim', Boolean(key) && !lit.has(node.dataset.node));
  });
}
document.addEventListener('mouseover', (event) => {
  const root = document.getElementById('overview-stack');
  if (!root) return;
  const node = event.target.closest && event.target.closest('[data-map] [data-node]');
  const key = node ? node.dataset.node : '';
  if ((root.dataset.hover || '') === key) return;
  if (key) root.dataset.hover = key;
  else delete root.dataset.hover;
  applyMapFocus(root);
});
document.addEventListener('click', (event) => {
  const root = document.getElementById('overview-stack');
  if (!root || !event.target.closest || !event.target.closest('[data-map]') || event.target.closest('a')) return;
  const node = event.target.closest('[data-map] [data-node]');
  if (node) root.dataset.pinned = node.dataset.node;
  else delete root.dataset.pinned;
  applyMapFocus(root);
});
document.addEventListener('keydown', (event) => {
  const root = document.getElementById('overview-stack');
  if (event.key !== 'Escape' || !root || !root.dataset.pinned) return;
  delete root.dataset.pinned;
  applyMapFocus(root);
});
document.body.addEventListener('htmx:afterSwap', () => {
  applyMapFocus(document.getElementById('overview-stack'));
});

// For the node tests (tests/web/test_app_js.py).
if (typeof module !== 'undefined') module.exports = { outsideClick, dismiss, navigates, markCurrent, rangeUrl, spanned, inView, splitMatches, mapFocus, sourceColumn };

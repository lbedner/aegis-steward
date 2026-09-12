/* The chat stream (island #2).

   The page is server HTML; this owns only the live turn. The composer's
   post appends a user bubble and a streaming bubble carrying the text to
   send; this reads that bubble, POSTs to the API's SSE endpoint and
   renders the frames into it: `chunk` text through a typewriter, `tool`
   calls as trail rows, `error` as a line. When the stream completes the
   stored message is swapped in over the bubble (server-rendered), so the
   client never renders a settled message.

   Transport is fetch + a streamed SSE body: chat needs a POST body, and
   EventSource is GET-only. Frames are `event: <name>` + `data: <json>`
   pairs separated by a blank line. */
(() => {
  const CURSOR = '▌';
  let controller = null; // the in-flight turn's AbortController

  const root = () => document.getElementById('chat');
  const thread = () => document.getElementById('chat-thread');
  const scroller = () => document.getElementById('chat-scroll');
  const config = () => JSON.parse(root().dataset.chat);
  // Markup the script needs is cloned from <template>s in the surface
  // partial, so styling has one home (the template), never a JS string.
  const clone = (id) => document.getElementById(id).content.firstElementChild.cloneNode(true);

  // --- Scroll: stay with the newest unless the reader went looking ------
  // Pinned is an INTENT, not a position. Content settles after it arrives
  // (images decode, markdown lays out, a drawer finishes opening), so a
  // single scroll-to-bottom at load lands short — it ran against a height
  // that was still growing. Hold the intent instead and re-apply it every
  // time the thread changes size.
  const SLACK = 24;
  let pinned = true;
  const nearBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight <= SLACK;
  const setPinned = (value) => {
    pinned = value;
    // The jump button is the intent's only visible state: it offers the
    // way back, so it shows exactly when the reader is not following.
    const jump = document.getElementById('chat-jump');
    if (jump) jump.hidden = pinned;
  };
  const follow = (force) => {
    const el = scroller();
    if (!el) return;
    if (force) setPinned(true);
    if (pinned) el.scrollTop = el.scrollHeight;
  };
  // Only going UP unpins. Content arriving fires a scroll event too - the
  // box is suddenly taller than it was - and treating that as the reader
  // walking away was what left a fresh open stranded mid-conversation:
  // the intent was dropped a frame after it was set, by the very growth
  // it was waiting for. Coming back to the bottom pins again.
  let lastTop = 0;
  document.addEventListener('scroll', (event) => {
    const el = scroller();
    if (!el || event.target !== el) return;
    if (el.scrollTop < lastTop - 1) setPinned(false);
    else if (nearBottom(el)) setPinned(true);
    lastTop = el.scrollTop;
  }, true);
  // Whatever the thread does next - an image arriving, an answer growing,
  // the drawer taking its width - is a reason to re-apply the intent.
  let growth = null;
  const watchGrowth = () => {
    const el = thread();
    if (!el) return;
    if (!growth) growth = new ResizeObserver(() => follow());
    growth.disconnect();
    growth.observe(el);
    const box = scroller();
    if (box) growth.observe(box);
  };
  document.addEventListener('click', (event) => {
    const jump = event.target.closest('#chat-jump');
    if (jump) follow(true);
    const replay = event.target.closest('[data-replay]');
    if (replay) {
      const text = replay.closest('[data-role=user]').querySelector('[data-text]').textContent;
      const form = document.getElementById('chat-composer');
      form.querySelector('textarea').value = text;
      form.requestSubmit();
    }
    const copy = event.target.closest('[data-copy]');
    if (copy) {
      const raw = copy.closest('[data-role=assistant]').querySelector('template[data-raw]');
      if (raw) {
        copied(copy);
        write(raw.innerHTML).catch(() => settle(copy));
      }
    }
    if (event.target.closest('#chat-stop') && controller) controller.abort();
    const view = event.target.closest('[data-view-image]');
    if (view) viewImage(view.dataset.viewImage, view.title || view.getAttribute('alt') || '');
  });

  // navigator.clipboard exists only in a secure context, which a stack
  // served over plain http to anything but localhost is not - so the
  // modern call is the preference, not the requirement, and the old
  // execCommand path keeps the button working on a LAN address.
  const write = (text) => {
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
  };

  // The clipboard says nothing back, so the button does: a tick for a
  // beat, then the clipboard again. It answers the CLICK, not the
  // promise — a write that hangs (or a browser that refuses one to an
  // unfocused document) would otherwise leave the button looking broken,
  // which is the thing this exists to prevent. A refusal settles it
  // early, so a failed copy flashes rather than claims success.
  const COPIED_MS = 1200;
  const settle = (button) => {
    delete button.dataset.copied;
    button.querySelector('[data-copy-idle]')?.classList.remove('hidden');
    button.querySelector('[data-copy-done]')?.classList.add('hidden');
  };
  const copied = (button) => {
    button.dataset.copied = 'true';
    button.querySelector('[data-copy-idle]')?.classList.add('hidden');
    button.querySelector('[data-copy-done]')?.classList.remove('hidden');
    clearTimeout(button._settle);
    button._settle = setTimeout(() => settle(button), COPIED_MS);
  };

  // A thumbnail opens its image in the one modal (pattern 4), not a tab.
  const viewImage = (url, name) => {
    const body = document.getElementById('dialog-body');
    const dialog = document.getElementById('dialog');
    if (!body || !dialog) return;
    const img = clone('chat-image');
    img.src = url;
    img.alt = name;
    body.replaceChildren(img);
    if (!dialog.open) dialog.showModal();
  };

  // --- Live markdown: enough to read while it streams ----------------
  // The settled message is rendered server-side; this only keeps a
  // half-arrived answer legible (bold, code, bullets, balanced fences).
  const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const balanceFences = (t) => ((t.match(/```/g) || []).length % 2 === 1 ? `${t}\n\`\`\`` : t);
  const live = (text) => {
    const blocks = balanceFences(text).split('```');
    return blocks.map((block, i) => {
      if (i % 2 === 1) return `<pre><code>${escapeHtml(block.replace(/^\w*\n/, ''))}</code></pre>`;
      let t = escapeHtml(block);
      t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
      let html = '';
      let inList = false;
      for (const line of t.split('\n')) {
        const m = line.match(/^\s*[-*]\s+(.*)$/);
        const h = line.match(/^(#{1,3})\s+(.*)$/);
        if (m) {
          if (!inList) { html += '<ul>'; inList = true; }
          html += `<li>${m[1]}</li>`;
          continue;
        }
        if (inList) { html += '</ul>'; inList = false; }
        if (h) html += `<h${h[1].length}>${h[2]}</h${h[1].length}>`;
        else if (line.trim()) html += `<p>${line}</p>`;
      }
      if (inList) html += '</ul>';
      return html;
    }).join('');
  };

  // --- Typewriter: decouple network bursts from display ----------------
  // Reveal per frame is backlog-proportional: ~2 chars/frame at rest
  // (~120 chars/s), exponential drain on a burst so lag stays bounded.
  const typewriter = (body) => {
    let shown = '';
    let buf = '';
    let raf = null;
    const paint = () => { body.innerHTML = live(shown + (buf.length ? CURSOR : '')); follow(); };
    const step = () => {
      if (!buf.length) { raf = null; paint(); return; }
      const n = Math.max(2, Math.ceil(buf.length * 0.1));
      shown += buf.slice(0, n);
      buf = buf.slice(n);
      paint();
      raf = requestAnimationFrame(step);
    };
    return {
      add(text) { buf += text; if (!raf) raf = requestAnimationFrame(step); },
      reset() { shown = ''; buf = ''; if (raf) cancelAnimationFrame(raf); raf = null; body.innerHTML = ''; },
      flush() { shown += buf; buf = ''; if (raf) cancelAnimationFrame(raf); raf = null; paint(); },
      async drain() {
        const started = Date.now();
        while (buf.length || raf) {
          if (document.hidden || Date.now() - started > 8000) { this.flush(); break; }
          await new Promise((r) => setTimeout(r, 50));
        }
      },
      get text() { return shown + buf; },
    };
  };

  // --- SSE parsing --------------------------------------------------------
  async function* frames(stream) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (let at = buffer.indexOf('\n\n'); at >= 0; at = buffer.indexOf('\n\n')) {
        const block = buffer.slice(0, at);
        buffer = buffer.slice(at + 2);
        let event = 'message';
        const data = [];
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trim());
        }
        if (data.length) yield { event, data: JSON.parse(data.join('\n')) };
      }
    }
  }

  const note = (text, limit = 100) => {
    const flat = text.split(/\s+/).join(' ').trim();
    return flat.length <= limit ? flat : `${flat.slice(0, limit - 3)}...`;
  };

  // --- Attachments: staged in memory, sent with the next turn ----------
  // A file input, a paste or a drop stages an image; the chips are drawn
  // from the staged list, and the stream request carries the bytes as
  // base64. They ride one turn only: cleared on success, kept on failure
  // so a retry still has them.
  const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
  const MAX_BYTES = 10 * 1024 * 1024;
  const MAX_FILES = 6;
  let staged = []; // {name, media_type, data_b64, url}
  const drawChips = () => {
    const box = document.getElementById('chat-attachments');
    if (!box) return;
    box.hidden = staged.length === 0;
    box.replaceChildren(...staged.map((file, i) => {
      const chip = clone('chat-chip');
      chip.dataset.chip = file.name;
      const img = chip.querySelector('img');
      img.src = file.url;
      img.dataset.viewImage = file.url;
      chip.querySelector('[data-name]').textContent = file.name;
      chip.querySelector('[data-remove]').addEventListener('click', () => { URL.revokeObjectURL(file.url); staged.splice(i, 1); drawChips(); });
      return chip;
    }));
    window.dispatchEvent(new CustomEvent('chat-staged', { detail: staged.length }));
  };
  // Vision models read at about a thousand pixels on the long edge; a
  // full-resolution screenshot only costs encode time (a 2214px paste took
  // two minutes on a local 30B model) and bytes on the wire. Anything
  // larger is scaled down before it is staged; animated GIFs are left
  // alone, since a canvas would keep one frame.
  const MAX_EDGE = 1568;
  const shrink = async (file) => {
    if (file.type === 'image/gif') return file;
    const bitmap = await createImageBitmap(file);
    const longest = Math.max(bitmap.width, bitmap.height);
    if (longest <= MAX_EDGE) { bitmap.close(); return file; }
    const scale = MAX_EDGE / longest;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const type = file.type === 'image/jpeg' ? 'image/jpeg' : 'image/png';
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, type, 0.9));
    return new File([blob], file.name, { type });
  };
  const stage = async (file) => {
    if (!IMAGE_TYPES.includes(file.type)) { toast(`${file.name || 'That'} is not an image.`, 'error'); return; }
    if (file.size > MAX_BYTES) { toast(`${file.name} is over 10 MB.`, 'error'); return; }
    if (staged.length >= MAX_FILES) { toast(`Up to ${MAX_FILES} images per message.`, 'error'); return; }
    let image = file;
    try { image = await shrink(file); } catch (_) { /* undecodable: send as pasted, the model will say so */ }
    const reader = new FileReader();
    reader.onload = () => {
      staged.push({
        name: file.name || 'image',
        media_type: image.type,
        data_b64: String(reader.result).split(',')[1],
        url: URL.createObjectURL(image),
      });
      drawChips();
    };
    reader.readAsDataURL(image);
  };
  document.addEventListener('change', (event) => {
    if (event.target.id !== 'chat-attach') return;
    [...event.target.files].forEach(stage);
    event.target.value = '';
  });
  document.addEventListener('paste', (event) => {
    if (!document.getElementById('chat-composer')) return;
    const files = [...(event.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) { event.preventDefault(); files.forEach(stage); }
  });
  document.addEventListener('drop', (event) => {
    if (!event.target.closest?.('#chat')) return;
    event.preventDefault();
    [...(event.dataTransfer?.files || [])].forEach(stage);
  });
  document.addEventListener('dragover', (event) => { if (event.target.closest?.('#chat')) event.preventDefault(); });
  // The composer's post carries only the names (for the note under the
  // bubble); the bytes go with the stream request.
  document.body.addEventListener('htmx:configRequest', (event) => {
    if (event.detail.elt.id !== 'chat-composer') return;
    event.detail.parameters.attachment_names = staged.map((f) => f.name);
  });

  // --- The turn -------------------------------------------------------------
  const setStreaming = (on) => {
    const form = document.getElementById('chat-composer');
    if (!form) return;
    form.querySelector('textarea').disabled = on;
    document.getElementById('chat-send').hidden = on;
    document.getElementById('chat-stop').hidden = !on;
    if (!on) form.querySelector('textarea').focus();
  };

  async function run(bubble) {
    const { stream, defaults, path } = config();
    const body = bubble.querySelector('[data-body]');
    const trail = bubble.querySelector('[data-trail]');
    const busy = bubble.querySelector('[data-busy]');
    const waiting = bubble.querySelector('[data-waiting]');
    // A pause names itself; anything arriving clears it. Silence that
    // looks identical to a hang is what this exists to end.
    const stillWaiting = (seconds, reason) => {
      if (!waiting) return;
      // The reason is the part that says it will finish: a model being
      // loaded is a wait with an end, and silence alone reads as a hang.
      waiting.textContent = reason ? `${reason} - ${seconds}s` : `waiting ${seconds}s`;
      waiting.hidden = false;
    };
    const answered = () => { if (waiting) waiting.hidden = true; };
    const writer = typewriter(body);
    const ids = { conversation: bubble.dataset.conversationId || null, message: null };
    const addTrail = (label) => {
      const li = document.createElement('li');
      li.textContent = label;
      trail.appendChild(li);
      trail.hidden = false;
    };
    const attachments = staged.map(({ name, media_type, data_b64 }) => ({ name, media_type, data_b64 }));
    controller = new AbortController();
    setStreaming(true);
    document.getElementById('chat-empty')?.remove();
    follow(true);
    let failed = null;
    try {
      const resp = await fetch(stream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({ ...defaults, message: bubble.dataset.text, conversation_id: ids.conversation, attachments }),
      });
      if (!resp.ok || !resp.body) throw new Error(`bad response ${resp.status}`);
      for await (const { event, data } of frames(resp.body)) {
        if (event === 'waiting') {
          stillWaiting(data.seconds ?? 0, data.reason);
        } else if (event === 'chunk') {
          answered();
          busy.hidden = true;
          if (data.conversation_id) ids.conversation = data.conversation_id;
          writer.add(data.content || '');
        } else if (event === 'tool') {
          // Fold the narration so far into the trail, then the call itself;
          // the answer is whatever follows the last tool call.
          const said = note(writer.text);
          if (said) addTrail(said);
          addTrail(data.label || 'working...');
          writer.reset();
          answered();
          busy.hidden = false;
        } else if (event === 'final') {
          ids.conversation = data.conversation_id || ids.conversation;
          ids.message = data.message_id;
        } else if (event === 'error') {
          failed = data.detail || data.error || 'Something went wrong answering that.';
        }
      }
      await writer.drain();
    } catch (e) {
      writer.flush();
      failed = e.name === 'AbortError' ? null : 'Something went wrong answering that.';
    } finally {
      controller = null;
      answered();
      busy.hidden = true;
      setStreaming(false);
      if (ids.conversation) {
        const hidden = document.getElementById('chat-conversation');
        if (hidden) hidden.value = ids.conversation;
      }
    }
    if (!failed && attachments.length) {
      // Sent: the bytes rode this turn. A failed turn keeps them staged
      // so a retry does not lose its images.
      for (const f of staged) URL.revokeObjectURL(f.url);
      staged = [];
      drawChips();
    }
    if (failed) {
      const p = document.createElement('p');
      p.className = 'text-sm text-error mt-2';
      p.textContent = failed;
      bubble.appendChild(p);
    } else if (ids.message && ids.conversation) {
      htmx.ajax('GET', `${path}/messages/${ids.conversation}/${ids.message}`, { target: bubble, swap: 'outerHTML' });
    }
    delete bubble.dataset.stream;
    follow();
  }

  // A streaming bubble the composer's post just appended starts its turn;
  // any other swap into the thread (a resumed or loaded conversation)
  // lands at its newest message.
  document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.detail.target.id !== 'chat-thread') return;
    watchGrowth();
    const bubble = thread().querySelector('[data-stream]');
    if (bubble && !controller) run(bubble);
    else follow(true);
  });
  document.body.addEventListener('htmx:load', (event) => {
    const elt = event.detail.elt;
    if (!(elt.querySelector?.('#chat-thread') || elt.id === 'chat-thread')) return;
    watchGrowth();
    follow(true);
  });

  // The Chat page arrives WITH the document rather than through a swap,
  // so the cold path has to arm itself; the drawer's surface comes
  // through htmx above. And `load` is the honest moment to land at the
  // bottom: everything the thread carries has finished taking up room by
  // then. Not forced - a reader who scrolled up while it loaded meant it.
  const arm = () => {
    if (!thread()) return;
    watchGrowth();
    follow(true);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', arm);
  } else {
    arm();
  }
  window.addEventListener('load', () => follow());
})();

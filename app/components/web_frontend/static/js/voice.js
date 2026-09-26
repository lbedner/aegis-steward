/* Talking to Illiana (island #3), beside the chat stream.

   A spoken turn is the typed turn. The microphone records until it is
   pressed again, the recording is transcribed into the composer and sent
   through chat.js like anything typed (straight away, for now).

   How she is heard is the server's, on the mic as data-voice
   (core/voice_settings.py): `reply` "live" speaks each sentence she streams
   as soon as it is complete, "answer" plays the settled answer; `sound`
   "typing" plays soft key taps while she works and has said nothing yet.
   How fast and how warmly she speaks is the speech model's (TTS_SPEED,
   TTS_INSTRUCTIONS), not the browser's. Any answer's Listen button plays
   it on demand.

   Nothing is recorded until the microphone is pressed - that is when the
   browser asks. The status line's wording lives in the surface partial's
   <template id="chat-mic-states">; the server's own refusals arrive as
   text. Audio is never kept. */
(() => {
  const LIMIT_MS = 2 * 60 * 1000; // a question, not a dictation
  const QUIET_MS = 8000; // typing stops this long after a turn with nothing to say
  let recorder = null;
  let speakReply = false; // the turn in flight is heard once it settles
  let live = false; // the turn in flight is heard as she writes it
  let pending = ''; // streamed text not yet a whole sentence
  const queue = []; // sentences fetched ahead, played in order
  let current = null;
  let player = null;
  let playing = null; // the Listen button whose audio is playing

  const mic = () => document.getElementById('chat-mic');
  const box = () => document.querySelector('#chat-composer textarea');
  const voice = () => JSON.parse(mic()?.dataset.voice || '{}');

  const say = (state, text) => {
    const status = document.getElementById('chat-mic-status');
    if (!status) return;
    const phrase = state && document.getElementById('chat-mic-states')
      ?.content.querySelector(`[data-state="${state}"]`);
    status.textContent = text ?? phrase?.textContent ?? '';
  };

  const pressed = (on) => mic()?.setAttribute('aria-pressed', String(on));
  // What a voice control is doing: idle, recording, thinking, speaking. The
  // page draws each state (the voice_states macro); this only sets it.
  const show = (control, state) => control?.setAttribute('data-state', state);
  // The mic's state, with the status line that reads it out.
  const mood = (state) => {
    show(mic(), state);
    if (state === 'thinking' || state === 'speaking') say(state);
    if (state === 'idle') say(null);
  };

  // --- Working: soft key taps until she speaks -------------------------------
  // Generated, not a recording: a short burst of filtered noise per key, at
  // the uneven pace of someone typing. Instead of narrating her tool calls,
  // which ran behind her own answer.
  let ear = null; // the AudioContext, made on the mic press that allows it
  let typing = null;
  let quiet = null;
  const tap = () => {
    const length = 0.018 + Math.random() * 0.02;
    const buffer = ear.createBuffer(1, Math.ceil(ear.sampleRate * length), ear.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) ** 3;
    const source = ear.createBufferSource();
    const tone = ear.createBiquadFilter();
    const level = ear.createGain();
    source.buffer = buffer;
    tone.type = 'bandpass';
    tone.frequency.value = 1800 + Math.random() * 1800;
    level.gain.value = 0.05 + Math.random() * 0.05;
    source.connect(tone).connect(level).connect(ear.destination);
    source.start();
  };
  const keepTyping = () => {
    tap();
    // Unhurried keys, and often the pause between words.
    typing = setTimeout(keepTyping, Math.random() < 0.25 ? 500 + Math.random() * 700 : 150 + Math.random() * 200);
  };
  const startTyping = () => {
    if (voice().sound !== 'typing' || typing || !ear) return;
    ear.resume();
    keepTyping();
  };
  const stopTyping = () => {
    clearTimeout(typing);
    clearTimeout(quiet);
    typing = null;
  };

  // The first sound of her voice ends the typing and shows her speaking -
  // on ``control`` (a speaker button) and, for her reply, on the mic.
  const voiced = (url, control = null, reply = true) => {
    const audio = new Audio(url);
    audio.addEventListener('playing', () => {
      stopTyping();
      show(control, 'speaking');
      if (reply) mood('speaking');
    });
    return audio;
  };

  // --- Hearing --------------------------------------------------------------
  const extension = (type) =>
    type.includes('mp4') ? 'mp4' : type.includes('ogg') ? 'ogg' : 'webm';

  // What was heard goes straight out, as if typed and sent. While a turn is
  // still streaming the box is locked, so it waits there to be sent.
  // The server names the agent that answers a spoken turn; it rides on the
  // composer as data-agent until chat.js sends the turn and clears it.
  const place = (text, agent) => {
    const input = box();
    if (!input) return;
    input.value = input.value.trim() ? `${input.value.trim()} ${text}` : text;
    input.dispatchEvent(new Event('input', { bubbles: true })); // Alpine's x-model
    input.form.dataset.agent = agent;
    say(null);
    if (input.disabled) return;
    input.form.requestSubmit();
  };

  const transcribe = async (blob, url) => {
    say('transcribing'); // an empty one too: the server says nothing was heard
    const body = new FormData();
    body.append('audio', blob, `speech.${extension(blob.type)}`);
    try {
      const answer = await fetch(url, { method: 'POST', body });
      const data = await answer.json().catch(() => ({}));
      if (answer.ok && data.text) return place(data.text, data.agent_slug);
      mic()?.setAttribute('data-state', 'idle');
      if (data.error) say(null, data.error);
      else say('offline');
    } catch (_) {
      mic()?.setAttribute('data-state', 'idle');
      say('offline');
    }
  };

  const start = async (button) => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return say('unsupported');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return say(e.name === 'NotAllowedError' ? 'denied' : 'unsupported');
    }
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener('dataavailable', (event) => { if (event.data.size) chunks.push(event.data); });
    recorder.addEventListener('stop', () => {
      for (const track of stream.getTracks()) track.stop();
      clearTimeout(recorder._limit);
      const type = recorder.mimeType || 'audio/webm';
      recorder = null;
      pressed(false);
      mic()?.setAttribute('data-state', 'thinking');
      transcribe(new Blob(chunks, { type }), button.dataset.transcripts);
    });
    recorder.start();
    recorder._limit = setTimeout(() => recorder?.stop(), LIMIT_MS);
    pressed(true);
    mic()?.setAttribute('data-state', 'recording');
    say('recording');
  };

  document.addEventListener('click', (event) => {
    const button = event.target.closest?.('#chat-mic');
    if (!button) return;
    // A browser only lets a page make sound after a press: this is that press.
    ear ??= new AudioContext();
    if (recorder) recorder.stop();
    else {
      hush();
      start(button);
    }
  });

  // --- Speaking ---------------------------------------------------------------
  const stop = () => {
    if (player?.reply) mood('idle');
    player?.pause();
    playing?.setAttribute('aria-pressed', 'false');
    show(playing, 'idle');
    player = null;
    playing = null;
  };

  // ``reply``: her answer to a spoken turn, so the mic shows it too; a
  // Listen or a preview shows only on its own button.
  const play = (button, reply = false) => {
    const again = playing === button;
    stop();
    if (again) return; // pressing a playing Listen button stops it
    // A form's preview speaks the form's current fields, unsaved.
    const form = 'speakForm' in button.dataset && button.closest('form');
    const query = form ? `?${new URLSearchParams(new FormData(form))}` : '';
    show(button, 'thinking'); // fetching and synthesizing
    player = voiced(`${button.dataset.speak}${query}`, button, reply);
    player.reply = reply;
    playing = button;
    button.setAttribute('aria-pressed', 'true');
    player.addEventListener('ended', stop);
    player.addEventListener('error', stop);
    player.play().catch(stop); // autoplay refused: the button still works
  };

  document.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-speak]');
    if (!button) return;
    hush();
    play(button);
  });

  // --- Live: each sentence as she writes it -----------------------------------
  // Fetched as soon as it is whole (the server synthesizes while the one
  // before plays) and played in order.
  const next = () => {
    current = queue.shift() || null;
    if (!current) {
      if (!live && !pending) mood('idle'); // she has finished
      return;
    }
    current.addEventListener('ended', next);
    current.addEventListener('error', next);
    current.play().catch(next);
  };
  const enqueue = (text) => {
    if (!text.trim()) return;
    const audio = voiced(`${mic().dataset.say}?text=${encodeURIComponent(text)}`);
    audio.preload = 'auto';
    queue.push(audio);
    if (!current) next();
  };
  // A sentence ends at . ! or ? before a space, or at a line break (a list
  // item or a heading has no full stop).
  const ENDS = /[.!?](?=\s)|\n/;
  const cut = () => {
    for (let at = pending.search(ENDS); at !== -1; at = pending.search(ENDS)) {
      enqueue(pending.slice(0, at + 1));
      pending = pending.slice(at + 1);
    }
  };
  const flush = () => {
    enqueue(pending);
    pending = '';
  };
  const hush = () => {
    live = false;
    pending = '';
    queue.length = 0;
    current?.pause();
    current = null;
    stopTyping();
    mood('idle');
  };
  document.addEventListener('chat:text', (event) => {
    if (!live) return;
    pending += event.detail;
    cut();
  });
  // Anything she wrote before a tool call is said before the tool runs.
  document.addEventListener('chat:tool', () => {
    if (live) flush();
  });
  document.addEventListener('chat:end', () => {
    if (live) flush();
    live = false;
    if (!current && !queue.length && !speakReply) mood('idle');
    // A turn that ends with nothing to say (failed, or silent) must not
    // leave the typing - or the spinner - running.
    clearTimeout(quiet);
    quiet = setTimeout(() => {
      stopTyping();
      if (!current && !player) mood('idle');
    }, QUIET_MS);
  });

  // A turn sent from a transcript is answered aloud, in the server's
  // chosen way; a typed one is not.
  document.body.addEventListener('htmx:configRequest', (event) => {
    const form = event.detail.elt;
    if (form.id !== 'chat-composer') return;
    const spoken = 'agent' in form.dataset;
    live = spoken && voice().reply === 'live';
    speakReply = spoken && !live;
    say(null);
    if (!spoken) return;
    mood('thinking');
    startTyping();
  });
  // The settled answer is swapped in over the streaming bubble (chat.js);
  // that is the moment it can be heard.
  document.body.addEventListener('htmx:load', (event) => {
    const button = speakReply && event.detail.elt.matches?.('[data-role=assistant]')
      && event.detail.elt.querySelector('[data-speak]');
    if (!button) return;
    speakReply = false;
    play(button, true);
  });
  // --- Talking live (issue 252): a call, not turns --------------------------------
  // GPT-Live hears and speaks over WebRTC; the server opens the session
  // (the key stays there). When it needs the household's books it delegates
  // to this page, which sends what was said to her agent and hands the
  // answer back to be said. Each answered turn lands in the conversation,
  // approval cards and all, so the thread reloads after each one.
  let call = null;
  const phone = () => document.getElementById('chat-live');
  const conversation = () => document.getElementById('chat-conversation');
  const hangUp = () => {
    if (!call) return;
    try { call.channel.send(JSON.stringify({ type: 'session.close' })); } catch (_) {}
    for (const track of call.stream.getTracks()) track.stop();
    call.peer.close();
    call.audio.srcObject = null;
    clearTimeout(call.quiet);
    clearTimeout(call.idle);
    call = null;
    stopTyping();
    show(phone(), 'idle');
    phone()?.setAttribute('aria-pressed', 'false');
    say(null);
  };
  // Dead air costs the same as talk: after the profile's seconds with
  // nobody speaking and nothing being worked on, hang up (0: never).
  const awake = () => {
    if (!call) return;
    clearTimeout(call.idle);
    const seconds = voice().idle;
    if (seconds > 0 && !call.working) call.idle = setTimeout(hangUp, seconds * 1000);
  };
  // Between her sentences the call is listening, or - while her agent is
  // pulling data - working, with the typing under it. GPT-Live says "let me
  // check" mid-delegation; when it stops, the work shows again.
  const rest = () => {
    if (!call) return;
    const working = call.working > 0;
    show(phone(), working ? 'working' : 'recording');
    say(working ? 'working' : 'live');
    if (working) startTyping();
    else stopTyping();
    awake();
  };
  // Something for her to say: a delegation's answer, or (``id`` null) her
  // greeting as the call opens.
  const answer = (id, content) => call.channel.send(JSON.stringify({
    type: 'session.commentary.append', event_id: `steward-${id ?? 'greeting'}`, delegation_id: id, content,
  }));
  const delegate = async (id) => {
    const text = call.heard.trim();
    call.heard = '';
    if (!text) return answer(id, phone().dataset.notHeard);
    call.working += 1;
    rest();
    let data = {};
    try {
      const answer = await fetch(phone().dataset.delegations, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, conversation_id: conversation()?.value || null }),
      });
      data = await answer.json();
    } catch (_) {}
    if (!call) return;
    call.working -= 1;
    rest();
    answer(id, data.speak || phone().dataset.sorry);
    if (data.conversation_id) {
      conversation().value = data.conversation_id;
      htmx.ajax('GET', phone().dataset.thread + data.conversation_id, { target: '#chat-thread', swap: 'innerHTML' });
    }
  };
  const heard = (event) => {
    if (event.type === 'session.started') answer(null, phone().dataset.greeting);
    else if (event.type === 'session.input_transcript.delta') {
      call.heard += event.delta;
      awake();
    } else if (event.type === 'session.output_transcript.delta') {
      stopTyping();
      clearTimeout(call.idle);
      show(phone(), 'speaking');
      call.said += event.delta;
      const done = call.said.includes(phone().dataset.signOff);
      clearTimeout(call.quiet);
      // ponytail: the transcript runs a little ahead of her audio, so a
      // goodbye hangs up on a longer pause; end_ms timing if it clips her.
      call.quiet = setTimeout(() => {
        if (!call) return;
        call.said = '';
        if (done) hangUp();
        else rest();
      }, done ? 2500 : 1200);
    } else if (event.type === 'session.delegation.created' && event.delegation?.target === 'client') {
      call.work = call.work.then(() => delegate(event.delegation.id));
    } else if (event.type === 'session.closed' || event.type === 'error') {
      if (event.type === 'error') console.warn('[live]', event);
      hangUp();
    }
  };
  const dial = async (button) => {
    if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) return say('unsupported');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return say(e.name === 'NotAllowedError' ? 'denied' : 'unsupported');
    }
    const peer = new RTCPeerConnection();
    const audio = new Audio();
    audio.autoplay = true;
    peer.ontrack = (event) => { audio.srcObject = event.streams[0]; };
    peer.addTrack(stream.getAudioTracks()[0], stream);
    const channel = peer.createDataChannel('oai-events');
    call = { peer, stream, audio, channel, heard: '', said: '', work: Promise.resolve(), working: 0, quiet: null, idle: null };
    channel.addEventListener('message', (message) => heard(JSON.parse(message.data)));
    show(button, 'thinking');
    button.setAttribute('aria-pressed', 'true');
    try {
      await peer.setLocalDescription(await peer.createOffer());
      const answer = await fetch(button.dataset.sessions, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: peer.localDescription.sdp, conversation_id: conversation()?.value || null }),
      });
      if (!answer.ok) throw new Error(`live session ${answer.status}`);
      await peer.setRemoteDescription({ type: 'answer', sdp: (await answer.json()).sdp });
    } catch (e) {
      console.warn('[live]', e);
      hangUp();
      return say('offline');
    }
    rest();
  };
  document.addEventListener('click', (event) => {
    const button = event.target.closest?.('#chat-live');
    if (!button) return;
    ear ??= new AudioContext();
    if (call) return hangUp();
    hush();
    dial(button);
  });
})();

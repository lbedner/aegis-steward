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
  const voice = () => JSON.parse(mic()?.dataset.voice || '{}');

  const phrase = (state) => document.getElementById('chat-mic-states')
    ?.content.querySelector(`[data-state="${state}"]`);
  const say = (state, text) => {
    const status = document.getElementById('chat-mic-status');
    if (!status) return;
    status.textContent = text ?? (state && phrase(state)?.textContent) ?? '';
  };

  const pressed = (on) => mic()?.setAttribute('aria-pressed', String(on));
  // What a voice control is doing: idle, recording, thinking, speaking. The
  // page draws each state (the voice_states macro); this only sets it.
  const show = (control, state) => {
    control?.setAttribute('data-state', state);
    // In a call the phone is out of sight; the call bar shows its state.
    if (control?.id === 'chat-live') callBar()?.setAttribute('data-state', state);
  };
  const callBar = () => document.getElementById('chat-call');
  // A control pressed and at work (a Listen button fetching, a call dialing).
  const engaged = (control) => {
    show(control, 'thinking');
    control.setAttribute('aria-pressed', 'true');
  };
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
    const input = composerBox();
    if (!input) return;
    setBoxText(input, input.value.trim() ? `${input.value.trim()} ${text}` : text);
    input.form.dataset.agent = agent;
    say(null);
    if (input.disabled) return;
    input.form.requestSubmit();
  };

  const transcribe = async (blob, url, seconds) => {
    say('transcribing'); // an empty one too: the server says nothing was heard
    const body = new FormData();
    body.append('audio', blob, `speech.${extension(blob.type)}`);
    // Transcription bills by the second (issue 270).
    if (seconds) body.append('seconds', seconds.toFixed(1));
    try {
      const answer = await fetch(url, { method: 'POST', body });
      const data = await answer.json().catch(() => ({}));
      if (answer.ok && data.text) return place(data.text, data.agent_slug);
      show(mic(), 'idle');
      if (data.error) say(null, data.error);
      else say('offline');
    } catch (_) {
      show(mic(), 'idle');
      say('offline');
    }
  };

  // The microphone, asked for once per use; null (and the reason said)
  // when the browser lacks what the use needs or the person said no.
  const microphone = async (needs) => {
    if (!navigator.mediaDevices?.getUserMedia || !needs) {
      say('unsupported');
      return null;
    }
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      say(e.name === 'NotAllowedError' ? 'denied' : 'unsupported');
      return null;
    }
  };

  const start = async (button) => {
    const stream = await microphone(window.MediaRecorder);
    if (!stream) return;
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener('dataavailable', (event) => { if (event.data.size) chunks.push(event.data); });
    recorder.addEventListener('stop', () => {
      for (const track of stream.getTracks()) track.stop();
      clearTimeout(recorder._limit);
      const type = recorder.mimeType || 'audio/webm';
      recorder = null;
      pressed(false);
      show(mic(), 'thinking');
      transcribe(new Blob(chunks, { type }), button.dataset.transcripts, (Date.now() - started) / 1000);
    });
    const started = Date.now();
    recorder.start();
    recorder._limit = setTimeout(() => recorder?.stop(), LIMIT_MS);
    pressed(true);
    show(mic(), 'recording');
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
    engaged(button); // fetching and synthesizing
    player = voiced(`${button.dataset.speak}${query}`, button, reply);
    player.reply = reply;
    playing = button;
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
    if (form !== chatComposer()) return;
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
  // How she opens: the server's line for this call, else the page's.
  const greeting = () => call.greeting || phone().dataset.greeting;
  // A call as it starts, whichever way it travels: nothing heard or said,
  // no work, quiet counted by the profile's seconds.
  const newCall = (how) => ({
    heard: '', said: '', work: Promise.resolve(),
    state: callState.start(voice().idle * 1000), ...how,
  });
  const phone = () => document.getElementById('chat-live');
  // Ending is two steps. Hanging up stops the mic and the sound at once and
  // asks OpenAI to close; the connection stays open a moment for its
  // session.closed, which carries the call's final billed seconds (issue
  // 270). ``finish`` tears down, on that event or after the wait.
  const CLOSE_WAIT_MS = 2500;
  // How far the transcript runs ahead of her audio, for a goodbye heard
  // only in what she said (GPT-Live).
  const GOODBYE_MS = 2500;
  // Her sign-off, heard in what she said: the call is over.
  const signsOff = (text) => (text || '').includes(phone().dataset.signOff);
  const finish = () => {
    if (!call) return;
    clearTimeout(call.closing);
    call.peer?.close();
    call = null;
  };
  const hangUp = () => {
    if (!call || call.closing) return;
    // Only GPT-Live has a close to ask for, and a final usage report to
    // wait on; a realtime call just ends (its usage is the server's).
    const live = call.transport === 'gpt_live';
    if (live) try { call.channel.send(JSON.stringify({ type: 'session.close' })); } catch (_) {}
    for (const track of call.stream.getTracks()) track.stop();
    if (call.audio) call.audio.srcObject = null;
    if (call.transport === 'relay') {
      call.ws.close();
      call.mic?.close();
      call.speaker?.close();
      // A turn saved as the call closed never reached this page.
      showSaved(call.conversation);
    }
    clearTimeout(call.audioEnd);
    clearTimeout(call.idle);
    clearTimeout(call.leave);
    clearTimeout(call.grace);
    call.closing = setTimeout(finish, live ? CLOSE_WAIT_MS : 0);
    stopTyping();
    stopBar();
    show(phone(), 'idle');
    phone()?.setAttribute('aria-pressed', 'false');
    say(null);
  };
  // --- The call bar (issue 273): the composer row gives way to it for the
  // call. Timer and running cost from the engine's per-second rate (the
  // server's); a token-billed engine is priced after the call.
  const field = (name) => callBar()?.querySelector(`[data-call-${name}]`);
  const showCost = (dollars) => { field('cost').textContent = `$${dollars.toFixed(2)}`; };
  const clock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const tick = () => {
    if (!call) return;
    const seconds = Math.max((Date.now() - call.started) / 1000, call.billed || 0);
    field('timer').textContent = clock(seconds);
    if (call.rate) showCost(seconds * call.rate);
    if (call.state.nudged) sayState(); // the countdown
  };
  const startBar = (engine) => {
    call.started = Date.now();
    call.rate = engine?.per_second || null;
    field('engine').textContent = engine?.label || '';
    field('cost').textContent = '';
    field('cost-later').hidden = Boolean(call.rate);
    chatComposer().hidden = true;
    callBar().hidden = false;
    call.ticker = setInterval(tick, 1000);
    tick();
  };
  const stopBar = () => {
    clearInterval(call?.ticker);
    muted(false);
    if (callBar()) callBar().hidden = true;
    if (chatComposer()) chatComposer().hidden = false;
  };
  // Mute: your voice stops going out (GPT-Live is told too).
  const muted = (on) => {
    document.getElementById('chat-mute')?.setAttribute('aria-pressed', String(on));
    if (!call) return;
    call.mutedAt = on ? Date.now() : null;
    for (const track of call.stream.getAudioTracks()) track.enabled = !on;
    if (call.transport === 'gpt_live' && !call.closing) {
      try {
        call.channel.send(JSON.stringify({ type: on ? 'session.input_audio.mute' : 'session.input_audio.unmute' }));
      } catch (_) {}
    }
    happen({ type: 'mute', on });
  };
  // What just happened on the call, put through its rules (call-state.js),
  // and what they say to do: the quiet clock, the goodbye's timers, a cue
  // for her, the hang-up. Every engine's events come through here.
  const arm = (name, ms) => {
    clearTimeout(call[name]);
    call[name] = ms == null ? null : setTimeout(() => happen({ type: name }), ms);
  };
  const happen = (event) => {
    if (!call || call.closing) return;
    const was = call.state;
    const out = callState.next(was, event);
    const now = out.state;
    call.state = now;
    if (out.quiet) {
      arm('idle', out.quiet === 'stop' ? null : now.quietMs);
      call.quietEnds = out.quiet === 'count' ? Date.now() + now.quietMs : null;
    }
    if (out.leave) arm('leave', out.leave === 'arm' ? callState.LEAVE_MS : null);
    if (out.grace) arm('grace', out.grace === 'arm' ? callState.GRACE_MS : null);
    if (out.cue) cue(out.cue);
    if (out.hangUp) return hangUp();
    // Redrawn on a change it shows: a step's own label too; the quiet's
    // re-arming only once it is a countdown.
    const changed = ['talking', 'working', 'muted', 'nudged'].some((key) => now[key] !== was[key]);
    if (changed || event.type === 'begin' || event.type === 'step' || (out.quiet && now.nudged)) draw();
  };
  // The call as it stands: her speaking, her working (the typing under
  // it - GPT-Live says "let me check" mid-delegation), or listening.
  const draw = () => {
    const { talking, working } = call.state;
    show(phone(), talking ? 'speaking' : working ? 'working' : 'recording');
    if (working && !talking) startTyping();
    else stopTyping();
    sayState();
  };
  // The status line: the quiet counted down to the hang-up once she has
  // checked in (a pause to think is no warning), muted, the step she is
  // running (as the relay names it and the trail will), or listening.
  const sayState = () => {
    const { nudged, muted, working } = call.state;
    const left = nudged && call.quietEnds && Math.ceil((call.quietEnds - Date.now()) / 1000);
    if (left > 0) {
      const line = phrase('hanging-up').cloneNode(true);
      line.querySelector('[data-countdown]').textContent = left;
      return say(null, line.textContent);
    }
    say(muted ? 'muted' : working ? 'working' : 'live', working ? call.step : null);
  };
  // A step she runs (the relay names it), and the end of her steps.
  const stepping = (step = null) => {
    call.step = step;
    happen({ type: 'step', on: true });
  };
  const stepped = () => {
    call.step = null;
    happen({ type: 'step', on: false });
  };
  // Her reply is done: no longer the cut one, and her steps with it.
  const replied = () => {
    happen({ type: 'reply' });
    stepped();
  };
  // Her audio as you hear it, done ``ms`` after the last of it.
  const herAudio = (ms) => {
    happen({ type: 'audio', on: true });
    clearTimeout(call.audioEnd);
    call.audioEnd = setTimeout(() => happen({ type: 'audio', on: false }), ms);
  };
  // Something for her to say: a delegation's answer, or (``id`` null) a
  // cue - her greeting, a check-in - each its own event.
  const answer = (id, content) => call.channel.send(JSON.stringify({
    type: 'session.commentary.append', event_id: `steward-${id ?? `cue-${Date.now()}`}`, delegation_id: id, content,
  }));
  // A cue for her to speak to (her greeting, a check-in), however the
  // call reaches her: a realtime response, GPT-Live commentary, or - on
  // the relay - the server's line by name.
  const tell = (text) => (call.transport === 'realtime'
    ? call.channel.send(JSON.stringify({ type: 'response.create', response: { instructions: text } }))
    : answer(null, text));
  const cue = (line) => (call.transport === 'relay'
    ? call.ws.send(JSON.stringify({ type: 'cue', line }))
    : tell(JSON.parse(phone().dataset.cues)[line]));
  const delegate = async (id) => {
    const asked = call;
    const text = call.heard.trim();
    call.heard = '';
    if (!text) return answer(id, phone().dataset.notHeard);
    stepping();
    let data = {};
    try {
      const answer = await fetch(phone().dataset.delegations, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, conversation_id: conversationId() }),
      });
      data = await answer.json();
    } catch (_) {}
    if (call !== asked) return; // that call is over
    stepped();
    answer(id, data.speak || phone().dataset.sorry);
    showTurns(data.conversation_id);
  };
  // Every live minute is metered (issue 270): OpenAI reports the call's
  // billed seconds as a running total, and the server's ledger keeps it.
  const bill = (seconds, reason = null) => {
    if (!call?.session || seconds == null) return;
    call.billed = seconds;
    fetch(phone().dataset.usage, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: call.session, seconds, reason }),
      keepalive: true,
    }).catch(() => {});
  };
  // The thread, reloaded so a saved turn shows with its steps and cards.
  const showTurns = (id) => {
    if (!id) return;
    setConversation(id);
    htmx.ajax('GET', phone().dataset.thread + id, { target: chatThread(), swap: 'innerHTML' });
  };
  // A realtime engine (issue 273): her own agent is the model, so the page
  // only follows along - greeting, speech, her tools at work, and the
  // thread once the server has saved the turn.
  const REALTIME_SAVE_MS = 1500;
  const showSaved = (id) => setTimeout(() => showTurns(id), REALTIME_SAVE_MS);
  const followRealtime = (event) => {
    if (event.type === 'session.created') {
      tell(greeting()); // the server's opening: a greeting, or picking up a dropped call
    } else if (event.type === 'input_audio_buffer.speech_started') {
      happen({ type: 'speech', on: true });
    } else if (event.type === 'input_audio_buffer.speech_stopped') {
      happen({ type: 'speech', on: false });
    } else if (event.type.startsWith('conversation.item.input_audio_transcription.')
      && (event.delta ?? event.transcript)?.trim()) {
      happen({ type: 'words' }); // as they are transcribed, or once done
    } else if (event.type === 'output_audio_buffer.started') {
      // Her audio as you hear it: the transcript runs well ahead of it.
      happen({ type: 'audio', on: true });
    } else if (event.type === 'output_audio_buffer.stopped') {
      happen({ type: 'audio', on: false });
    } else if (event.type === 'output_audio_buffer.cleared') {
      happen({ type: 'cut' });
    } else if (event.type === 'response.created') {
      happen({ type: 'reply' });
    } else if (event.type === 'response.output_item.added' && event.item?.type === 'function_call') {
      if (event.item.name === phone().dataset.endCall) happen({ type: 'goodbye' });
      else stepping();
    } else if (event.type === 'response.done') {
      replied();
      showSaved(call.conversation);
    } else if (event.type === 'error') {
      console.warn('[live]', event);
    }
  };
  const heard = (event) => {
    if (call.transport === 'realtime') return followRealtime(event);
    if (event.type === 'session.started') tell(greeting());
    else if (event.type === 'session.input_transcript.delta') {
      call.heard += event.delta;
      // No speech onset here: each piece of your words is speech.
      happen({ type: 'speech', on: true });
      happen({ type: 'words' });
      happen({ type: 'speech', on: false });
    } else if (event.type === 'session.output_transcript.delta') {
      if (!call.state.talking) call.said = ''; // a new reply of hers
      call.said += event.delta;
      const done = signsOff(call.said);
      // ponytail: end_ms timing if a goodbye's longer pause clips her.
      herAudio(done ? GOODBYE_MS : 1200);
      if (done) happen({ type: 'goodbye' });
    } else if (event.type === 'session.delegation.created' && event.delegation?.target === 'client') {
      call.work = call.work.then(() => delegate(event.delegation.id));
    } else if (event.type === 'session.usage.updated') {
      bill(event.usage?.seconds);
    } else if (event.type === 'session.closed' || event.type === 'error') {
      if (event.type === 'session.closed') bill(event.usage?.seconds, event.reason);
      if (event.type === 'error') console.warn('[live]', event);
      hangUp();
      if (event.type === 'session.closed') finish();
    }
  };
  const dial = async (button) => {
    // How to dial: WebRTC (GPT-Live, OpenAI realtime) or the relay (Gemini).
    let route = {};
    try {
      const asked = await fetch(button.dataset.engine);
      if (!asked.ok) throw new Error(`live engine ${asked.status}`);
      route = await asked.json();
    } catch (e) {
      console.warn('[live]', e);
      return say('offline');
    }
    if (route.transport === 'relay') return relay(button);
    const stream = await microphone(window.RTCPeerConnection);
    if (!stream) return;
    const peer = new RTCPeerConnection();
    const audio = new Audio();
    audio.autoplay = true;
    peer.ontrack = (event) => { audio.srcObject = event.streams[0]; };
    peer.addTrack(stream.getAudioTracks()[0], stream);
    const channel = peer.createDataChannel('oai-events');
    call = newCall({ peer, stream, audio, channel });
    channel.addEventListener('message', (message) => heard(JSON.parse(message.data)));
    engaged(button);
    try {
      await peer.setLocalDescription(await peer.createOffer());
      const answer = await fetch(button.dataset.sessions, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp: peer.localDescription.sdp, conversation_id: conversationId() }),
      });
      if (!answer.ok) throw new Error(`live session ${answer.status}`);
      const opened = await answer.json();
      call.session = opened.session_id;
      call.transport = opened.transport || 'gpt_live';
      call.greeting = opened.greeting;
      call.conversation = opened.conversation_id;
      setConversation(opened.conversation_id);
      await peer.setRemoteDescription({ type: 'answer', sdp: opened.sdp });
      call.engine = opened.engine;
    } catch (e) {
      console.warn('[live]', e);
      hangUp();
      return say('offline');
    }
    startBar(call.engine);
    happen({ type: 'begin' });
  };
  // --- The relay (issue 273): Gemini Live has no WebRTC, so the call's
  // audio goes through our server's WebSocket. The mic is captured at the
  // model's input rate (an AudioWorklet, mic-worklet.js) and sent as PCM16;
  // her voice comes back as PCM16 at its output rate and is scheduled
  // gap-free. The server says what the call is doing in small JSON events.
  const FRAME = 640; // samples a frame: 40ms at 16 kHz
  const MUTE_TAIL_MS = 1500; // past Gemini's 800ms end of speech
  const pcm16 = (samples) => {
    const out = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) out[i] = Math.max(-1, Math.min(1, samples[i])) * 0x7fff;
    return out.buffer;
  };
  const listen = async (rate) => {
    call.mic = new AudioContext({ sampleRate: rate });
    await call.mic.audioWorklet.addModule(phone().dataset.worklet);
    const node = new AudioWorkletNode(call.mic, 'mic-pcm');
    let batch = [];
    node.port.onmessage = ({ data }) => {
      if (!call || call.ws.readyState !== WebSocket.OPEN) return;
      // Muted, a moment of silence still goes, for Gemini to hear you stop
      // (or its turn for you never ends); then nothing - it bills audio in.
      if (call.mutedAt && Date.now() - call.mutedAt > MUTE_TAIL_MS) {
        batch = [];
        return;
      }
      batch.push(...data);
      if (batch.length < FRAME) return;
      call.ws.send(pcm16(batch));
      batch = [];
    };
    call.mic.createMediaStreamSource(call.stream).connect(node);
  };
  // Her voice, each chunk queued behind the last; the call shows her
  // speaking until the queue has played out.
  const voiceChunk = (data) => {
    if (!call?.speaker) return;
    const samples = new Int16Array(data);
    const buffer = call.speaker.createBuffer(1, samples.length, call.speaker.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;
    const node = call.speaker.createBufferSource();
    node.buffer = buffer;
    node.connect(call.speaker.destination);
    const at = Math.max(call.speaker.currentTime, call.playhead);
    node.start(at);
    call.playhead = at + buffer.duration;
    call.sources.push(node);
    node.onended = () => { if (call) call.sources = call.sources.filter((s) => s !== node); };
    herAudio((call.playhead - call.speaker.currentTime) * 1000 + 300);
  };
  const hush16 = () => {
    for (const node of call.sources) try { node.stop(); } catch (_) {}
    call.sources = [];
    call.playhead = 0;
    clearTimeout(call.audioEnd);
    happen({ type: 'cut' });
  };
  const relayed = async (event) => {
    if (!call) return;
    if (event.type === 'ready') {
      call.ready = true;
      call.conversation = event.conversation_id;
      setConversation(event.conversation_id);
      call.speaker = new AudioContext({ sampleRate: event.output_rate });
      try {
        await listen(event.input_rate);
      } catch (e) {
        console.warn('[live]', e);
        hangUp();
        return say('unsupported');
      }
      startBar(event.engine);
      happen({ type: 'begin' });
    } else if (event.type === 'interrupted') {
      hush16(); // she was spoken over: drop what was not yet heard
    } else if (event.type === 'hearing') {
      happen({ type: 'speech', on: true }); // your first transcribed words: you
      happen({ type: 'words' });
    } else if (event.type === 'heard') {
      happen({ type: 'speech', on: false });
    } else if (event.type === 'said') {
      if (signsOff(event.text)) happen({ type: 'goodbye' });
    } else if (event.type === 'hang_up') {
      happen({ type: 'goodbye' });
    } else if (event.type === 'working') {
      stepping(event.label);
    } else if (event.type === 'done') {
      replied();
    } else if (event.type === 'card') {
      // A chart up while she talks about it; the saved turn replaces it.
      const slot = clone('chat-live-card');
      chatThread().append(slot);
      htmx.ajax('GET', event.url, { target: slot, swap: 'innerHTML' });
    } else if (event.type === 'saved') {
      showTurns(call.conversation);
      // Token-billed: the server prices the call at each saved turn.
      if (event.cost != null) {
        showCost(event.cost);
        field('cost-later').hidden = true;
      }
    }
  };
  const relay = async (button) => {
    const stream = await microphone(window.AudioWorkletNode);
    if (!stream) return;
    const url = new URL(button.dataset.relay, location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    if (conversationId()) url.searchParams.set('conversation_id', conversationId());
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    call = newCall({ ws, stream, transport: 'relay', sources: [], playhead: 0 });
    engaged(button);
    ws.addEventListener('message', ({ data }) => (typeof data === 'string' ? relayed(JSON.parse(data)) : voiceChunk(data)));
    ws.addEventListener('close', () => {
      if (call?.ws !== ws || call.closing) return;
      const opened = call.ready;
      hangUp();
      if (!opened) say('offline');
    });
  };
  document.addEventListener('click', (event) => {
    if (event.target.closest?.('#chat-mute')) return call && muted(!call.state.muted);
    if (event.target.closest?.('#chat-hang-up')) return hangUp();
    const button = event.target.closest?.('#chat-live');
    if (!button) return;
    ear ??= new AudioContext();
    if (call) return hangUp();
    hush();
    dial(button);
  });
})();

/* A live call's rules (issue 458): what the call does next, given what
   just happened. Pure - no DOM, no clock - so every ordering a real call
   threw at it is a test (tests/js/call_state.test.js); voice.js maps each
   engine's events onto these and carries out what comes back.

   No provider ends a call on silence, and dead air costs the same as
   talk, so the quiet is counted here - only real quiet: you are not
   talking, her audio has played out, nothing is being worked on, you are
   not muted (muting is on purpose). After the profile's seconds she checks
   in; still quiet, she says goodbye; once it has played and you have said
   nothing, the call ends. Words of yours call any of it off.

   Each engine reports at its own delay - your words are transcribed after
   you spoke, her tool call comes after her audio, her audio plays after it
   is made - so the goodbye is judged against when it began, not against
   whichever event arrived last. */
(() => {
  // After her goodbye has played, a moment to say "wait" before it ends:
  // your first words are transcribed a beat after you start.
  const GRACE_MS = 2000;
  // A goodbye that never plays still ends the call.
  const LEAVE_MS = 10000;

  const start = (quietMs) => ({
    quietMs,
    hearing: false, // something like speech on the mic (a noise is too)
    talking: false, // her audio, as you hear it
    working: false, // a step of hers running
    muted: false,
    nudged: 0, // her check-ins into this quiet
    leaving: false, // her goodbye is under way
    played: false, // ... and has played out
    spoke: false, // ... and you started speaking since
    cut: false, // her reply was spoken over and is not yet done
  });

  const counting = (s) => s.quietMs > 0 && !(s.hearing || s.talking || s.working || s.muted || s.leaving);
  const over = (s) => s.leaving && s.played && !s.hearing;

  // One event in; the next state, and what to do:
  //   quiet  'count' arms the quiet clock (a countdown once she has checked
  //          in), 'fallback' arms it unseen (a check-in she never says
  //          still moves on), 'stop' stops it
  //   leave, grace  'arm' / 'stop' those timers
  //   cue    a line for her ('check_in', 'goodbye')
  //   hangUp the call ends
  const next = (was, event) => {
    const s = { ...was };
    const out = { state: s };
    const stay = () => {
      s.leaving = false;
      s.played = false;
    };
    switch (event.type) {
      case 'begin': // the call is up
        break;
      case 'speech':
        s.hearing = event.on;
        if (event.on) s.spoke = true;
        break;
      case 'words': // words of yours: the count starts over - and the goodbye
        // is off, unless they were said before it began (a late transcript)
        s.nudged = 0;
        if (!s.leaving || s.spoke) stay();
        break;
      case 'audio':
        s.talking = event.on;
        if (s.leaving) s.played = !event.on;
        break;
      case 'cut': // spoken over: no goodbye played out, and the goodbye is off
        s.talking = false;
        s.cut = true;
        stay();
        break;
      case 'reply': // a reply of hers begins or ends: no longer the cut one
        s.cut = false;
        break;
      case 'goodbye': // her end_call or sign-off - but a cut reply's "Talk soon." still comes in
        if (!s.leaving && !s.cut) {
          s.leaving = true;
          s.played = !s.talking; // a short "Bye." can have played before this came
          s.spoke = false;
        }
        break;
      case 'step':
        s.working = event.on;
        break;
      case 'mute':
        s.muted = event.on;
        if (event.on) s.hearing = false; // muted, you are not talking, whatever was last heard
        break;
      case 'idle': // the quiet clock ran out
        s.nudged += 1;
        if (s.nudged > 1) {
          s.leaving = true; // only cued: not played until she has said it
          s.spoke = false;
          out.cue = 'goodbye';
        } else {
          out.cue = 'check_in';
          out.quiet = 'fallback';
        }
        break;
      case 'leave': // not on you mid-sentence: words are transcribed after you stop
        if (s.hearing) out.leave = 'arm';
        else out.hangUp = true;
        return out;
      case 'grace':
        if (over(s)) out.hangUp = true;
        return out;
      default:
        return out;
    }
    if (s.leaving !== was.leaving) out.leave = s.leaving ? 'arm' : 'stop';
    if (counting(s) && (!counting(was) || event.type === 'begin' || event.type === 'words')) out.quiet = 'count';
    else if (!counting(s) && counting(was)) out.quiet = 'stop';
    if (over(s) !== over(was)) out.grace = over(s) ? 'arm' : 'stop';
    return out;
  };

  const callState = { start, next, GRACE_MS, LEAVE_MS };
  if (typeof module === 'object') module.exports = callState;
  else window.callState = callState;
})();

/* Appearance: theme x mode.

   Loaded synchronously in <head>, ahead of the stylesheet, so the stored
   choice is on <html> before the first paint: a light-mode user never sees
   a dark flash. `theme` (aegis, steward) is voice and shape; `mode` (dark,
   light, system) is the palette. The two resolve to one DaisyUI theme
   name, `<theme>-<mode>`, generated in tailwind.config.js. Charts listen
   for `theme-changed` and repaint from the new tokens. */
(() => {
  const root = document.documentElement;
  const CHOICES = { theme: ['aegis', 'steward'], mode: ['dark', 'light', 'system'] };
  const DEFAULTS = { theme: 'aegis', mode: 'dark' };
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  const read = (key) => {
    let stored = null;
    try {
      stored = localStorage.getItem(key);
    } catch (_) {
      /* private mode or storage disabled: keep the default */
    }
    return CHOICES[key].includes(stored) ? stored : DEFAULTS[key];
  };

  const apply = () => {
    const theme = read('theme');
    const mode = read('mode');
    const resolved = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
    root.dataset.theme = `${theme}-${resolved}`;
    document.dispatchEvent(new CustomEvent('theme-changed', { detail: { theme, mode } }));
  };

  /* The sidebar menu's Alpine state; what the user chose, not what resolved. */
  window.appearance = () => ({ theme: read('theme'), mode: read('mode') });
  window.setAppearance = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      /* not persisted; the switch still applies for this page */
    }
    apply();
  };

  media.addEventListener('change', apply);
  apply();
})();

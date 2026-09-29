/* Appearance: theme x mode x finish.

   Loaded synchronously in <head>, ahead of the stylesheet, so the stored
   choice is on <html> before the first paint: a light-mode user never sees
   a dark flash. `theme` (aegis, steward) is voice and shape; `mode`
   (dark, light, system) is the palette; `finish` (matte, lustre) is how
   surfaces are lit. The three resolve to one DaisyUI theme name,
   `<theme>-<mode>-<finish>`, generated in tailwind.config.js. Charts listen
   for `theme-changed` and repaint from the new tokens. */
(() => {
  const root = document.documentElement;
  // What each key may be, listed once on the server (rendering.py
  // APPEARANCE) and carried on <html>; the first value is the default.
  const CHOICES = JSON.parse(root.dataset.appearanceChoices);
  const DEFAULTS = Object.fromEntries(Object.entries(CHOICES).map(([key, values]) => [key, values[0]]));
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
    root.dataset.theme = `${theme}-${resolved}-${read('finish')}`;
    root.dataset.assistant = read('assistant');
    root.dataset.sidebar = read('sidebar');
    document.dispatchEvent(new CustomEvent('theme-changed', { detail: { theme, mode } }));
  };

  /* The sidebar menu's Alpine state; what the user chose, not what resolved. */
  window.appearance = () => Object.fromEntries(Object.keys(CHOICES).map((key) => [key, read(key)]));
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

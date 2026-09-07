/* htmx lifecycle hooks. */

// Re-execute inline scripts after an htmx swap.
//
// Load-bearing: browsers do not run <script> tags injected via innerHTML,
// which is exactly how htmx inserts a swapped fragment. Without this, an
// inline init script inside a partial silently never runs. Replacing each
// script node with a fresh one built via createElement makes the browser
// execute it.
document.body.addEventListener('htmx:afterSwap', (event) => {
  const scripts = event.detail.target.querySelectorAll('script');
  scripts.forEach((script) => {
    const newScript = document.createElement('script');
    newScript.textContent = script.textContent;
    script.parentNode.replaceChild(newScript, script);
  });
});

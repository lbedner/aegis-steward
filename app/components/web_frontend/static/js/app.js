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

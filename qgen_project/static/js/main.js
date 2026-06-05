// Main JS entrypoint — lightweight helpers only.
// HTMX and Alpine.js are loaded from CDN in base.html.

document.addEventListener('DOMContentLoaded', () => {
  // Auto-dismiss flash messages after 4 seconds
  document.querySelectorAll('.alert').forEach(el => {
    setTimeout(() => el.remove(), 4000);
  });
});

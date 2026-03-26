// Apply dark mode before first render to avoid flash
(function () {
  var stored = localStorage.getItem('theme');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (stored === 'dark' || (!stored && prefersDark)) {
    document.documentElement.classList.add('dark');
  }
})();

// Dark mode toggle button handler
document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('dark-toggle');
  if (!btn) return;
  btn.addEventListener('click', function () {
    var html = document.documentElement;
    if (html.classList.contains('dark')) {
      html.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    } else {
      html.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    }
  });
});

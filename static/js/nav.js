// Close <details data-menu> dropdowns on outside click or Escape.
document.addEventListener('click', function (event) {
  document.querySelectorAll('details[data-menu][open]').forEach(function (menu) {
    if (!menu.contains(event.target)) menu.removeAttribute('open');
  });
});

document.addEventListener('keydown', function (event) {
  if (event.key !== 'Escape') return;
  document.querySelectorAll('details[data-menu][open]').forEach(function (menu) {
    menu.removeAttribute('open');
    var summary = menu.querySelector('summary');
    if (summary) summary.focus();
  });
});

// Real-time password match validation on register/reset forms
document.addEventListener('DOMContentLoaded', function () {
  var pw      = document.querySelector('input[name="password"]');
  var confirm = document.querySelector('input[name="confirm_password"]');
  var errEl   = document.getElementById('pw-match-error');
  if (!pw || !confirm || !errEl) return;

  function check() {
    errEl.classList.toggle('hidden', !confirm.value || pw.value === confirm.value);
  }

  confirm.addEventListener('blur', check);
  confirm.addEventListener('input', check);
  pw.addEventListener('input', check);
});

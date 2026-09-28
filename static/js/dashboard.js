// Invoice detail page interactions
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  // Send email modal open
  var sendBtn = document.getElementById('btn-send-email');
  var sendModal = document.getElementById('send-modal');
  if (sendBtn && sendModal) {
    sendBtn.addEventListener('click', function () {
      sendModal.classList.remove('hidden');
      var emailInput = sendModal.querySelector('input[type="email"]');
      if (emailInput) emailInput.focus();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !sendModal.classList.contains('hidden')) {
        sendModal.classList.add('hidden');
        sendBtn.focus();
      }
    });
  }

  // Send email modal close
  var cancelBtn = document.getElementById('btn-send-cancel');
  if (cancelBtn && sendModal) {
    cancelBtn.addEventListener('click', function () {
      sendModal.classList.add('hidden');
    });
  }

  // Close modal on backdrop click
  if (sendModal) {
    sendModal.addEventListener('click', function (e) {
      if (e.target === sendModal) {
        sendModal.classList.add('hidden');
      }
    });
  }
});

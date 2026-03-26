// Invoice detail page interactions
document.addEventListener('DOMContentLoaded', function () {
  // Send email modal open
  var sendBtn = document.getElementById('btn-send-email');
  var sendModal = document.getElementById('send-modal');
  if (sendBtn && sendModal) {
    sendBtn.addEventListener('click', function () {
      sendModal.classList.remove('hidden');
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

// Sync color picker <-> hex input on branding page
document.addEventListener('DOMContentLoaded', function () {
  var picker   = document.querySelector('input[type="color"]');
  var hexInput = document.getElementById('accent_hex');
  if (!picker || !hexInput) return;

  picker.addEventListener('input', function () {
    hexInput.value = picker.value;
  });

  hexInput.addEventListener('input', function () {
    if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
      picker.value = hexInput.value;
    }
    picker.dispatchEvent(new Event('change'));
  });

  hexInput.addEventListener('change', function () {
    if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
      picker.value = hexInput.value;
    }
  });

  document.querySelector('form').addEventListener('submit', function () {
    if (/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) {
      picker.value = hexInput.value;
    }
  });
});

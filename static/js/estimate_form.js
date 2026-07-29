(function () {
  "use strict";

  const form = document.getElementById("estimate-form");
  if (!form) return;

  const items = document.getElementById("estimate-items");
  const template = document.getElementById("estimate-item-template");
  const currency = document.getElementById("estimate-currency");
  const total = document.getElementById("estimate-preview-total");

  function addItem(values) {
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector(".estimate-item");
    row.querySelector("[name='description[]']").value = values?.description || "";
    row.querySelector("[name='qty[]']").value = values?.qty || "1";
    row.querySelector("[name='rate[]']").value = values?.rate || "0";
    items.appendChild(fragment);
    refresh();
  }

  function refresh() {
    const option = currency.options[currency.selectedIndex];
    const symbol = option.dataset.symbol || "$";
    const digits = Number(option.dataset.digits || 2);
    const step = digits === 0 ? "1" : "0.01";
    let subtotal = 0;
    items.querySelectorAll(".estimate-item").forEach(function (row) {
      const qty = Number(row.querySelector(".estimate-qty").value) || 0;
      const rateField = row.querySelector(".estimate-rate");
      rateField.step = step;
      subtotal += qty * (Number(rateField.value) || 0);
    });
    const tax = Number(document.getElementById("estimate-tax").value) || 0;
    const discountField = document.getElementById("estimate-discount");
    discountField.step = step;
    const discount = Number(discountField.value) || 0;
    const amount = Math.max(0, subtotal + subtotal * tax / 100 - discount);
    total.textContent = symbol + amount.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  document.getElementById("add-estimate-item").addEventListener("click", function () {
    addItem();
  });
  items.addEventListener("click", function (event) {
    if (!event.target.classList.contains("remove-estimate-item")) return;
    const rows = items.querySelectorAll(".estimate-item");
    if (rows.length === 1) {
      rows[0].querySelectorAll("input").forEach(function (input) {
        input.value = input.name === "qty[]" ? "1" : "";
      });
    } else {
      event.target.closest(".estimate-item").remove();
    }
    refresh();
  });
  form.addEventListener("input", refresh);
  currency.addEventListener("change", refresh);

  const client = document.getElementById("estimate-client");
  if (client) {
    client.addEventListener("change", function () {
      const option = client.options[client.selectedIndex];
      if (!option.value) return;
      document.getElementById("estimate-to-name").value = option.dataset.name || "";
      document.getElementById("estimate-to-address").value = option.dataset.address || "";
      document.getElementById("estimate-to-email").value = option.dataset.email || "";
      if (option.dataset.tax) {
        document.getElementById("estimate-tax").value = option.dataset.tax;
      }
      refresh();
    });
  }

  const service = document.getElementById("estimate-service");
  if (service) {
    service.addEventListener("change", function () {
      const option = service.options[service.selectedIndex];
      if (!option.value) return;
      addItem({
        description: option.dataset.description,
        qty: option.dataset.qty,
        rate: option.dataset.rate
      });
      service.value = "";
    });
  }

  refresh();
})();

(function () {
  'use strict';

  var rowCounter = 0;

  function parseNum(val) {
    var n = parseFloat(val);
    return isNaN(n) ? 0 : n;
  }

  function currencySettings() {
    var select = document.getElementById('currency-code');
    var option = select && select.options[select.selectedIndex];
    return {
      symbol: option ? option.dataset.symbol : '$',
      minorUnits: option ? parseInt(option.dataset.minorUnits, 10) : 2,
      step: option ? option.dataset.step : '0.01'
    };
  }

  function fmt(n) {
    var currency = currencySettings();
    return currency.symbol + n.toFixed(currency.minorUnits)
      .replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function roundMoney(n) {
    var factor = Math.pow(10, currencySettings().minorUnits);
    return Math.round((n + Number.EPSILON) * factor) / factor;
  }

  function makeInput(type, name, placeholder, extraClasses) {
    var inp = document.createElement('input');
    inp.type = type;
    if (name) inp.name = name;
    if (placeholder) inp.placeholder = placeholder;
    inp.className = 'input-field' + (extraClasses ? ' ' + extraClasses : '');
    return inp;
  }

  function makeCell(colClass) {
    var div = document.createElement('div');
    div.className = colClass;
    return div;
  }

  // U2: update remove button state based on row count
  function updateRemoveBtns() {
    var rows = document.querySelectorAll('.line-item-row');
    var btns = document.querySelectorAll('.remove-btn');
    var onlyOne = rows.length <= 1;
    btns.forEach(function (btn) {
      if (onlyOne) {
        btn.setAttribute('title', 'At least one line item is required');
        btn.classList.add('opacity-40', 'cursor-not-allowed');
        btn.setAttribute('aria-disabled', 'true');
      } else {
        btn.removeAttribute('title');
        btn.classList.remove('opacity-40', 'cursor-not-allowed');
        btn.removeAttribute('aria-disabled');
      }
    });
  }

  function createRow(id) {
    var row = document.createElement('div');
    row.className = 'line-item-row grid grid-cols-12 gap-2 items-center';
    row.dataset.id = id;

    var descCell = makeCell('col-span-12 md:col-span-6');
    var descInput = makeInput('text', 'description[]', 'Service or product description', null);
    descCell.appendChild(descInput);

    var qtyCell = makeCell('col-span-4 md:col-span-2');
    var qtyInput = makeInput('number', 'qty[]', '1', null);
    qtyInput.min = '0';
    qtyInput.step = 'any';
    qtyCell.appendChild(qtyInput);

    var rateCell = makeCell('col-span-4 md:col-span-2');
    var rateWrap = document.createElement('div');
    rateWrap.className = 'relative';
    var ratePfx = document.createElement('span');
    ratePfx.className = 'absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 text-sm select-none pointer-events-none';
    ratePfx.classList.add('currency-prefix');
    ratePfx.textContent = currencySettings().symbol;
    var rateInput = makeInput('number', 'rate[]', '0.00', 'pl-6');
    rateInput.min = '0';
    rateInput.step = currencySettings().step;
    rateWrap.appendChild(ratePfx);
    rateWrap.appendChild(rateInput);
    rateCell.appendChild(rateWrap);

    var amtCell = makeCell('col-span-2 md:col-span-1 text-right');
    var amtSpan = document.createElement('span');
    amtSpan.className = 'amount-display text-sm font-mono text-gray-700 dark:text-gray-300';
    amtSpan.textContent = fmt(0);
    amtCell.appendChild(amtSpan);

    var rmCell = makeCell('col-span-2 md:col-span-1 flex justify-end');
    var rmBtn = document.createElement('button');
    rmBtn.type = 'button';
    rmBtn.className = 'remove-btn p-1.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors rounded';
    rmBtn.setAttribute('aria-label', 'Remove row');

    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'w-4 h-4');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('viewBox', '0 0 24 24');
    var path = document.createElementNS(svgNS, 'path');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('d', 'M6 18L18 6M6 6l12 12');
    svg.appendChild(path);
    rmBtn.appendChild(svg);
    rmCell.appendChild(rmBtn);

    row.appendChild(descCell);
    row.appendChild(qtyCell);
    row.appendChild(rateCell);
    row.appendChild(amtCell);
    row.appendChild(rmCell);

    function updateRowAmount() {
      var amount = roundMoney(parseNum(qtyInput.value) * parseNum(rateInput.value));
      amtSpan.textContent = fmt(amount);
      recalcTotals();
    }

    qtyInput.addEventListener('input', updateRowAmount);
    rateInput.addEventListener('input', updateRowAmount);

    // U2: block deletion of last row
    rmBtn.addEventListener('click', function () {
      var container = document.getElementById('line-items');
      var rows = container.querySelectorAll('.line-item-row');
      if (rows.length <= 1) {
        return; // blocked — tooltip already shown by updateRemoveBtns()
      }
      row.remove();
      recalcTotals();
      updateRemoveBtns();
    });

    return row;
  }

  function addRow() {
    rowCounter++;
    var row = createRow(rowCounter);
    document.getElementById('line-items').appendChild(row);
    // U2: re-enable all remove buttons now that there are 2+ rows
    updateRemoveBtns();
    row.querySelector('input[name="description[]"]').focus();
    return row;
  }

  function recalcTotals() {
    var subtotal = 0;
    document.querySelectorAll('.line-item-row').forEach(function (row) {
      var qty = parseNum(row.querySelector('input[name="qty[]"]').value);
      var rate = parseNum(row.querySelector('input[name="rate[]"]').value);
      subtotal += roundMoney(qty * rate);
    });

    var taxRate = parseNum(document.getElementById('tax-input').value);
    var discount = parseNum(document.getElementById('discount-input').value);
    var taxAmount = roundMoney(subtotal * (taxRate / 100));
    discount = Math.min(roundMoney(Math.max(0, discount)), subtotal + taxAmount);
    var total = subtotal + taxAmount - discount;

    document.getElementById('display-subtotal').textContent = fmt(subtotal);
    document.getElementById('display-tax').textContent = fmt(taxAmount);
    document.getElementById('display-discount').textContent = fmt(discount);
    document.getElementById('display-total').textContent = fmt(total);
  }

  // U1: spinner SVG factory
  function makeSpinner() {
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('class', 'animate-spin w-4 h-4 inline-block mr-2 align-middle');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('viewBox', '0 0 24 24');
    var circle = document.createElementNS(svgNS, 'circle');
    circle.setAttribute('class', 'opacity-25');
    circle.setAttribute('cx', '12');
    circle.setAttribute('cy', '12');
    circle.setAttribute('r', '10');
    circle.setAttribute('stroke', 'currentColor');
    circle.setAttribute('stroke-width', '4');
    var path = document.createElementNS(svgNS, 'path');
    path.setAttribute('class', 'opacity-75');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', 'M4 12a8 8 0 018-8v8z');
    svg.appendChild(circle);
    svg.appendChild(path);
    return svg;
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('invoice-form');
    if (!form) return;

    var catalog = { clients: [], services: [] };
    var catalogEl = document.getElementById('invoice-catalog-data');
    if (catalogEl) {
      try {
        catalog = JSON.parse(catalogEl.textContent);
      } catch (e) {
        catalog = { clients: [], services: [] };
      }
    }

    // U3: repopulate from server-side prefill data if present
    var prefillEl = document.getElementById('form-prefill');
    if (prefillEl) {
      try {
        var prefill = JSON.parse(prefillEl.textContent);
        var descriptions = prefill.descriptions || [];
        var qtys = prefill.qtys || [];
        var rates = prefill.rates || [];
        descriptions.forEach(function (desc, i) {
          var row = addRow();
          row.querySelector('input[name="description[]"]').value = desc;
          row.querySelector('input[name="qty[]"]').value = qtys[i] !== undefined ? qtys[i] : '';
          row.querySelector('input[name="rate[]"]').value = rates[i] !== undefined ? rates[i] : '';
        });
        if (descriptions.length === 0) addRow();
      } catch (e) {
        addRow();
      }
    } else {
      addRow();
    }

    recalcTotals();
    updateRemoveBtns();

    document.getElementById('add-row-btn').addEventListener('click', addRow);
    document.getElementById('tax-input').addEventListener('input', recalcTotals);
    document.getElementById('discount-input').addEventListener('input', recalcTotals);

    var clientSelect = document.getElementById('client-select');
    if (clientSelect) {
      clientSelect.addEventListener('change', function () {
        if (!clientSelect.value) return;
        var client = (catalog.clients || []).find(function (item) {
          return String(item.id) === clientSelect.value;
        });
        if (!client) return;

        form.querySelector('[name="to_name"]').value = client.name || '';
        form.querySelector('[name="to_address"]').value = client.address || '';
        form.querySelector('[name="to_email"]').value = client.email || '';
        document.getElementById('tax-input').value = client.tax_rate || '0';

        var invoiceDate = form.querySelector('[name="invoice_date"]').value;
        if (invoiceDate) {
          var due = new Date(invoiceDate + 'T00:00:00');
          due.setDate(due.getDate() + parseInt(client.payment_terms_days || 0, 10));
          var year = String(due.getFullYear());
          var month = String(due.getMonth() + 1).padStart(2, '0');
          var day = String(due.getDate()).padStart(2, '0');
          form.querySelector('[name="due_date"]').value = year + '-' + month + '-' + day;
        }
        recalcTotals();
      });
    }

    var serviceSelect = document.getElementById('service-item-select');
    var addServiceButton = document.getElementById('add-service-item-btn');
    if (serviceSelect && addServiceButton) {
      addServiceButton.addEventListener('click', function () {
        if (!serviceSelect.value) return;
        var service = (catalog.services || []).find(function (item) {
          return String(item.id) === serviceSelect.value;
        });
        if (!service) return;

        var rows = document.querySelectorAll('.line-item-row');
        var row = rows.length === 1 &&
          !rows[0].querySelector('input[name="description[]"]').value.trim()
          ? rows[0]
          : addRow();
        row.querySelector('input[name="description[]"]').value = service.description || service.name;
        row.querySelector('input[name="qty[]"]').value = service.quantity || '1';
        row.querySelector('input[name="rate[]"]').value = service.rate || '0';
        row.querySelector('input[name="rate[]"]').dispatchEvent(new Event('input'));
        serviceSelect.value = '';
      });
    }

    document.getElementById('currency-code').addEventListener('change', function () {
      var currency = currencySettings();
      document.querySelectorAll('.currency-prefix').forEach(function (prefix) {
        prefix.textContent = currency.symbol;
      });
      document.querySelectorAll('input[name="rate[]"]').forEach(function (input) {
        input.step = currency.step;
      });
      document.getElementById('discount-currency-symbol').textContent = currency.symbol;
      document.getElementById('discount-input').step = currency.step;
      recalcTotals();
    });

    var initialCurrency = currencySettings();
    document.querySelectorAll('.currency-prefix').forEach(function (prefix) {
      prefix.textContent = initialCurrency.symbol;
    });
    document.getElementById('discount-currency-symbol').textContent = initialCurrency.symbol;
    document.getElementById('discount-input').step = initialCurrency.step;

    var btnPreview  = document.getElementById('btn-preview');
    var btnDownload = document.getElementById('btn-download');
    var btnDraft    = document.getElementById('btn-save-draft');

    function setSpinner(btn) {
      btn.disabled = true;
      btn.classList.add('opacity-50', 'cursor-not-allowed');
      var spinner = makeSpinner();
      btn.insertBefore(spinner, btn.firstChild);
      btn.dataset.originalText = btn.textContent.trim();
      var textNode = btn.lastChild;
      if (textNode && textNode.nodeType === 3) {
        textNode.textContent = 'Generating\u2026';
      }
    }

    function resetBtn(btn) {
      btn.disabled = false;
      btn.classList.remove('opacity-50', 'cursor-not-allowed');
      var spinner = btn.querySelector('svg.animate-spin');
      if (spinner) spinner.remove();
      if (btn.dataset.originalText) {
        btn.textContent = btn.dataset.originalText;
        delete btn.dataset.originalText;
      }
    }

    function handlePdfButton(e, btn) {
      e.preventDefault();

      var descriptions = form.querySelectorAll('input[name="description[]"]');
      var hasItem = false;
      descriptions.forEach(function (inp) {
        if (inp.value.trim()) hasItem = true;
      });
      if (!hasItem) {
        alert('Please add at least one line item with a description.');
        return;
      }

      setSpinner(btn);

      var formData = new FormData(form);
      formData.set('action', btn.value);

      fetch(form.getAttribute('action'), { method: 'POST', body: formData })
        .then(function (response) {
          if (!response.ok) throw new Error('Server error');
          // Extract filename from Content-Disposition header
          var cd = response.headers.get('Content-Disposition') || '';
          var match = cd.match(/filename="([^"]+)"/);
          var filename = match ? match[1] : 'invoice.pdf';
          return response.blob().then(function (blob) {
            return { blob: blob, filename: filename };
          });
        })
        .then(function (result) {
          var url = URL.createObjectURL(result.blob);
          if (btn.value === 'preview') {
            window.open(url, '_blank');
          } else {
            var a = document.createElement('a');
            a.href = url;
            a.download = result.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          }
          setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
        })
        .catch(function () {
          alert('Failed to generate PDF. Please try again.');
        })
        .finally(function () {
          resetBtn(btn);
        });
    }

    if (btnPreview)  btnPreview.addEventListener('click',  function (e) { handlePdfButton(e, btnPreview); });
    if (btnDownload) btnDownload.addEventListener('click', function (e) { handlePdfButton(e, btnDownload); });

    // U1: spinner on submit (Save Draft only — preview/download handled above)
    form.addEventListener('submit', function (e) {
      var clicked = e.submitter;
      if (!clicked || clicked.id === 'btn-save-draft') return;
      // preview/download are handled by click handlers above; prevent double-submit
      if (clicked.id === 'btn-preview' || clicked.id === 'btn-download') {
        e.preventDefault();
      }
    });

    // U5: upgrade tooltip on locked theme links
    document.querySelectorAll('a[href*="upgrade"]').forEach(function (link) {
      if (link.closest('.rounded-xl') || link.closest('[class*="rounded"]')) {
        link.setAttribute('title', 'Upgrade to Pro to unlock this template');
      }
    });
  });
})();

// Theme card highlight
(function () {
  document.addEventListener('DOMContentLoaded', function () {
    var cards = document.querySelectorAll('.theme-card');
    function updateHighlight() {
      cards.forEach(function (label) {
        var radio = label.querySelector('input[type="radio"]');
        var inner = label.querySelector('.theme-card-inner');
        if (radio && inner) {
          if (radio.checked) {
            inner.classList.add('border-blue-600', 'dark:border-blue-400');
            inner.classList.remove('border-transparent');
          } else {
            inner.classList.remove('border-blue-600', 'dark:border-blue-400');
            inner.classList.add('border-transparent');
          }
        }
      });
    }
    cards.forEach(function (label) {
      var radio = label.querySelector('input[type="radio"]');
      if (radio) radio.addEventListener('change', updateHighlight);
    });
    updateHighlight();
  });
})();

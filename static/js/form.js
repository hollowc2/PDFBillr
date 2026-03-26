(function () {
  'use strict';

  var rowCounter = 0;

  function parseNum(val) {
    var n = parseFloat(val);
    return isNaN(n) ? 0 : n;
  }

  function fmt(n) {
    return '$' + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
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
    ratePfx.textContent = '$';
    var rateInput = makeInput('number', 'rate[]', '0.00', 'pl-6');
    rateInput.min = '0';
    rateInput.step = '0.01';
    rateWrap.appendChild(ratePfx);
    rateWrap.appendChild(rateInput);
    rateCell.appendChild(rateWrap);

    var amtCell = makeCell('col-span-2 md:col-span-1 text-right');
    var amtSpan = document.createElement('span');
    amtSpan.className = 'amount-display text-sm font-mono text-gray-700 dark:text-gray-300';
    amtSpan.textContent = '$0.00';
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
      var amount = parseNum(qtyInput.value) * parseNum(rateInput.value);
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
  }

  function recalcTotals() {
    var subtotal = 0;
    document.querySelectorAll('.line-item-row').forEach(function (row) {
      var qty = parseNum(row.querySelector('input[name="qty[]"]').value);
      var rate = parseNum(row.querySelector('input[name="rate[]"]').value);
      subtotal += qty * rate;
    });

    var taxRate = parseNum(document.getElementById('tax-input').value);
    var discount = parseNum(document.getElementById('discount-input').value);
    var taxAmount = subtotal * (taxRate / 100);
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

    // U3: repopulate from server-side prefill data if present
    var prefillEl = document.getElementById('form-prefill');
    if (prefillEl) {
      try {
        var prefill = JSON.parse(prefillEl.textContent);
        var descriptions = prefill.descriptions || [];
        var qtys = prefill.qtys || [];
        var rates = prefill.rates || [];
        descriptions.forEach(function (desc, i) {
          if (i > 0) addRow();
          var rows = document.querySelectorAll('.line-item-row');
          var row = rows[rows.length - 1];
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

    var btnPreview  = document.getElementById('btn-preview');
    var btnDownload = document.getElementById('btn-download');
    var btnDraft    = document.getElementById('btn-save-draft');

    // U1: spinner on submit
    form.addEventListener('submit', function (e) {
      var descriptions = form.querySelectorAll('input[name="description[]"]');
      var hasItem = false;
      descriptions.forEach(function (inp) {
        if (inp.value.trim()) hasItem = true;
      });

      if (!hasItem) {
        e.preventDefault();
        alert('Please add at least one line item with a description.');
        return;
      }

      var clicked = e.submitter;
      // Don't spinner the Save Draft button (it goes to a different URL)
      if (clicked && clicked.id !== 'btn-save-draft') {
        clicked.disabled = true;
        clicked.classList.add('opacity-50', 'cursor-not-allowed');
        var spinner = makeSpinner();
        clicked.insertBefore(spinner, clicked.firstChild);
        clicked.dataset.originalText = clicked.textContent.trim();
        // Update text node (last child after spinner insertion)
        var textNode = clicked.lastChild;
        if (textNode && textNode.nodeType === 3) {
          textNode.textContent = 'Generating\u2026';
        }
      }
    });

    // U1: re-enable on back navigation (pageshow)
    window.addEventListener('pageshow', function () {
      [btnPreview, btnDownload].forEach(function (btn) {
        if (!btn) return;
        btn.disabled = false;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
        var spinner = btn.querySelector('svg.animate-spin');
        if (spinner) spinner.remove();
        if (btn.dataset.originalText) {
          btn.textContent = btn.dataset.originalText;
          delete btn.dataset.originalText;
        }
      });
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

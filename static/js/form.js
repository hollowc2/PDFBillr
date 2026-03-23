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
    inp.className = 'w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none transition-colors' + (extraClasses ? ' ' + extraClasses : '');
    return inp;
  }

  function makeCell(colClass) {
    var div = document.createElement('div');
    div.className = colClass;
    return div;
  }

  function createRow(id) {
    var row = document.createElement('div');
    row.className = 'line-item-row grid grid-cols-12 gap-2 items-center';
    row.dataset.id = id;

    // Description (col-span-6 on md)
    var descCell = makeCell('col-span-12 md:col-span-6');
    var descInput = makeInput('text', 'description[]', 'Service or product description', null);
    descCell.appendChild(descInput);

    // Qty
    var qtyCell = makeCell('col-span-4 md:col-span-2');
    var qtyInput = makeInput('number', 'qty[]', '1', null);
    qtyInput.min = '0';
    qtyInput.step = 'any';
    qtyCell.appendChild(qtyInput);

    // Rate (with $ prefix wrapper)
    var rateCell = makeCell('col-span-4 md:col-span-2');
    var rateWrap = document.createElement('div');
    rateWrap.className = 'relative';
    var ratePfx = document.createElement('span');
    ratePfx.className = 'absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 text-sm select-none';
    ratePfx.textContent = '$';
    var rateInput = makeInput('number', 'rate[]', '0.00', 'pl-6');
    rateInput.min = '0';
    rateInput.step = '0.01';
    rateWrap.appendChild(ratePfx);
    rateWrap.appendChild(rateInput);
    rateCell.appendChild(rateWrap);

    // Amount display
    var amtCell = makeCell('col-span-2 md:col-span-1 text-right');
    var amtSpan = document.createElement('span');
    amtSpan.className = 'amount-display text-sm font-mono text-gray-700 dark:text-gray-300';
    amtSpan.textContent = '$0.00';
    amtCell.appendChild(amtSpan);

    // Remove button
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

    rmBtn.addEventListener('click', function () {
      var container = document.getElementById('line-items');
      var rows = container.querySelectorAll('.line-item-row');
      if (rows.length <= 1) {
        descInput.value = '';
        qtyInput.value = '';
        rateInput.value = '';
        amtSpan.textContent = '$0.00';
        recalcTotals();
      } else {
        row.remove();
        recalcTotals();
      }
    });

    return row;
  }

  function addRow() {
    rowCounter++;
    var row = createRow(rowCounter);
    document.getElementById('line-items').appendChild(row);
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

  document.addEventListener('DOMContentLoaded', function () {
    addRow();

    document.getElementById('add-row-btn').addEventListener('click', addRow);
    document.getElementById('tax-input').addEventListener('input', recalcTotals);
    document.getElementById('discount-input').addEventListener('input', recalcTotals);

    var form = document.getElementById('invoice-form');
    var btnPreview = document.getElementById('btn-preview');
    var btnDownload = document.getElementById('btn-download');

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

      btnPreview.disabled = true;
      btnDownload.disabled = true;
      btnPreview.textContent = 'Generating\u2026';
      btnDownload.textContent = 'Generating\u2026';
      btnPreview.classList.add('opacity-50', 'cursor-not-allowed');
      btnDownload.classList.add('opacity-50', 'cursor-not-allowed');

      setTimeout(function () {
        btnPreview.disabled = false;
        btnDownload.disabled = false;
        btnPreview.textContent = 'Preview PDF';
        btnDownload.textContent = 'Download PDF';
        btnPreview.classList.remove('opacity-50', 'cursor-not-allowed');
        btnDownload.classList.remove('opacity-50', 'cursor-not-allowed');
      }, 10000);
    });
  });
})();

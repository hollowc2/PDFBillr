// Line item editor for recurring invoice form
(function () {
  var container = document.getElementById('line-items-container');
  var hiddenInput = document.getElementById('line_items_json');
  var items = [];

  try { items = JSON.parse(hiddenInput.value || '[]'); } catch(e) { items = []; }

  function renderItems() {
    container.innerHTML = '';
    items.forEach(function(item, i) {
      var row = document.createElement('div');
      row.className = 'flex gap-2 items-start';
      row.innerHTML =
        '<input type="text" placeholder="Description" value="' + esc(item.description) + '" class="input-field flex-1" data-i="' + i + '" data-f="description" />' +
        '<input type="number" placeholder="Qty" value="' + item.qty + '" min="0" step="0.01" class="input-field w-20" data-i="' + i + '" data-f="qty" />' +
        '<input type="number" placeholder="Rate" value="' + item.rate + '" min="0" step="0.01" class="input-field w-24" data-i="' + i + '" data-f="rate" />' +
        '<button type="button" class="mt-1 text-red-400 hover:text-red-600 dark:hover:text-red-300 px-1 text-lg leading-none" data-remove="' + i + '">&times;</button>';
      container.appendChild(row);
    });
  }

  function esc(s) { return (s||'').replace(/"/g,'&quot;'); }

  function sync() {
    hiddenInput.value = JSON.stringify(items);
  }

  container.addEventListener('input', function(e) {
    var el = e.target;
    var i = el.dataset.i;
    var f = el.dataset.f;
    if (i === undefined || !f) return;
    i = parseInt(i);
    if (f === 'description') items[i].description = el.value;
    else if (f === 'qty') { items[i].qty = parseFloat(el.value) || 0; items[i].amount = items[i].qty * items[i].rate; }
    else if (f === 'rate') { items[i].rate = parseFloat(el.value) || 0; items[i].amount = items[i].qty * items[i].rate; }
    sync();
  });

  container.addEventListener('click', function(e) {
    var ri = e.target.dataset.remove;
    if (ri !== undefined) { items.splice(parseInt(ri), 1); renderItems(); sync(); }
  });

  document.getElementById('add-line-item').addEventListener('click', function() {
    items.push({description: '', qty: 1, rate: 0, amount: 0});
    renderItems();
    sync();
    var inputs = container.querySelectorAll('input[data-f="description"]');
    if (inputs.length) inputs[inputs.length-1].focus();
  });

  renderItems();
  sync();
})();

// Line item editor for recurring invoice form
(function () {
  var container = document.getElementById('line-items-container');
  var hiddenInput = document.getElementById('line_items_json');
  var items = [];

  try { items = JSON.parse(hiddenInput.value || '[]'); } catch(e) { items = []; }

  function currencySettings() {
    var select = document.getElementById('currency-code');
    var option = select && select.options[select.selectedIndex];
    return {
      code: option ? option.value : 'USD',
      step: option ? option.dataset.step : '0.01'
    };
  }

  function renderItems() {
    container.innerHTML = '';
    items.forEach(function(item, i) {
      var row = document.createElement('div');
      row.className = 'flex gap-2 items-start';
      row.appendChild(makeInput('text', 'Description', item.description || '', i, 'description', 'input-field flex-1'));
      row.appendChild(makeInput('number', 'Qty', item.qty, i, 'qty', 'input-field w-20'));
      row.appendChild(makeInput('number', 'Rate', item.rate, i, 'rate', 'input-field w-24'));

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'mt-1 text-red-400 hover:text-red-600 dark:hover:text-red-300 px-1 text-lg leading-none';
      remove.dataset.remove = i;
      remove.textContent = '\u00d7';
      row.appendChild(remove);
      container.appendChild(row);
    });
  }

  function makeInput(type, placeholder, value, index, field, classes) {
    var input = document.createElement('input');
    input.type = type;
    input.placeholder = placeholder;
    input.value = value === undefined || value === null ? '' : String(value);
    input.dataset.i = index;
    input.dataset.f = field;
    input.className = classes;
    if (type === 'number') {
      input.min = '0';
      input.step = field === 'rate' ? currencySettings().step : 'any';
    }
    return input;
  }

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

  document.getElementById('currency-code').addEventListener('change', function () {
    document.getElementById('discount-currency-code').textContent =
      currencySettings().code;
    document.getElementById('discount-input').step = currencySettings().step;
    container.querySelectorAll('input[data-f="rate"]').forEach(function (input) {
      input.step = currencySettings().step;
    });
  });

  renderItems();
  sync();
})();

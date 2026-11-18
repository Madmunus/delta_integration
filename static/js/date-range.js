(function () {
  var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  var DATE_RE = /\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2}/g;
  var openPop = null;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function toIso(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function fmtRu(d) {
    return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear();
  }
  function startOfDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }
  // Программная замена значения должна выглядеть для других модулей так же,
  // как ввод пользователя: сохранённые фильтры реагируют на событие input.
  function notifyInput(input) {
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function parseDate(token) {
    var m = token.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    m = token.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
    if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
    return null;
  }
  function parseValue(value) {
    var tokens = (value || '').match(DATE_RE) || [];
    var start = tokens.length ? parseDate(tokens[0]) : null;
    var end = tokens.length > 1 ? parseDate(tokens[tokens.length - 1]) : start;
    if (start && end && start > end) {
      var tmp = start; start = end; end = tmp;
    }
    return { start: start, end: end };
  }

  function buildPopover(group) {
    var pop = document.createElement('div');
    pop.className = 'delta-range-popover';
    pop.hidden = true;
    pop.innerHTML =
      '<div class="delta-range-head">' +
        '<button type="button" class="btn btn-sm btn-outline-secondary"' +
        ' data-nav="-1" aria-label="Предыдущий месяц">&#8249;</button>' +
        '<select class="form-select form-select-sm delta-range-select' +
        ' delta-range-month" aria-label="Месяц">' +
        MONTHS.map(function (name, index) {
          return '<option value="' + index + '">' + name + '</option>';
        }).join('') +
        '</select>' +
        '<select class="form-select form-select-sm delta-range-select' +
        ' delta-range-year" aria-label="Год"></select>' +
        '<button type="button" class="btn btn-sm btn-outline-secondary"' +
        ' data-nav="1" aria-label="Следующий месяц">&#8250;</button>' +
      '</div>' +
      '<div class="delta-range-weekdays">' +
        WEEKDAYS.map(function (w) { return '<span>' + w + '</span>'; }).join('') +
      '</div>' +
      '<div class="delta-range-grid" role="grid"></div>' +
      '<div class="delta-range-foot">' +
        '<span class="delta-range-hint">Выберите начало и конец</span>' +
        '<button type="button" class="btn btn-sm btn-outline-secondary"' +
        ' data-clear>Сбросить</button>' +
      '</div>';
    // Поповер живёт рядом с группой, а не внутри неё: иначе кнопка очистки
    // перестаёт быть последним элементом .input-group и Bootstrap снимает
    // у неё правые скругления.
    group.parentNode.appendChild(pop);
    return pop;
  }

  // Выравниваем поповер по левому краю поля ввода: у колонки есть отступ
  // сетки, поэтому left: 0 дал бы сдвиг влево.
  function alignPopover(group, pop) {
    pop.style.left = group.offsetLeft + 'px';
  }

  // Года в селекторе обновляются вокруг текущего/выбранного года:
  // стрелки могут перейти за границы исходного списка.
  function ensureYears(pop, year) {
    var select = pop.querySelector('.delta-range-year');
    var selected = String(year);
    var option = select.querySelector('option[value="' + selected + '"]');
    if (option) return;
    var from = year - 20;
    var to = year + 10;
    if (select.options.length) {
      from = Math.min(from, Number(select.options[select.options.length - 1].value));
      to = Math.max(to, Number(select.options[0].value));
    }
    var html = '';
    for (var y = to; y >= from; y--) {
      html += '<option value="' + y + '">' + y + '</option>';
    }
    select.innerHTML = html;
  }

  function paint(pop) {
    var st = pop._delta;
    ensureYears(pop, st.month.getFullYear());
    pop.querySelector('.delta-range-month').value =
      String(st.month.getMonth());
    pop.querySelector('.delta-range-year').value =
      String(st.month.getFullYear());
    var grid = pop.querySelector('.delta-range-grid');
    grid.innerHTML = '';
    var year = st.month.getFullYear();
    var month = st.month.getMonth();
    var first = new Date(year, month, 1);
    var offset = (first.getDay() + 6) % 7;  // неделя с понедельника
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var today = startOfDay(new Date());
    var end = st.end || (st.start ? st.hover : null);
    var i;
    for (i = 0; i < offset; i++) {
      var blank = document.createElement('span');
      blank.className = 'delta-range-empty';
      grid.appendChild(blank);
    }
    for (var day = 1; day <= daysInMonth; day++) {
      var date = new Date(year, month, day);
      var cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'delta-range-day';
      cell.textContent = day;
      cell.dataset.date = toIso(date);
      if (today.getTime() === date.getTime()) cell.classList.add('is-today');
      if (st.start && st.start.getTime() === date.getTime()) {
        cell.classList.add('is-start');
      }
      if (end && end.getTime() === date.getTime()) cell.classList.add('is-end');
      if (st.start && end && date > st.start && date < end) {
        cell.classList.add('is-preview');
      }
      grid.appendChild(cell);
    }
  }

  /* Наведение мыши меняет только подсветку диапазона: заново строить
     сетку календаря на каждом mouseover дорого и заметно дёргает UI. */
  function updatePreview(pop, hover) {
    var st = pop._delta;
    var cells = pop.querySelectorAll('.delta-range-day');
    cells.forEach(function (cell) { cell.classList.remove('is-preview'); });
    if (!st.start || st.end || !hover) return;
    var from = st.start < hover ? st.start : hover;
    var to = st.start < hover ? hover : st.start;
    cells.forEach(function (cell) {
      var day = parseDate(cell.dataset.date);
      if (day && from < day && day < to) cell.classList.add('is-preview');
    });
  }

  function closePopover() {
    if (!openPop) return;
    openPop.hidden = true;
    openPop = null;
    document.removeEventListener('mousedown', onDocDown);
    document.removeEventListener('keydown', onKeyDown);
  }

  function onDocDown(ev) {
    if (openPop && !openPop.parentNode.contains(ev.target)) closePopover();
  }

  function onKeyDown(ev) {
    if (ev.key === 'Escape') closePopover();
  }

  function commit(pop, input) {
    var st = pop._delta;
    input.value = fmtRu(st.start) + ' — ' + fmtRu(st.end);
    notifyInput(input);
    closePopover();
    // Возвращаем фокус в поле, но не открываем календарь снова.
    input._deltaNoOpen = true;
    input.focus();
  }

  function bindPopover(pop, input) {
    pop.addEventListener('change', function (ev) {
      if (!ev.target.matches('.delta-range-month, .delta-range-year')) return;
      var st = pop._delta;
      var month = Number(pop.querySelector('.delta-range-month').value);
      var year = Number(pop.querySelector('.delta-range-year').value);
      if (isNaN(month) || isNaN(year)) return;
      st.month = new Date(year, month, 1);
      st.hover = null;
      paint(pop);
    });
    pop.addEventListener('click', function (ev) {
      var st = pop._delta;
      var nav = ev.target.closest('[data-nav]');
      if (nav) {
        st.month = new Date(st.month.getFullYear(),
                            st.month.getMonth() + Number(nav.dataset.nav), 1);
        st.hover = null;
        paint(pop);
        return;
      }
      if (ev.target.closest('[data-clear]')) {
        input.value = '';
        notifyInput(input);
        st.start = null;
        st.end = null;
        st.hover = null;
        paint(pop);
        return;
      }
      var cell = ev.target.closest('.delta-range-day');
      if (!cell) return;
      var day = parseDate(cell.dataset.date);
      if (!st.start || st.end) {
        st.start = day;
        st.end = null;
        st.hover = null;
      } else if (day < st.start) {
        st.end = st.start;
        st.start = day;
        commit(pop, input);
        return;
      } else {
        st.end = day;
        commit(pop, input);
        return;
      }
      paint(pop);
    });
    pop.addEventListener('mouseover', function (ev) {
      var st = pop._delta;
      var cell = ev.target.closest('.delta-range-day');
      if (!cell || !st.start || st.end) return;
      var day = parseDate(cell.dataset.date);
      if (st.hover && st.hover.getTime() === day.getTime()) return;
      st.hover = day;
      updatePreview(pop, day);
    });
  }

  // Быстрые периоды — полный календарный год/квартал/месяц, в котором
  // находится сегодня. Кнопки только заполняют поле; фильтр применяется
  // обычной отправкой формы.
  function presetRange(kind) {
    var now = new Date();
    var year = now.getFullYear();
    var month = now.getMonth();
    var start;
    var end;
    if (kind === 'year') {
      start = new Date(year, 0, 1);
      end = new Date(year, 11, 31);
    } else if (kind === 'quarter') {
      var first = Math.floor(month / 3) * 3;
      start = new Date(year, first, 1);
      end = new Date(year, first + 3, 0);
    } else {
      start = new Date(year, month, 1);
      end = new Date(year, month + 1, 0);
    }
    return fmtRu(start) + ' — ' + fmtRu(end);
  }

  function initPresets() {
    document.querySelectorAll('[data-range-preset]').forEach(function (btn) {
      if (btn._deltaPresetBound) return;
      btn._deltaPresetBound = true;
      btn.addEventListener('click', function () {
        var form = btn.closest('form');
        if (!form) return;
        var input = form.querySelector('input[name="date_range"]');
        if (!input) return;
        input.value = presetRange(btn.dataset.rangePreset);
        notifyInput(input);
        form.querySelectorAll('[data-range-preset]').forEach(function (button) {
          button.classList.toggle('active', button === btn);
        });
        closePopover();
        input._deltaNoOpen = true;
        input.focus();
      });
    });
  }

  function openPicker(input) {
    var group = input.closest('.input-group');
    var pop = group.parentNode.querySelector('.delta-range-popover')
              || buildPopover(group);
    if (!pop._delta) {
      pop._delta = { month: null, start: null, end: null, hover: null };
      bindPopover(pop, input);
    }
    if (openPop === pop) return;
    closePopover();
    var range = parseValue(input.value);
    pop._delta.start = range.start;
    pop._delta.end = range.end;
    pop._delta.hover = null;
    var base = range.end || range.start || new Date();
    pop._delta.month = new Date(base.getFullYear(), base.getMonth(), 1);
    paint(pop);
    alignPopover(group, pop);
    pop.hidden = false;
    openPop = pop;
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKeyDown);
  }

  // Календарь открывается при фокусе на поле даты и по клику на него
  // (если поле уже в фокусе). Отдельной кнопки календаря нет.
  function initRangeInputs() {
    document.querySelectorAll('input[name="date_range"]').forEach(function (input) {
      if (input._deltaBound) return;
      input._deltaBound = true;
      input.addEventListener('focus', function () {
        if (input._deltaNoOpen) {
          input._deltaNoOpen = false;
          return;
        }
        openPicker(input);
      });
      input.addEventListener('click', function () {
        input._deltaNoOpen = false;
        openPicker(input);
      });
      input.addEventListener('input', function () {
        var group = input.closest('.input-group');
        var pop = group.parentNode.querySelector('.delta-range-popover');
        if (!pop || !pop._delta || openPop !== pop) return;
        var range = parseValue(input.value);
        pop._delta.start = range.start;
        pop._delta.end = range.end;
        pop._delta.hover = null;
        paint(pop);
      });
    });
  }

  initRangeInputs();
  initPresets();

  window.deltaClearRange = function (btn) {
    var group = btn.closest('.input-group');
    var input = group.querySelector('input[name="date_range"]');
    input.value = '';
    notifyInput(input);
    var form = btn.closest('form');
    if (form) {
      form.querySelectorAll('[data-range-preset]').forEach(function (button) {
        button.classList.remove('active');
      });
    }
    var pop = group.parentNode.querySelector('.delta-range-popover');
    if (pop && pop._delta) {
      pop._delta.start = null;
      pop._delta.end = null;
      pop._delta.hover = null;
      if (openPop === pop) paint(pop);
    }
    // Возврат фокуса после очистки не должен снова открывать календарь.
    input._deltaNoOpen = true;
    input.focus();
  };
})();

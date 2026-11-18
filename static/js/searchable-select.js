(function () {
  'use strict';

  // Визуальная копия Select2 на чистом JS для списков, где вариантов может
  // быть больше 10 (клиенты, контрагенты, ответственные, исполнители и т.п.).
  // Включается по атрибуту data-searchable независимо от текущего числа option.
  var generatedIds = 0;
  var widgets = [];

  function normalize(value) {
    return String(value || '').trim().toLocaleLowerCase('ru-RU');
  }

  function matches(item, query) {
    return item.searchText.indexOf(query) !== -1
        || item.searchValue.indexOf(query) !== -1;
  }

  function nextId(prefix) {
    generatedIds += 1;
    return prefix + '-' + generatedIds;
  }

  function addSearch(select) {
    if (select.dataset.searchableSelect === 'ready') {
      return;
    }
    if (!select.hasAttribute('data-searchable')) {
      return;
    }

    var nativeOptions = Array.prototype.slice.call(select.options);
    if (!select.id) {
      select.id = nextId('delta-select');
    }
    var listId = nextId('delta-select-list');

    var wrapper = document.createElement('div');
    wrapper.className = 'delta-select';
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    select.classList.add('delta-select-native');

    var display = document.createElement('div');
    display.className = 'delta-select-display';
    display.tabIndex = 0;
    display.setAttribute('role', 'combobox');
    display.setAttribute('aria-haspopup', 'listbox');
    display.setAttribute('aria-expanded', 'false');
    display.setAttribute('aria-controls', listId);
    display.innerHTML = '<span class="delta-select-value"></span>'
                      + '<span class="delta-select-arrow" aria-hidden="true"></span>';
    wrapper.appendChild(display);
    var valueNode = display.querySelector('.delta-select-value');

    var dropdown = document.createElement('div');
    dropdown.className = 'delta-select-dropdown';
    dropdown.hidden = true;
    dropdown.innerHTML = '<input type="search" class="delta-select-search" '
        + 'placeholder="Поиск..." aria-label="Поиск по вариантам списка">'
        + '<div class="delta-select-options" role="listbox" id="'
        + listId + '"></div>'
        + '<div class="delta-select-empty" hidden>Ничего не найдено</div>';
    wrapper.appendChild(dropdown);

    var search = dropdown.querySelector('.delta-select-search');
    var list = dropdown.querySelector('.delta-select-options');
    var empty = dropdown.querySelector('.delta-select-empty');

    // Нормализованные строки считаем один раз при создании списка: на длинных
    // реестрах поиск не должен каждый раз обходить текст через toLowerCase.
    var items = nativeOptions.map(function (native) {
      var node = document.createElement('div');
      node.className = 'delta-select-option';
      node.setAttribute('role', 'option');
      node.textContent = native.textContent.trim();
      node.dataset.value = native.value;
      list.appendChild(node);
      return {
        native: native,
        node: node,
        searchText: normalize(native.textContent),
        searchValue: normalize(native.value)
      };
    });

    // Кэш видимых пунктов обновляется только в applyFilter(): все остальные
    // функции раньше заново фильтровали весь массив на каждое нажатие клавиши.
    var visible = items.slice();
    var activeIndex = -1;

    function setActive(index) {
      activeIndex = index;
      items.forEach(function (item) {
        item.node.classList.remove('is-active');
      });
      if (activeIndex >= 0 && activeIndex < visible.length) {
        visible[activeIndex].node.classList.add('is-active');
      }
    }

    function activeIndexOfSelected() {
      var option = select.options[select.selectedIndex];
      for (var i = 0; i < visible.length; i += 1) {
        if (visible[i].native === option) {
          return i;
        }
      }
      return -1;
    }

    function scrollActiveIntoView() {
      if (activeIndex < 0 || activeIndex >= visible.length) {
        return;
      }
      var listRect = list.getBoundingClientRect();
      var itemRect = visible[activeIndex].node.getBoundingClientRect();
      if (itemRect.top < listRect.top) {
        list.scrollTop -= listRect.top - itemRect.top;
      } else if (itemRect.bottom > listRect.bottom) {
        list.scrollTop += itemRect.bottom - listRect.bottom;
      }
    }

    function scrollActiveToTop() {
      if (activeIndex < 0 || activeIndex >= visible.length) {
        return;
      }
      var listRect = list.getBoundingClientRect();
      var itemRect = visible[activeIndex].node.getBoundingClientRect();
      var desired = list.scrollTop + itemRect.top - listRect.top;
      var maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
      // Если поставить пункт вверх нельзя, список просто уйдёт в самый низ.
      list.scrollTop = Math.max(0, Math.min(desired, maxScroll));
    }

    function updateDisplay() {
      var option = select.options[select.selectedIndex];
      valueNode.textContent = option ? option.textContent.trim() : '';
      valueNode.classList.toggle('is-placeholder', !select.value);
      items.forEach(function (item) {
        var selected = item.native === option;
        item.node.classList.toggle('is-selected', selected);
        item.node.setAttribute('aria-selected', selected ? 'true' : 'false');
      });
    }

    function applyFilter() {
      var query = normalize(search.value);
      items.forEach(function (item) {
        var show = query === '' || matches(item, query);
        item.node.hidden = !show;
        item.native.hidden = !show;
      });
      visible = items.filter(function (item) {
        return !item.node.hidden;
      });
      empty.hidden = visible.length > 0;
      setActive(visible.length ? 0 : -1);
      list.scrollTop = 0;
    }

    function open() {
      wrapper.classList.add('is-open');
      dropdown.hidden = false;
      display.setAttribute('aria-expanded', 'true');
      search.value = '';
      applyFilter();
      setActive(activeIndexOfSelected());
      scrollActiveToTop();
      search.focus();
    }

    function close() {
      wrapper.classList.remove('is-open');
      dropdown.hidden = true;
      display.setAttribute('aria-expanded', 'false');
      setActive(-1);
    }

    function pick(item) {
      select.value = item.native.value;
      updateDisplay();
      close();
      select.dispatchEvent(new Event('change', { bubbles: true }));
      display.focus();
    }

    display.addEventListener('click', function () {
      if (wrapper.classList.contains('is-open')) {
        close();
      } else {
        open();
      }
    });
    display.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
        event.preventDefault();
        open();
      }
    });

    search.addEventListener('input', applyFilter);
    search.addEventListener('change', applyFilter);
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        display.focus();
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActive(visible.length ? (activeIndex + 1) % visible.length : -1);
        scrollActiveIntoView();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActive(visible.length
          ? (activeIndex <= 0 ? visible.length - 1 : activeIndex - 1)
          : -1);
        scrollActiveIntoView();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (activeIndex >= 0 && activeIndex < visible.length) {
          pick(visible[activeIndex]);
        }
      }
    });

    items.forEach(function (item) {
      item.node.addEventListener('click', function () {
        pick(item);
      });
      item.node.addEventListener('mousemove', function () {
        setActive(visible.indexOf(item));
      });
    });

    // Один общий обработчик на документ вместо отдельного на каждый select.
    widgets.push({ wrapper: wrapper, close: close });

    select.addEventListener('change', updateDisplay);
    select.dataset.searchableSelect = 'ready';
    updateDisplay();
  }

  document.addEventListener('mousedown', function (event) {
    widgets.forEach(function (widget) {
      if (!widget.wrapper.contains(event.target)) {
        widget.close();
      }
    });
  });

  function init() {
    document.querySelectorAll('select').forEach(addSearch);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

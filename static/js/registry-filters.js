(function () {
  'use strict';

  // Фильтры реестров сохраняются отдельно для каждого пользователя и реестра.
  // Ключ зависит от пользователя, чтобы разные учётки в одном браузере не
  // перетирали настройки друг друга. Данные живут только в браузере: сервер
  // и база данных не меняются.
  var PREFIX = 'delta.registry-filters.';
  var FLAG = 'delta.registry-filters.restored.';
  // Пагинация и сортировка — состояние экрана, а не фильтр: сохранять их
  // бессмысленно и опасно (страница может исчезнуть после изменения данных).
  var EXCLUDED = ['page', 'sort', 'dir', 'order'];
  var timers = new WeakMap();

  function storageKey(form) {
    var user = document.body.getAttribute('data-user-id') || 'guest';
    return PREFIX + user + '.' + form.getAttribute('data-registry-filter');
  }

  function flagKey(form) {
    var user = document.body.getAttribute('data-user-id') || 'guest';
    return FLAG + user + '.' + form.getAttribute('data-registry-filter');
  }

  function readForm(form) {
    var data = {};
    Array.prototype.forEach.call(form.elements, function (field) {
      if (!field.name || field.disabled) return;
      if (EXCLUDED.indexOf(field.name) !== -1) return;
      if (field.name === 'csrf_token') return;
      var type = (field.type || '').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset'
          || type === 'image') {
        return;
      }
      if (type === 'checkbox' || type === 'radio') {
        if (field.checked) data[field.name] = field.value;
        return;
      }
      if (field.tagName === 'SELECT' && field.multiple) {
        var selected = Array.prototype.map.call(
          field.selectedOptions, function (option) { return option.value; });
        if (selected.length) data[field.name] = selected;
        return;
      }
      var value = (field.value || '').trim();
      if (value) data[field.name] = value;
    });
    return data;
  }

  function buildQuery(data) {
    var params = new URLSearchParams();
    Object.keys(data).sort().forEach(function (name) {
      var value = data[name];
      if (Array.isArray(value)) {
        value.forEach(function (item) {
          if (item !== '' && item !== null && item !== undefined) {
            params.append(name, item);
          }
        });
      } else if (value !== '' && value !== null && value !== undefined) {
        params.set(name, value);
      }
    });
    return params.toString();
  }

  function save(form) {
    try {
      window.localStorage.setItem(storageKey(form), JSON.stringify(readForm(form)));
    } catch (err) {
      // Приватный режим или переполненное хранилище не должны ломать форму.
    }
  }

  function scheduleSave(form) {
    var timer = timers.get(form);
    if (timer) window.clearTimeout(timer);
    timers.set(form, window.setTimeout(function () { save(form); }, 300));
  }

  function clearSaved(form) {
    try {
      window.localStorage.removeItem(storageKey(form));
      window.sessionStorage.removeItem(flagKey(form));
    } catch (err) {
      // Хранилище может быть недоступно — очистка страницы всё равно работает.
    }
  }

  function hasQueryFilters() {
    var params = new URLSearchParams(window.location.search);
    var keys = Array.prototype.filter.call(
      Array.from(params.keys()),
      function (key) { return key !== '_' && key !== 'v'; });
    return keys.length > 0;
  }

  function showNotice(form) {
    if (form.parentNode.querySelector('.delta-filters-notice')) return;
    var notice = document.createElement('div');
    notice.className = ('alert alert-light border py-1 px-2 small mb-2 '
                        + 'd-flex align-items-center delta-filters-notice');
    var text = document.createElement('span');
    text.textContent = 'Фильтры восстановлены из сохранённых.';
    var clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'btn btn-sm btn-link p-0 ms-auto';
    clear.textContent = 'Очистить';
    clear.addEventListener('click', function () {
      clearSaved(form);
      window.location.replace(window.location.pathname);
    });
    notice.appendChild(text);
    notice.appendChild(clear);
    form.parentNode.insertBefore(notice, form);
  }

  function consumeFlag(form) {
    try {
      var key = flagKey(form);
      var value = window.sessionStorage.getItem(key);
      if (value !== window.location.pathname) return false;
      window.sessionStorage.removeItem(key);
      return true;
    } catch (err) {
      return false;
    }
  }

  function restore(form) {
    if (hasQueryFilters()) return;
    var raw;
    var query;
    try {
      raw = window.localStorage.getItem(storageKey(form));
    } catch (err) {
      return;
    }
    if (!raw) return;
    try {
      query = buildQuery(JSON.parse(raw));
    } catch (err) {
      return;
    }
    if (!query) return;
    try {
      window.sessionStorage.setItem(flagKey(form), window.location.pathname);
    } catch (err) {
      // Без флага восстановление всё равно сработает, просто без подсказки.
    }
    window.location.replace(window.location.pathname + '?' + query);
  }

  function bind(form) {
    form.addEventListener('submit', function () { save(form); });
    form.addEventListener('change', function () { scheduleSave(form); });
    form.addEventListener('input', function () { scheduleSave(form); });

    var key = form.getAttribute('data-registry-filter');
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-filter-reset="' + key + '"]'),
      function (link) {
        link.addEventListener('click', function () { clearSaved(form); });
      });
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll('form[data-registry-filter]'),
      function (form) {
        bind(form);
        if (consumeFlag(form)) {
          showNotice(form);
        } else {
          restore(form);
        }
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

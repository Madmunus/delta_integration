/* AJAX-выполнение сценариев на странице «Тестирование».
 *
 * Запуск сценария — POST /testing/run/<id>: сервер выполняет сценарий
 * в изолированной базе и возвращает JSON-сводку.
 * Результат каждого шага — отдельный GET /testing/run/<id>/step?index=N,
 * поэтому таблица заполняется по одному шагу, а не одним куском.
 * Без JavaScript формы работают как обычный POST с редиректом.
 */
(function () {
  'use strict';

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF_TOKEN = csrfMeta ? csrfMeta.content : '';
  var batchRunning = false;
  var runInProgress = false;

  function setAllRunButtons(disabled) {
    document.querySelectorAll('.js-run-btn').forEach(function (btn) {
      btn.disabled = disabled;
    });
    var runAllBtn = document.getElementById('run-all-btn');
    if (runAllBtn) runAllBtn.disabled = disabled;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  async function requestJSON(url, options) {
    var opts = options || {};
    var headers = Object.assign({
      'Accept': 'application/json',
      'X-Requested-With': 'XMLHttpRequest'
    }, opts.headers || {});

    var res = await fetch(url, {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: headers
    });
    var text = await res.text();
    var data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      throw new Error((data && data.error) || ('ошибка HTTP ' + res.status));
    }
    if (!data) throw new Error('сервер вернул не JSON');
    return data;
  }

  function setBusy(card, busy) {
    var btn = card.querySelector('.js-run-btn');
    if (!btn) return;
    if (busy) {
      if (!btn.dataset.label) btn.dataset.label = btn.textContent.trim();
      btn.disabled = true;
      clear(btn);
      btn.appendChild(el('span', 'spinner-border spinner-border-sm me-1'));
      btn.appendChild(document.createTextNode('Выполняется…'));
    } else {
      btn.disabled = batchRunning;
      clear(btn);
      btn.textContent = btn.dataset.label || 'Выполнить';
    }
  }

  function setStatus(card, summary) {
    var badge = card.querySelector('.js-status');
    if (!badge) return;
    badge.hidden = false;
    badge.className = 'badge js-status ' +
      (summary.status === 'passed' ? 'text-bg-success' : 'text-bg-danger');
    badge.textContent = summary.status === 'passed' ? 'Пройден' : 'Не пройден';
  }

  function setStatusRunning(card) {
    var badge = card.querySelector('.js-status');
    if (!badge) return;
    badge.hidden = false;
    badge.className = 'badge js-status text-bg-secondary';
    badge.textContent = 'Выполняется…';
  }

  function setStatusError(card) {
    var badge = card.querySelector('.js-status');
    if (!badge) return;
    badge.hidden = false;
    badge.className = 'badge js-status text-bg-warning';
    badge.textContent = 'Ошибка запуска';
  }

  function showRunning(box) {
    clear(box);
    var p = el('p', 'text-muted mb-0 d-flex align-items-center gap-2');
    p.appendChild(el('span', 'spinner-border spinner-border-sm'));
    p.appendChild(document.createTextNode('Выполняется сценарий…'));
    box.appendChild(p);
  }

  function showError(box, message) {
    var previous = box.querySelector('.js-error');
    if (previous) previous.remove();
    // Если сценарий ещё не вернул сводку, убираем заглушку «Выполняется…»,
    // но уже показанные шаги не трогаем.
    if (box.querySelector('.spinner-border')) clear(box);
    box.insertBefore(
      el('div', 'alert alert-danger mb-3 py-2 js-error',
         'Не удалось выполнить сценарий: ' + message),
      box.firstChild);
  }

  /* Каркас результата: строка «Запуск/длительность/шаги», таблица и итог. */
  function renderShell(card, summary) {
    var box = card.querySelector('.js-result');
    clear(box);

    var meta = el('p', 'small text-muted mb-2');
    meta.appendChild(document.createTextNode(
      'Запуск: ' + summary.executed_at +
      ' · длительность ' + summary.duration_ms + ' мс · '));
    var progress = el('span', 'js-progress',
                      'шагов: 0 из ' + summary.total_steps);
    meta.appendChild(progress);
    box.appendChild(meta);

    var wrap = el('div', 'table-responsive mb-3');
    var table = el('table', 'table table-sm align-middle');
    var thead = el('thead', 'table-light');
    var headRow = el('tr');
    [
      ['Шаг', '30%'],
      ['Ожидаемый результат', '27%'],
      ['Фактический результат', null],
      ['Итог', null]
    ].forEach(function (h) {
      var th = el('th', h[0] === 'Итог' ? 'text-center' : '', h[0]);
      if (h[1]) th.style.width = h[1];
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);

    var tbody = el('tbody', 'js-steps');
    table.appendChild(thead);
    table.appendChild(tbody);
    wrap.appendChild(table);
    box.appendChild(wrap);

    var conclusion = el('div', 'alert mb-0 py-2 d-none js-conclusion');
    box.appendChild(conclusion);

    return {
      tbody: tbody,
      progress: progress,
      conclusion: conclusion,
      total: summary.total_steps
    };
  }

  function appendStep(tbody, step) {
    var tr = el('tr', step.status === 'failed' ? 'table-danger' : '');
    tr.classList.add('delta-step-enter');

    tr.appendChild(el('td', '', step.name));
    tr.appendChild(el('td', 'text-muted', step.expected));

    var actual = el('td');
    actual.appendChild(document.createTextNode(step.actual));
    if (step.details) {
      actual.appendChild(el('pre', 'small text-muted mb-0 mt-2', step.details));
    }
    tr.appendChild(actual);

    var verdict = el('td', 'text-center');
    verdict.appendChild(el('span',
      'badge ' + (step.status === 'passed' ? 'text-bg-success' : 'text-bg-danger'),
      step.status === 'passed' ? 'OK' : 'FAIL'));
    tr.appendChild(verdict);

    tbody.appendChild(tr);
  }

  /* Запуск одного сценария: сводка, затем по одному запросу на каждый шаг. */
  async function runScenario(card) {
    var form = card.querySelector('.js-run-form');
    var box = card.querySelector('.js-result');
    var stepUrl = card.dataset.stepUrl;

    runInProgress = true;
    setBusy(card, true);
    setAllRunButtons(true);
    setStatusRunning(card);
    showRunning(box);

    var view = null;
    try {
      var summary = await requestJSON(form.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF_TOKEN }
      });
      view = renderShell(card, summary);
      setStatus(card, summary);

      for (var i = 0; i < summary.total_steps; i++) {
        var data = await requestJSON(stepUrl + '?index=' + i);
        appendStep(view.tbody, data.step);
        view.progress.textContent = 'шагов: ' + (i + 1) + ' из ' + data.total;
      }
      if (!summary.total_steps) view.progress.textContent = 'шагов нет';

      view.conclusion.className = 'alert mb-0 py-2 js-conclusion ' +
        (summary.status === 'passed' ? 'alert-success' : 'alert-danger');
      clear(view.conclusion);
      view.conclusion.appendChild(el('strong', '', 'Результат:'));
      view.conclusion.appendChild(
        document.createTextNode(' ' + summary.conclusion));
      return summary.status === 'passed';
    } catch (err) {
      setStatusError(card);
      showError(box, err.message);
      return false;
    } finally {
      runInProgress = false;
      setBusy(card, false);
      setAllRunButtons(batchRunning);
    }
  }

  async function runAll() {
    if (batchRunning || runInProgress) return;
    var cards = Array.prototype.slice.call(
      document.querySelectorAll('[data-scenario]'));
    var summaryBox = document.getElementById('testing-summary');

    batchRunning = true;
    setAllRunButtons(true);
    summaryBox.className = 'alert alert-info';
    summaryBox.textContent = 'Выполняются сценарии: 0 из ' + cards.length + '…';

    var passed = 0;
    try {
      for (var i = 0; i < cards.length; i++) {
        summaryBox.textContent = 'Выполняются сценарии: ' +
          (i + 1) + ' из ' + cards.length + '…';
        if (await runScenario(cards[i])) passed += 1;
      }
    } finally {
      batchRunning = false;
      setAllRunButtons(false);
    }

    summaryBox.className = 'alert ' +
      (passed === cards.length ? 'alert-success' : 'alert-danger');
    summaryBox.textContent = 'Выполнено сценариев: ' + cards.length +
      ', пройдено: ' + passed;
  }

  document.querySelectorAll('.js-run-form').forEach(function (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (batchRunning || runInProgress) return;
      runScenario(form.closest('[data-scenario]'));
    });
  });

  var runAllForm = document.getElementById('run-all-form');
  if (runAllForm) {
    runAllForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      runAll();
    });
  }
})();

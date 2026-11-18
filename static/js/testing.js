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

  var STEP_BATCH = 4;          // шагов за один запрос при отрисовке
  var POLL_TIMEOUT_MS = 120000;// предел ожидания фонового run-all
  var POLL_INITIAL_MS = 400;   // начальная пауза между опросами
  var POLL_MAX_MS = 2000;      // максимальная пауза (backoff)

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

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function nextFrame() {
    return new Promise(function (resolve) {
      if (window.requestAnimationFrame) requestAnimationFrame(resolve);
      else setTimeout(resolve, 0);
    });
  }

  /* Несколько шагов одним параллельным пакетом: порядок сохраняем сами. */
  async function loadStepBatch(stepUrl, start, count) {
    var indexes = [];
    for (var i = start; i < start + count; i++) indexes.push(i);
    var replies = await Promise.all(indexes.map(function (index) {
      return requestJSON(stepUrl + '?index=' + index);
    }));
    return replies.map(function (reply) { return reply.step; });
  }

  function appendSteps(tbody, steps) {
    var fragment = document.createDocumentFragment();
    steps.forEach(function (step) {
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

      fragment.appendChild(tr);
    });
    // Ленивый рендер: одна вставка на пакет вместо вставки каждой строки.
    tbody.appendChild(fragment);
  }

  async function renderSteps(view, stepUrl) {
    var drawn = 0;
    while (drawn < view.total) {
      var count = Math.min(STEP_BATCH, view.total - drawn);
      var steps = await loadStepBatch(stepUrl, drawn, count);
      appendSteps(view.tbody, steps);
      drawn += steps.length;
      view.progress.textContent = 'шагов: ' + drawn + ' из ' + view.total;
      await nextFrame();
    }
    if (!view.total) view.progress.textContent = 'шагов нет';
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

  function renderConclusion(view, summary) {
    view.conclusion.className = 'alert mb-0 py-2 js-conclusion ' +
      (summary.status === 'passed' ? 'alert-success' : 'alert-danger');
    clear(view.conclusion);
    view.conclusion.appendChild(el('strong', '', 'Результат:'));
    view.conclusion.appendChild(
      document.createTextNode(' ' + summary.conclusion));
  }

  async function loadSteps(card, summary) {
    var view = renderShell(card, summary);
    setStatus(card, summary);
    await renderSteps(view, card.dataset.stepUrl);
    renderConclusion(view, summary);
  }

  /* Запуск одного сценария: сводка, затем по одному запросу на каждый шаг. */
  async function runScenario(card) {
    var form = card.querySelector('.js-run-form');
    var box = card.querySelector('.js-result');

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

      await renderSteps(view, stepUrl);

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
    var runAllForm = document.getElementById('run-all-form');

    batchRunning = true;
    setAllRunButtons(true);
    summaryBox.className = 'alert alert-info';
    summaryBox.textContent = 'Выполняются сценарии: 0 из ' + cards.length + '…';

    try {
      var response = await requestJSON(runAllForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': CSRF_TOKEN }
      });
      var data = response;

      // Живое приложение возвращает job_id: сценарии выполняются в фоне.
      if (response.job_id && response.status === 'running') {
        var deadline = Date.now() + POLL_TIMEOUT_MS;
        var pause = POLL_INITIAL_MS;
        while (Date.now() < deadline) {
          await sleep(pause);
          data = await requestJSON(
            '/testing/run-all/status/' + encodeURIComponent(response.job_id));
          if (data.status === 'running') {
            summaryBox.textContent = 'Выполняются сценарии: ' +
              (data.completed || 0) + ' из ' + (data.total || cards.length) + '…';
            // Backoff: реже дёржим сервер при долгом прогоне.
            pause = Math.min(POLL_MAX_MS, Math.round(pause * 1.5));
            continue;
          }
          if (data.status === 'failed') {
            throw new Error(data.error || 'фоновый запуск прерван');
          }
          break;
        }
        if (data.status === 'running') {
          throw new Error('время ожидания фонового запуска истекло');
        }
      }

      var passed = 0;
      var shown = data.scenarios || [];
      for (var i = 0; i < shown.length; i++) {
        var card = cards.filter(function (c) {
          return c.dataset.scenario === shown[i].id;
        })[0];
        if (!card) continue;
        if (shown[i].status === 'passed') passed += 1;
        await loadSteps(card, shown[i]);
      }

      var total = data.total !== undefined ? data.total : shown.length;
      summaryBox.className = 'alert ' +
        (passed === total ? 'alert-success' : 'alert-danger');
      summaryBox.textContent = 'Выполнено сценариев: ' + total +
        ', пройдено: ' + passed;
    } catch (err) {
      summaryBox.className = 'alert alert-danger';
      summaryBox.textContent = 'Не удалось выполнить сценарии: ' + err.message;
    } finally {
      batchRunning = false;
      setAllRunButtons(false);
    }
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

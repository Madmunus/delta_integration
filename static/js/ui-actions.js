(function () {
  'use strict';

  // Delegated handlers instead of inline onclick/onsubmit: the page keeps a
  // strict CSP without 'unsafe-inline'. All messages come from data-confirm,
  // which Jinja escapes when rendering.
  document.addEventListener('click', function (ev) {
    if (!ev.target || !ev.target.closest) return;
    var el = ev.target.closest(
      '[data-search-clear], button[data-confirm], input[data-confirm]');
    if (!el) return;

    if (el.hasAttribute('data-search-clear')) {
      var field = el.form && el.form.querySelector('input[name="q"]');
      if (field) {
        field.value = '';
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.focus();
      }
      return;
    }
    if ((el.tagName === 'BUTTON' || el.tagName === 'INPUT')
            && !window.confirm(el.getAttribute('data-confirm') || '')) {
      ev.preventDefault();
    }
  });

  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (form.hasAttribute && form.hasAttribute('data-confirm')
            && !window.confirm(form.getAttribute('data-confirm') || '')) {
      ev.preventDefault();
    }
  });

  // Geometry of charts and progress bars is computed on the server. CSSOM
  // writes are allowed by CSP; inline style attributes are not.
  function applyMetrics() {
    document.querySelectorAll('[data-bar-height]').forEach(function (el) {
      el.style.height = el.getAttribute('data-bar-height') + '%';
    });
    document.querySelectorAll('[data-bar-width]').forEach(function (el) {
      el.style.width = el.getAttribute('data-bar-width') + '%';
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyMetrics);
  } else {
    applyMetrics();
  }
})();

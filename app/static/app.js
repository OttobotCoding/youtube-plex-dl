// Small helpers layered on top of htmx: bulk selection, selection counter,
// auto-dismissing toasts.
(function () {
  function cardsIn(scope) {
    return Array.from(scope.querySelectorAll('input[name="video_ids"]:not(:disabled)'));
  }

  function updateCount(scope) {
    var counter = document.getElementById('sel-count');
    if (!counter) return;
    var form = document.getElementById('select-form');
    if (!form) { counter.textContent = ''; return; }
    var boxes = cardsIn(form);
    var n = boxes.filter(function (b) { return b.checked; }).length;
    counter.textContent = n + ' of ' + boxes.length + ' selected';
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-select]');
    if (btn) {
      var form = btn.closest('form');
      if (!form) return;
      var mode = btn.getAttribute('data-select');
      cardsIn(form).forEach(function (b) {
        b.checked = mode === 'all' ? true : mode === 'none' ? false : !b.checked;
      });
      updateCount(form);
      return;
    }
    if (e.target.matches('input[name="video_ids"]')) updateCount(document);
  });

  document.addEventListener('change', function (e) {
    if (e.target.matches('input[name="video_ids"]')) updateCount(document);
  });

  document.body.addEventListener('htmx:afterSwap', function (evt) {
    updateCount(document);
    // Auto-dismiss toasts after a few seconds.
    if (evt.detail.target && evt.detail.target.id === 'toast-host') {
      Array.from(evt.detail.target.querySelectorAll('[data-toast]')).forEach(function (t) {
        if (t.dataset.armed) return;
        t.dataset.armed = '1';
        setTimeout(function () { t.remove(); }, 6000);
      });
    }
  });

  // Enter in the URL box submits the analyze form.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.id === 'url-input') {
      e.preventDefault();
      document.getElementById('analyze-form').requestSubmit();
    }
  });

  // --- never fail silently ------------------------------------------------
  // htmx ignores non-2xx responses by default, so an unhandled server error
  // used to leave the page sitting there with nothing to show. Surface every
  // transport-level failure as a toast.
  function errorToast(text) {
    var host = document.getElementById('toast-host');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'toast toast-error';
    el.dataset.toast = '1';
    el.dataset.armed = '1';
    var msg = document.createElement('span');
    msg.textContent = text;
    var x = document.createElement('button');
    x.type = 'button';
    x.className = 'toast-x';
    x.textContent = '×';
    x.onclick = function () { el.remove(); };
    el.appendChild(msg);
    el.appendChild(x);
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, 12000);
  }

  document.body.addEventListener('htmx:responseError', function (evt) {
    var x = evt.detail.xhr;
    errorToast('Server error ' + x.status + ' on ' + (evt.detail.pathInfo
      ? evt.detail.pathInfo.requestPath : 'request') +
      '. Check the container log: docker compose logs --tail 50');
  });
  document.body.addEventListener('htmx:sendError', function () {
    errorToast('Could not reach the server. Is the container still running?');
  });
  document.body.addEventListener('htmx:timeout', function () {
    errorToast('The request timed out in the browser. Large channels can take '
      + 'a while — try a smaller count.');
  });

  // --- "still working" hint for slow channel probes -----------------------
  var slowTimer = null;
  document.body.addEventListener('htmx:beforeRequest', function (evt) {
    if (!evt.detail.pathInfo || evt.detail.pathInfo.requestPath !== '/analyze') return;
    var hint = document.getElementById('slow-hint');
    if (!hint) return;
    clearTimeout(slowTimer);
    hint.hidden = true;
    slowTimer = setTimeout(function () { hint.hidden = false; }, 6000);
  });
  document.body.addEventListener('htmx:afterRequest', function (evt) {
    if (!evt.detail.pathInfo || evt.detail.pathInfo.requestPath !== '/analyze') return;
    clearTimeout(slowTimer);
    var hint = document.getElementById('slow-hint');
    if (hint) hint.hidden = true;
  });
})();

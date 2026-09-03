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
})();

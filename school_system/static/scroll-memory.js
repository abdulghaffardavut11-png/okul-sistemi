/* Remembers where you were so pages never jump back to the top by surprise.
 *  - Back/forward and reload: restores the saved scroll position of that page.
 *  - After a form action that reloads the same page (save, delete, filter, language...):
 *    restores the position you were at when you pressed the button.
 *  - Elements with data-scroll-keep keep their own inner scroll (data-scroll-global: on every page,
 *    used for the side menu).
 * Loaded synchronously in <head>; exposes window.PortalReturn.isReturn for page scripts. */
(function () {
  'use strict';
  var store = null;
  try {
    store = window.sessionStorage;
    store.setItem('__pm', '1');
    store.removeItem('__pm');
  } catch (e) { store = null; }

  var nav = window.performance && performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null;
  var navType = nav && nav.type ? nav.type : 'navigate';
  var KEY = 'scroll:' + location.pathname + location.search;
  var FLAG = 'scroll-return';
  var pending = null;
  if (store) {
    try { pending = JSON.parse(store.getItem(FLAG) || 'null'); } catch (e) { pending = null; }
    try { store.removeItem(FLAG); } catch (e) { /* ignore */ }
  }
  var fresh = !!(pending && Date.now() - pending.t < 20000 && pending.path === location.pathname);
  var isReturn = navType === 'back_forward' || navType === 'reload' || fresh;
  window.PortalReturn = { isReturn: isReturn };
  if (!store) return;

  try { history.scrollRestoration = 'manual'; } catch (e) { /* ignore */ }
  var num = function (value) { var n = parseFloat(value); return isFinite(n) ? n : null; };

  var target = null;
  if (!location.hash) {
    if (fresh) target = num(pending.y);
    else if (navType === 'back_forward' || navType === 'reload') target = num(store.getItem(KEY));
  }

  var restoring = target !== null;
  var saveWindow = function () {
    if (restoring) return;
    try { store.setItem(KEY, String(Math.round(window.scrollY || window.pageYOffset || 0))); } catch (e) { /* ignore */ }
  };
  var timer = null;
  var throttle = function (fn) {
    return function () {
      if (timer) return;
      timer = setTimeout(function () { timer = null; fn(); }, 120);
    };
  };

  var restoreWindow = function () {
    if (target === null) return;
    var tries = 0;
    var step = function () {
      try { window.scrollTo({ top: target, left: 0, behavior: 'instant' }); } catch (e) { window.scrollTo(0, target); }
      tries += 1;
      if (tries < 15 && Math.abs((window.scrollY || 0) - target) > 3) setTimeout(step, 70);
      else restoring = false;
    };
    step();
  };

  var keepInner = function () {
    var nodes = document.querySelectorAll('[data-scroll-keep]');
    for (var i = 0; i < nodes.length; i += 1) {
      (function (el) {
        var global = el.hasAttribute('data-scroll-global');
        var k = 'scrollkeep:' + (global ? '' : location.pathname + ':') + el.getAttribute('data-scroll-keep');
        if (!el.__scrollKept) {
          el.__scrollKept = true;
          var t = null;
          el.addEventListener('scroll', function () {
            if (t) return;
            t = setTimeout(function () { t = null; try { store.setItem(k, String(Math.round(el.scrollTop))); } catch (e) { /* ignore */ } }, 120);
          }, { passive: true });
        }
        if (global || isReturn) {
          var saved = num(store.getItem(k));
          if (saved !== null) el.scrollTop = saved;
        }
      })(nodes[i]);
    }
  };

  var start = function () {
    restoreWindow();
    keepInner();
    if (target === null) restoring = false;
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
  window.addEventListener('load', function () { restoreWindow(); keepInner(); });

  window.addEventListener('scroll', throttle(saveWindow), { passive: true });
  window.addEventListener('pagehide', saveWindow);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveWindow();
  });
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (form && form.hasAttribute && form.hasAttribute('data-no-scroll-memory')) return;
    try {
      store.setItem(FLAG, JSON.stringify({ path: location.pathname, y: Math.round(window.scrollY || 0), t: Date.now() }));
    } catch (e) { /* ignore */ }
  }, true);
})();

// Runs static/scroll-memory.js against a tiny fake browser and checks the scroll behaviour.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname, '..', 'school_system', 'static', 'scroll-memory.js'), 'utf8');

function makeBrowser({ navType = 'navigate', url = '/student-registration', search = '', session = {}, innerEls = [] }) {
  const store = Object.assign({}, session);
  const timers = [];
  const handlers = { window: {}, document: {} };
  const scrolls = [];
  const win = {
    scrollY: 0, pageYOffset: 0,
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    scrollTo: (a, b) => { const y = typeof a === 'object' ? a.top : b; win.scrollY = y; scrolls.push(y); },
    addEventListener: (n, f) => { (handlers.window[n] = handlers.window[n] || []).push(f); },
    performance: { getEntriesByType: () => [{ type: navType }] },
  };
  const doc = {
    readyState: 'loading', visibilityState: 'visible',
    addEventListener: (n, f) => { (handlers.document[n] = handlers.document[n] || []).push(f); },
    querySelectorAll: () => innerEls,
  };
  const ctx = {
    window: win, document: doc, history: {}, performance: win.performance,
    location: { pathname: url, search, hash: '' },
    setTimeout: (f) => { timers.push(f); return timers.length; },
    isFinite, parseFloat, Date, Math, JSON,
  };
  win.window = win; win.document = doc;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  const fire = (target, name, ev) => (handlers[target][name] || []).forEach((f) => f(ev || {}));
  const flush = () => { let guard = 0; while (timers.length && guard++ < 200) timers.shift()(); };
  return { win, doc, store, scrolls, fire, flush, ctx };
}
const assert = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } };

// 1. Back button: restores the saved position, and the early scroll-to-0 event must not overwrite it.
{
  const b = makeBrowser({ navType: 'back_forward', session: { 'scroll:/student-registration': '1234' } });
  assert(b.ctx.window.PortalReturn.isReturn === true, 'back_forward counts as a return visit');
  b.win.scrollY = 0; b.fire('window', 'scroll'); b.flush();          // browser's own early reset to the top
  assert(b.store['scroll:/student-registration'] === '1234', 'early scroll-to-0 must not overwrite the saved value');
  b.doc.readyState = 'interactive'; b.fire('document', 'DOMContentLoaded'); b.flush();
  assert(b.scrolls.includes(1234), 'scrolled to the saved 1234');
  b.win.scrollY = 1500; b.fire('window', 'scroll'); b.flush();
  assert(b.store['scroll:/student-registration'] === '1500', 'saving resumes after the restore');
}
// 2. Normal visit: starts at the top, then remembers where the reader scrolls to.
{
  const b = makeBrowser({ navType: 'navigate', session: { 'scroll:/student-registration': '999' } });
  assert(b.ctx.window.PortalReturn.isReturn === false, 'plain navigation is not a return');
  b.fire('document', 'DOMContentLoaded'); b.flush();
  assert(b.scrolls.length === 0, 'no jump on a normal visit');
  b.win.scrollY = 640; b.fire('window', 'scroll'); b.flush();
  assert(b.store['scroll:/student-registration'] === '640', 'remembers 640');
}
// 3. Form action that reloads the same page: position comes back.
{
  const first = makeBrowser({ navType: 'navigate' });
  first.win.scrollY = 2200; first.fire('document', 'submit', { target: { hasAttribute: () => false } });
  const flag = first.store['scroll-return'];
  assert(flag && JSON.parse(flag).y === 2200, 'flag stores 2200 on submit');
  const after = makeBrowser({ navType: 'navigate', session: { 'scroll-return': flag.replace(/"t":\d+/, '"t":' + Date.now()) } });
  assert(after.ctx.window.PortalReturn.isReturn === true, 'same-page reload after submit is a return');
  after.fire('document', 'DOMContentLoaded'); after.flush();
  assert(after.scrolls.includes(2200), 'restored 2200 after the form reload');
  assert(!('scroll-return' in after.store), 'flag is consumed');
}
// 4. A form that leads to another page must not move that page.
{
  const other = makeBrowser({ navType: 'navigate', url: '/settings', session: {
    'scroll-return': JSON.stringify({ path: '/student-registration', y: 900, t: Date.now() }) } });
  other.fire('document', 'DOMContentLoaded'); other.flush();
  assert(other.scrolls.length === 0, 'different page ignores the flag');
  const stale = makeBrowser({ navType: 'navigate', session: {
    'scroll-return': JSON.stringify({ path: '/student-registration', y: 900, t: Date.now() - 60000 }) } });
  stale.fire('document', 'DOMContentLoaded'); stale.flush();
  assert(stale.scrolls.length === 0, 'stale flag ignored');
}
// 5. Opt-out forms do not leave a flag; side menu keeps its own scroll on every page.
{
  const b = makeBrowser({ navType: 'navigate' });
  b.fire('document', 'submit', { target: { hasAttribute: (n) => n === 'data-no-scroll-memory' } });
  assert(!('scroll-return' in b.store), 'opt-out respected');
  const menu = { scrollTop: 0, hasAttribute: (n) => n === 'data-scroll-global', getAttribute: () => 'sidebar', addEventListener() {} };
  const m = makeBrowser({ navType: 'navigate', session: { 'scrollkeep:sidebar': '310' }, innerEls: [menu] });
  m.fire('document', 'DOMContentLoaded');
  assert(menu.scrollTop === 310, 'menu scroll restored on a fresh page');
  const list = { scrollTop: 0, hasAttribute: () => false, getAttribute: () => 'student-table', addEventListener() {} };
  const fresh = makeBrowser({ navType: 'navigate', session: { 'scrollkeep:/student-registration:student-table': '120' }, innerEls: [list] });
  fresh.fire('document', 'DOMContentLoaded');
  assert(list.scrollTop === 0, 'page-local container starts at the top on a normal visit');
  const back = makeBrowser({ navType: 'back_forward', session: { 'scrollkeep:/student-registration:student-table': '120' }, innerEls: [list] });
  back.fire('document', 'DOMContentLoaded');
  assert(list.scrollTop === 120, 'page-local container restored when coming back');
}
console.log('scroll-memory OK');

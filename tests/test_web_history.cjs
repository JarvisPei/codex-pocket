const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const code = source.slice(source.indexOf('function mergeIncrementalThreadDetail('), source.indexOf('function renderHistoryNotice('));
const turn = (id, text = id) => ({id, items: [{text}]});
const snapshot = (ids, next = 'older') => ({id: 't', historyPaged: true, historyNextCursor: next,
  historyTruncated: !!next, turns: ids.map(id => turn(id))});
function harness(extra = {}) {
  const context = vm.createContext({Set, Map, URLSearchParams, ...extra});
  vm.runInContext(code, context);
  return context;
}
const plain = value => JSON.parse(JSON.stringify(value));

test('fresh newest page preserves already loaded older turns and their cursor', () => {
  const h = harness();
  const cached = {thread: snapshot(['1', '2', '3', '4'], 'oldest'), complete: true};
  const incoming = snapshot(['3', '4', '5'], 'wrong-newest-page-cursor');
  const result = h.mergeIncrementalThreadDetail(cached, incoming).thread;
  assert.deepEqual(plain(result.turns.map(t => t.id)), ['1', '2', '3', '4', '5']);
  assert.equal(result.historyNextCursor, 'oldest');
});

test('mutable tail update keeps older pages instead of clipping to 30 turns', () => {
  const h = harness();
  const ids = Array.from({length: 90}, (_, i) => String(i));
  const cached = {thread: snapshot(ids, null), complete: true};
  const incoming = {...snapshot(['89']), historyLimit: 30, historyDelta: {baseTurnId: '89', mode: 'replace'}};
  incoming.turns[0] = turn('89', 'new output');
  const result = h.mergeIncrementalThreadDetail(cached, incoming).thread;
  assert.equal(result.turns.length, 90);
  assert.equal(result.turns.at(-1).items[0].text, 'new output');
  assert.equal(result.historyNextCursor, null);
  assert.equal(result.historyTruncated, false);
  assert.equal(result.historyDelta, undefined);
});

test('unchanged tail sends no items without losing stored history', () => {
  const h = harness();
  const cached = {thread: snapshot(['1', '2']), complete: true};
  const result = h.mergeIncrementalThreadDetail(cached, {...snapshot([]), historyDelta: {baseTurnId: '2', mode: 'append'}});
  assert.deepEqual(plain(result.thread.turns.map(t => t.id)), ['1', '2']);
});

test('missing base and incomplete session cache request a full newest page', () => {
  const h = harness();
  const incoming = {...snapshot(['new']), historyDelta: {baseTurnId: 'missing', mode: 'replace'}};
  assert.equal(h.mergeIncrementalThreadDetail({thread: snapshot(['1'])}, incoming), undefined);
  incoming.historyDelta.baseTurnId = '1';
  assert.equal(h.mergeIncrementalThreadDetail({thread: snapshot(['1']), complete: false}, incoming), undefined);
});

test('nonoverlapping page resets instead of silently joining a history gap', () => {
  const h = harness();
  const incoming = snapshot(['7', '8']);
  const result = h.mergeIncrementalThreadDetail({thread: snapshot(['1', '2'])}, incoming);
  assert.deepEqual(plain(result.thread.turns.map(t => t.id)), ['7', '8']);
});

test('older page prepends in order, deduplicates overlap and preserves live tail', () => {
  const h = harness();
  const current = {...snapshot(['3', '4']), status: 'active', historyCursor: {turnId: '4'}};
  const older = {...snapshot(['1', '2', '3'], null), status: 'idle'};
  older.turns[2] = turn('3', 'stale');
  const result = h.prependHistoryPage(current, older);
  assert.deepEqual(plain(result.turns.map(t => t.id)), ['1', '2', '3', '4']);
  assert.equal(result.turns[2].items[0].text, '3');
  assert.equal(result.status, 'active');
  assert.equal(result.historyCursor.turnId, '4');
  assert.equal(result.historyNextCursor, null);
});

test('older page request coalesces and merges with a concurrent newest refresh', async () => {
  let finish, calls = 0, renders = 0;
  const cache = new Map([['t', {thread: snapshot(['3', '4']), turnLimit: 2}]]);
  const h = harness({threadHistoryCache: cache, olderHistoryLoading: new Set(),
    selectedThread: {id: 't'}, threads: [{id: 't'}], INITIAL_HISTORY_TURNS: 30,
    authorizationHeaders: () => ({}), renderHistoryNotice() {},
    renderThreadDetail(thread, count, options) {renders++; assert.equal(options.preservePosition, true);},
    rememberThreadDetail(summary, thread, turnLimit) {cache.set('t', {thread, turnLimit});},
    fetch: async () => {calls++; return new Promise(resolve => {finish = resolve;});},
    elements: {threadMeta: {textContent: ''}},
  });
  const pending = h.loadOlderHistory('t');
  await h.loadOlderHistory('t');
  assert.equal(calls, 1);
  cache.set('t', {thread: snapshot(['3', '4', '5']), turnLimit: 3});
  finish({ok: true, status: 200, json: async () => ({thread: snapshot(['1', '2'], null)})});
  await pending;
  assert.deepEqual(plain(cache.get('t').thread.turns.map(t => t.id)), ['1', '2', '3', '4', '5']);
  assert.equal(renders, 1);
  assert.equal(h.olderHistoryLoading.size, 0);
});

test('failed older page retains history and releases retry control', async () => {
  const cached = {thread: snapshot(['3', '4']), turnLimit: 2};
  const cache = new Map([['t', cached]]);
  const h = harness({threadHistoryCache: cache, olderHistoryLoading: new Set(), selectedThread: {id: 't'},
    INITIAL_HISTORY_TURNS: 30, authorizationHeaders: () => ({}), renderHistoryNotice() {},
    fetch: async () => ({ok: false, status: 502}), elements: {threadMeta: {textContent: ''}},
  });
  await h.loadOlderHistory('t');
  assert.equal(cache.get('t'), cached);
  assert.equal(h.olderHistoryLoading.size, 0);
  assert.match(h.elements.threadMeta.textContent, /已有内容保留/);
});

test('late older-page response never renders into another selected thread', async () => {
  const cache = new Map([['t', {thread: snapshot(['3']), turnLimit: 1}]]);
  const h = harness({threadHistoryCache: cache, olderHistoryLoading: new Set(), selectedThread: {id: 'other'},
    threads: [{id: 't'}], INITIAL_HISTORY_TURNS: 30, authorizationHeaders: () => ({}),
    renderHistoryNotice() {throw Error('wrong thread');}, renderThreadDetail() {throw Error('wrong thread');},
    rememberThreadDetail(summary, thread, turnLimit) {cache.set('t', {thread, turnLimit});},
    fetch: async () => ({ok: true, status: 200, json: async () => ({thread: snapshot(['1', '2'], null)})}),
  });
  await h.loadOlderHistory('t');
  assert.deepEqual(plain(cache.get('t').thread.turns.map(t => t.id)), ['1', '2', '3']);
});

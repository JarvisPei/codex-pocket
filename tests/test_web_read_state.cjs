const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/app.js'), 'utf8');
const slice = source.slice(source.indexOf('function threadCatalogRevision'), source.indexOf('function reconcileThreadCatalog'));
function harness(response = {ok:true,status:200,json:async()=>({ok:true,threadId:'one',isUnread:false})}) {
  const thread={id:'one',updatedAt:123,isUnread:true,readStateAuthoritative:true,readStateAvailable:true,readStateScope:'account-host'};
  const calls=[];
  const ctx=vm.createContext({threads:[thread],selectedThread:thread,document:{visibilityState:'visible'},
    threadReadRevisions:{one:'123'}, localStorage:{getItem:()=>null,setItem(){}},UNREAD_BASELINE_KEY:'baseline',
    saveThreadReadRevisions(){},Date,Map,AbortSignal,authorizationHeaders:()=>({}),handleUnauthorized(){},
    renderProjectGroups(){}, elements:{threadMeta:{textContent:''}},fetch:async(...args)=>{calls.push(args);return response;}});
  vm.runInContext(slice,ctx);
  return {ctx,thread,calls,run:s=>vm.runInContext(s,ctx)};
}
test('modern manual unread survives local receipts and initial baseline',()=>{
  const h=harness();h.run('applyThreadReadReceipts(threads)');assert.equal(h.thread.isUnread,true);
  assert.equal(h.run('acknowledgeThreadRead("one")'),false);assert.equal(h.thread.isUnread,true);
});
test('confirmed Desktop acknowledgement clears unread with scoped revision',async()=>{
  const h=harness();await h.run('acknowledgeDesktopRead(threads[0])');assert.equal(h.thread.isUnread,false);
  assert.deepEqual(JSON.parse(h.calls[0][1].body),{scope:'account-host',revision:'123'});
});
test('failed acknowledgement preserves unread and exposes retry hint',async()=>{
  const h=harness({ok:false,status:502});await h.run('acknowledgeDesktopRead(threads[0])');
  assert.equal(h.thread.isUnread,true);assert.match(h.ctx.elements.threadMeta.textContent,/暂未同步/);
});
test('hidden page and unavailable state never mark read',async()=>{
  const h=harness();h.ctx.document.visibilityState='hidden';await h.run('acknowledgeDesktopRead(threads[0])');
  h.ctx.document.visibilityState='visible';h.thread.readStateAvailable=false;await h.run('acknowledgeDesktopRead(threads[0])');
  assert.equal(h.calls.length,0);
});
test('identity change during reply does not clear new account state',async()=>{
  const h=harness();h.ctx.fetch=async()=>{h.thread.readStateScope='other';return {ok:true,status:200,json:async()=>({ok:true,threadId:'one',isUnread:false})};};
  await h.run('acknowledgeDesktopRead(threads[0])');assert.equal(h.thread.isUnread,true);
});
test('legacy local receipt behavior remains unchanged',()=>{
  const h=harness();h.thread.readStateAuthoritative=false;h.run('applyThreadReadReceipts(threads)');assert.equal(h.thread.isUnread,false);
});

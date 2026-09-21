const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function between(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

function harness() {
  const nodes = new Map();
  const element = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      disabled: false, textContent: '',
      classList: { toggle() {} }, setAttribute() {},
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { querySelector: element },
    elements: new Proxy({}, { get: (_, key) => element(key) }),
    selectedThread: { id: 'a' }, managedRun: undefined,
    isSendingMessage: false, isUploadingAttachments: false,
    nativeDeliveryNotices: new Map(),
    isCreatingTask: false, isUploadingNewTaskAttachments: false,
    newTaskAttachments: [], MAX_ATTACHMENTS_PER_TURN: 4,
    currentStopCandidates: 0, modelSettingsLoadingThreadId: '',
    selectedThreadLastTurnStatus: 'inProgress',
    uniqueCurrentThreadId: () => '',
    managedRunIsActive: () => false,
    selectedThreadHasActiveTurn: () => false,
    selectedThreadIsPaused: () => false,
    selectedThreadIsComplete: () => true,
    composerHasContent: () => true,
    selectedAttachments: () => [],
    renderModelSettingsButton() {}, setComposerDisabled() {},
  });
  vm.runInContext(source.slice(0, source.indexOf('const elements =')), context);
  vm.runInContext(between('function nativeMessageIdentity(', 'async function nativeDeliveryRequestId('), context);
  vm.runInContext(between('function updateNewTaskControls()', 'function renderNewTaskAttachments()'), context);
  vm.runInContext(between('function updateComposerState()', 'function drawerThreadIsRunning('), context);
  return { context, nodes, run: code => vm.runInContext(code, context) };
}

const windows = `applyBridgeCapabilities({platform:'windows', executionMode:'background', desktopControl:false, attachments:false});`;

test('old Mac server with no capabilities keeps existing behavior', () => {
  const h = harness();
  h.run('applyBridgeCapabilities(undefined); updateComposerState(); updateNewTaskControls();');
  assert.equal(h.run('hostLabel()'), 'Mac');
  assert.equal(h.nodes.get('attachmentButton').disabled, false);
  assert.equal(h.nodes.get('newTaskAttachmentButton').disabled, false);
});

test('Windows disables attachments and explains background ownership', () => {
  const h = harness();
  h.run(windows + 'updateComposerState();');
  assert.equal(h.run('hostLabel()'), 'Windows');
  assert.equal(h.nodes.get('attachmentButton').disabled, true);
  assert.equal(h.nodes.get('newTaskAttachmentButton').disabled, true);
  assert.match(h.nodes.get('#newTaskExecutionHint').textContent, /后台/);
  assert.equal(h.nodes.get('composerActionButton').disabled, false);
});

test('Windows cannot offer Stop for a Desktop-owned active task', () => {
  const h = harness();
  h.context.selectedThreadHasActiveTurn = () => true;
  h.run(windows + 'updateComposerState();');
  assert.equal(h.nodes.get('composerActionButton').disabled, true);
  assert.match(h.nodes.get('composerState').textContent, /原客户端停止/);
});

test('Windows can Stop a turn owned by this Bridge', () => {
  const h = harness();
  h.context.managedRun = { threadId: 'a', turnId: 'turn-a', status: 'inProgress' };
  h.context.managedRunIsActive = () => true;
  h.run(windows + 'updateComposerState();');
  assert.equal(h.nodes.get('composerActionButton').disabled, false);
  assert.equal(h.nodes.get('composerMode').textContent, '后台 · 运行中');
  assert.doesNotMatch(h.nodes.get('composerState').textContent, /锁屏/);
});

test('native Stop requires explicit capability and known turn, without enabling other controls', () => {
  const h=harness();h.context.selectedThreadHasActiveTurn=()=>true;
  h.context.selectedThreadLastTurnId='turn-a';
  h.run(`applyBridgeCapabilities({platform:'windows',desktopControl:false,nativeTextSend:true,nativeStop:true,newTasks:false,attachments:false});updateComposerState();`);
  assert.equal(h.nodes.get('composerActionButton').disabled,false);
  assert.match(h.nodes.get('composerState').textContent,/Windows/);
  assert.equal(h.nodes.get('newTaskSubmit').disabled,true);
  h.context.selectedThreadLastTurnId='';h.run('updateComposerState()');
  assert.equal(h.nodes.get('composerActionButton').disabled,true);
});

test('Mac still offers Desktop Stop and attachments', () => {
  const h = harness();
  h.context.selectedThreadHasActiveTurn = () => true;
  h.run('updateComposerState();');
  assert.equal(h.nodes.get('composerActionButton').disabled, false);
  assert.equal(h.nodes.get('attachmentButton').disabled, false);
});

test('native Windows preview does not claim background sending or task creation', () => {
  const h = harness();
  h.run(`applyBridgeCapabilities({platform:'windows',executionMode:'background',desktopControl:false,attachments:false,nativeTextSend:true,newTasks:false});updateComposerState();`);
  assert.match(h.nodes.get('#newTaskExecutionHint').textContent, /原生文字预览/);
  assert.equal(h.nodes.get('newTaskSubmit').disabled, true);
  assert.equal(h.nodes.get('composerActionButton').disabled, false);
});

test('native creation requires an explicit capability and keeps attachments disabled', () => {
  const h = harness();
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,attachments:false});`);
  assert.equal(h.nodes.get('newTaskSubmit').disabled, true);
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,newTasks:true,attachments:false});`);
  assert.equal(h.nodes.get('newTaskSubmit').disabled, false);
  assert.equal(h.nodes.get('newTaskAttachmentButton').disabled, true);
  assert.match(h.nodes.get('#newTaskExecutionHint').textContent, /先创建空任务/);
});

test('file path mode enables uploads but explains it is not native image attachment', () => {
  const h = harness();
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,newTasks:true,attachments:true,attachmentMode:'localPaths'});updateComposerState();`);
  assert.equal(h.nodes.get('attachmentButton').disabled, false);
  assert.equal(h.nodes.get('newTaskAttachmentButton').disabled, false);
  assert.match(h.nodes.get('#newTaskAttachmentHint').textContent, /不是原生图片附件/);
  assert.match(h.nodes.get('#newTaskAttachmentHint').textContent, /\.codex-pocket-attachments/);
  assert.match(h.nodes.get('attachmentButton').title, /本机路径/);
});

test('attachment identity includes IDs and order without changing text-only retries', () => {
  const h = harness();
  assert.equal(h.run(`nativeMessageIdentity('text',[])`), 'text');
  assert.notEqual(h.run(`nativeMessageIdentity('text',[{id:'a'}])`), h.run(`nativeMessageIdentity('text',[{id:'b'}])`));
  assert.notEqual(h.run(`nativeMessageIdentity('text',[{id:'a'},{id:'b'}])`), h.run(`nativeMessageIdentity('text',[{id:'b'},{id:'a'}])`));
});

function creationHarness(reply) {
  const storage = new Map(), calls = [], opened = [];
  const context = vm.createContext({
    crypto: require('node:crypto').webcrypto, TextEncoder, AbortController,
    bridgeCapabilities: {nativeTextSend:true,newTasks:true},
    newTaskTarget: {projectId:null,name:'Recents'}, isCreatingTask:false,
    newTaskAttachments: [], selectedThread:undefined, modelSettingsCache:new Map(),
    threads:[], threadDrafts:new Map(), threadAttachments:new Map(),
    selectedThreadLastTurnStatus:'completed', desktopDispatchState:undefined,
    elements:{newTaskMessage:{value:'first message'},newTaskError:{},composerState:{},
      newTaskDialog:{close(){}}},
    window:{setTimeout(){return 1;},clearTimeout(){}},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    localStorage:{setItem(){}},SELECTED_THREAD_KEY:'selected',
    authorizationHeaders:()=>({}),handleUnauthorized(){},
    updateNewTaskControls(){},renderProjectGroups(){},renderNewTaskAttachments(){},
    loadThreads:async()=>{},openThread:async(id)=>opened.push(id),
    setDeviceState(){},updateComposerState(){},refreshStatus(){},
    nativeDeliveryResponse:async(url,options)=>{
      calls.push(JSON.parse(options.body));
      return reply(calls.length, calls.at(-1));
    },
  });
  vm.runInContext(between('function nativeMessageIdentity(', 'async function nativeDeliveryResponse('),context);
  vm.runInContext(between('async function createNewTask()', 'function renderProjectGroups()'),context);
  return {context,storage,calls,opened,run:code=>vm.runInContext(code,context)};
}

const createdReceipt = (duplicate=false) => ({response:{ok:true,status:202},result:{
  ok:true,mode:'desktop',thread:{id:'new-thread',title:'first message'},
  desktop:{ok:true,confirmedBy:'threadHistory',duplicateRequest:duplicate},
}});

test('creation timeout preserves draft and ID; reconciliation does not fake a new run', async () => {
  const h=creationHarness((n)=>{if(n===1)throw Error('timeout');return createdReceipt(true);});
  await h.run('createNewTask()');
  assert.equal(h.context.elements.newTaskMessage.value,'first message');
  assert.match(h.context.elements.newTaskError.textContent,/回执超时/);
  assert.equal(h.context.isCreatingTask,false);
  assert.ok(h.storage.has('pocket-native-delivery:create'));
  await h.run('createNewTask()');
  assert.equal(h.calls[0].requestId,h.calls[1].requestId);
  assert.equal(h.context.selectedThreadLastTurnStatus,'completed');
  assert.equal(h.context.desktopDispatchState,undefined);
  assert.deepEqual(h.opened,['new-thread']);
  assert.equal(h.storage.size,0);
});

test('partial create transfers uncertain send ID and draft to the known thread', async () => {
  const h=creationHarness(()=>({response:{ok:false,status:409},result:{
    ok:false,error:'native_delivery_uncertain',retryAllowed:false,
    threadCreated:true,threadId:'new-thread',thread:{id:'new-thread'},
  }}));
  await h.run('createNewTask()');
  assert.equal(h.context.threadDrafts.get('new-thread'),'first message');
  assert.equal(await h.run(`nativeDeliveryRequestId('new-thread','first message')`),h.calls[0].requestId);
  assert.equal(h.storage.has('pocket-native-delivery:create'),false);
  assert.deepEqual(h.opened,['new-thread']);
});

test('native creation without a delivery receipt keeps request ID and draft', async () => {
  const h=creationHarness(()=>({response:{ok:true,status:202},result:{ok:true,thread:{id:'new-thread'}}}));
  await h.run('createNewTask()');
  assert.equal(h.context.elements.newTaskMessage.value,'first message');
  assert.ok(h.storage.has('pocket-native-delivery:create'));
  assert.deepEqual(h.opened,[]);
});

test('partial file task carries attachments and original request ID into existing-task retry', async () => {
  const h=creationHarness(()=>({response:{ok:false,status:409},result:{
    ok:false,error:'native_delivery_uncertain',retryAllowed:false,
    threadCreated:true,threadId:'new-thread',thread:{id:'new-thread'},
  }}));
  h.context.newTaskAttachments = [{id:'file-a',name:'image.png'}];
  await h.run('createNewTask()');
  assert.equal(h.context.threadAttachments.get('new-thread')[0].id,'file-a');
  assert.equal(h.context.newTaskAttachments.length,0);
  assert.equal(await h.run(`nativeDeliveryRequestId('new-thread',nativeMessageIdentity('first message',threadAttachments.get('new-thread')))`),h.calls[0].requestId);
});

test('disabled native creation does not call the server', async () => {
  const h=creationHarness(()=>createdReceipt());
  h.context.bridgeCapabilities.newTasks=false;
  await h.run('createNewTask()');
  assert.equal(h.calls.length,0);
});

test('native request ID survives refresh without storing prompt text', async () => {
  const storage = new Map();
  const context = vm.createContext({crypto: require('node:crypto').webcrypto, TextEncoder,
    sessionStorage: {getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}});
  vm.runInContext(between('async function nativeDeliveryRequestId(', 'async function startManagedTurn('), context);
  const first = await vm.runInContext(`nativeDeliveryRequestId('thread','private text')`,context);
  const again = await vm.runInContext(`nativeDeliveryRequestId('thread','private text')`,context);
  assert.equal(first,again);
  assert.notEqual(first,await vm.runInContext(`nativeDeliveryRequestId('thread','different')`,context));
  assert.ok(!JSON.stringify([...storage.values()]).includes('private text'));
});

test('native preview does not offer unsupported paused-task resume', () => {
  const h = harness();
  h.context.selectedThreadIsPaused = () => true;
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,desktopControl:false});updateComposerState();`);
  assert.equal(h.nodes.get('composerActionButton').disabled, true);
  assert.match(h.nodes.get('composerState').textContent, /暂不支持恢复/);
});

test('native resume needs live capability, paused turn ID and empty composer', () => {
  const h=harness();h.context.selectedThreadIsPaused=()=>true;
  h.context.composerHasContent=()=>false;h.context.selectedThreadLastTurnId='paused-turn';
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,nativeResume:true});updateComposerState();`);
  assert.equal(h.nodes.get('composerActionButton').disabled,false);
  h.context.composerHasContent=()=>true;h.run('updateComposerState()');
  assert.equal(h.nodes.get('composerActionButton').disabled,true);
  h.context.composerHasContent=()=>false;h.context.selectedThreadLastTurnId='';h.run('updateComposerState()');
  assert.equal(h.nodes.get('composerActionButton').disabled,true);
});

function sendHarness() {
  const h = harness();
  let resolveResponse;
  const timers = [];
  Object.assign(h.context, {
    fetch: () => new Promise(resolve => { resolveResponse = resolve; }),
    authorizationHeaders: () => ({}), resizeComposer() {}, renderComposerAttachments() {},
    selectedThreadLastTurnId: 'old-turn',
    threadDrafts: new Map([['a','message A'],['b','draft B']]),
    threadAttachments: new Map(), threadHistoryCache: new Map(),
    window: {setTimeout: callback => timers.push(callback)},
    setDeviceState() {}, refreshStatus() {}, openThread() { throw Error('Unexpected navigation'); },
  });
  h.context.elements.composerInput.value = 'message A';
  h.context.elements.managedLiveHistory.replaceChildren = () => {};
  h.run(between('async function startManagedTurn(', 'function sendManagedMessage('));
  return {...h, timers, resolve: result => resolveResponse({status:200,ok:true,json:async()=>result})};
}

test('late send response does not clear or mark another selected task active', async () => {
  const h = sendHarness();
  const pending = h.run('startManagedTurn()');
  h.context.selectedThread = {id:'b',title:'B'};
  h.context.elements.composerInput.value = 'draft B';
  h.resolve({mode:'desktop',desktop:{ok:true,taskTitle:'A'}});
  await pending;
  assert.equal(h.context.elements.composerInput.value,'draft B');
  assert.equal(h.context.threadDrafts.get('b'),'draft B');
  assert.equal(h.context.threadDrafts.has('a'),false);
  assert.equal(h.timers.length,0);
});

test('delayed history refresh does not navigate back after switching tasks', async () => {
  const h = sendHarness();
  const pending = h.run('startManagedTurn()');
  h.resolve({mode:'desktop',desktop:{ok:true,taskTitle:'A'}});
  await pending;
  h.context.selectedThread = {id:'b',title:'B'};
  for (const callback of h.timers) callback();
});

for (const stall of ['headers', 'body']) {
  test(`native delivery deadline also bounds stalled ${stall} without retry`, async () => {
    let expire, signal, requests=0, cleared=false;
    const context=vm.createContext({AbortController,
      window:{setTimeout:fn=>{expire=fn;return 1;},clearTimeout:()=>{cleared=true;}},
      fetch:async (_, options)=>{
        requests++;signal=options.signal;
        if(stall==='headers') return new Promise(()=>{});
        return {status:202,ok:true,json:()=>new Promise(()=>{})};
      },
    });
    vm.runInContext(between('async function nativeDeliveryResponse(', 'async function startManagedTurn('),context);
    const pending=vm.runInContext(`nativeDeliveryResponse('/test',{method:'POST'})`,context);
    await Promise.resolve();
    expire();
    await assert.rejects(pending,/native_delivery_confirmation_timeout/);
    assert.equal(signal.aborted,true);
    assert.equal(requests,1);
    assert.equal(cleared,true);
  });
}

test('native timeout releases composer, keeps draft and request ID, and only refreshes history', async () => {
  const h=sendHarness();
  let calls=0;const refreshes=[];
  Object.assign(h.context,{
    nativeDeliveryRequestId:async ()=>'existing-request-id',
    nativeDeliveryResponse:async ()=>{calls++;throw Error('native_delivery_confirmation_timeout');},
    sessionStorage:{removeItem(){throw Error('Must preserve request ID');}},
    openThread:(id, options)=>refreshes.push({id,options}),
  });
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,desktopControl:false});`);
  await h.run('startManagedTurn()');
  assert.equal(h.run('isSendingMessage'),false);
  assert.equal(h.context.elements.composerInput.value,'message A');
  assert.equal(h.context.threadDrafts.get('a'),'message A');
  assert.match(h.context.elements.composerState.textContent,/结果待确认/);
  for(const callback of h.timers)callback();
  assert.equal(refreshes.length,1);
  assert.equal(refreshes[0].id,'a');
  assert.equal(refreshes[0].options.scroll,false);
  assert.equal(calls,1);
});

function receiptHarness(replies, capability = true) {
  const calls = [], pending = [];
  const context = vm.createContext({AbortController,
    bridgeCapabilities: {nativeReceiptPolling:capability},
    window: {
      setTimeout(fn, ms) { if(ms <= 5000) {queueMicrotask(fn);return null;} return setTimeout(fn, ms); },
      clearTimeout,
    },
    fetch: async (url, options) => {
      calls.push({url,body:JSON.parse(options.body)});
      const reply = replies[Math.min(calls.length-1,replies.length-1)];
      if(reply instanceof Error) throw reply;
      return {status:reply.status,ok:reply.status<300,json:async()=>structuredClone(reply.body)};
    },
    notify:()=>pending.push(true),
  });
  vm.runInContext(between('async function nativeDeliveryResponse(', 'async function startManagedTurn('),context);
  return {calls,pending,run:(url='/api/codex/threads/a/turn')=>vm.runInContext(
    `nativeDeliveryResponse(${JSON.stringify(url)},{method:'POST',body:JSON.stringify({requestId:'stable-id',message:'hello',attachmentIds:['file']})},45000,notify)`,context)};
}

const latePending = {status:409,body:{ok:false,error:'native_delivery_uncertain',retryAllowed:false}};
const lateConfirmed = {status:202,body:{ok:true,mode:'desktop',desktop:{ok:true,confirmedBy:'threadHistory',duplicateRequest:true}}};

test('late receipt automatically polls without ordinary redelivery and keeps full identity', async () => {
  const h=receiptHarness([latePending,latePending,lateConfirmed]);
  const result=await h.run();
  assert.equal(h.calls.length,3);
  assert.equal(h.calls[0].body.receiptOnly,undefined);
  for(const call of h.calls.slice(1)) {
    assert.equal(call.body.receiptOnly,true);
    assert.equal(call.body.requestId,'stable-id');
    assert.deepEqual(call.body.attachmentIds,['file']);
    assert.equal(call.body.message,'hello');
  }
  assert.equal(result.result.desktop.receiptPolled,true);
  assert.equal(h.pending.length,2);
});

test('lost creation response only polls existing claim and never repeats allocation', async () => {
  const h=receiptHarness([Error('network disconnected'),lateConfirmed]);
  const result=await h.run('/api/codex/threads');
  assert.equal(result.result.ok,true);
  assert.equal(h.calls.length,2);
  assert.equal(h.calls[1].body.receiptOnly,true);
});

test('receipt polling stops on auth refusal and never polls older servers or Resume', async () => {
  const auth=receiptHarness([latePending,{status:401,body:null}]);
  assert.equal((await auth.run()).response.status,401);
  assert.equal(auth.calls.length,2);
  const old=receiptHarness([latePending],false);
  await old.run();assert.equal(old.calls.length,1);
  const resume=receiptHarness([latePending]);
  await resume.run('/api/codex/threads/a/continue');assert.equal(resume.calls.length,1);
});

test('receipt polling is bounded and retains unresolved outcome', async () => {
  const h=receiptHarness([latePending]);
  assert.equal((await h.run()).result.error,'native_delivery_uncertain');
  assert.equal(h.calls.length,9);
  assert.equal(h.calls.filter(c=>!c.body.receiptOnly).length,1);
});

test('automatic late confirmation clears submitted draft but does not invent running state', async () => {
  const h=sendHarness();
  Object.assign(h.context,{
    nativeDeliveryRequestId:async ()=>'request-id',
    sessionStorage:{removeItem(){}},
    nativeDeliveryResponse:async()=>({response:{ok:true,status:202},result:{ok:true,mode:'desktop',desktop:{
      ok:true,confirmedBy:'threadHistory',duplicateRequest:true,receiptPolled:true,
    }}}),
    openThread(){},
  });
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,nativeReceiptPolling:true});`);
  await h.run('startManagedTurn()');
  assert.equal(h.context.elements.composerInput.value,'');
  assert.equal(h.context.threadDrafts.has('a'),false);
  assert.equal(h.run('typeof desktopDispatchState'),'undefined');
});

test('old receipt keeps draft and does not fake a new run; next explicit send uses a new ID', async () => {
  const h=sendHarness();
  const storage=new Map();const requestIds=[];
  Object.assign(h.context,{
    crypto:require('node:crypto').webcrypto,TextEncoder,
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    nativeDeliveryResponse:async (_,options)=>{
      requestIds.push(JSON.parse(options.body).requestId);
      return {response:{ok:true,status:202},result:{ok:true,mode:'desktop',desktop:{
        ok:true,confirmedBy:'threadHistory',duplicateRequest:requestIds.length===1,
      }}};
    },
    openThread() {},
  });
  h.run(between('async function nativeDeliveryRequestId(', 'async function nativeDeliveryResponse('));
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,desktopControl:false});`);
  const oldId=await h.run(`nativeDeliveryRequestId('a','message A')`);
  await h.run('startManagedTurn()');
  assert.equal(requestIds[0],oldId);
  assert.equal(h.context.elements.composerInput.value,'message A');
  assert.equal(h.context.threadDrafts.get('a'),'message A');
  assert.equal(h.context.selectedThreadLastTurnId,'old-turn');
  assert.equal(h.run('typeof desktopDispatchState'),'undefined');
  h.run('updateComposerState()');
  assert.match(h.context.elements.composerState.textContent,/本次没有再次发送/);
  assert.equal(storage.size,0);
  assert.equal(requestIds.length,1);
  await h.run('startManagedTurn()');
  assert.notEqual(requestIds[1],oldId);
  assert.equal(h.context.elements.composerInput.value,'');
  assert.equal(h.context.nativeDeliveryNotices.size,0);
});

test('native HTTP success without verified receipt cannot erase draft', async () => {
  const h=sendHarness();
  Object.assign(h.context,{
    nativeDeliveryRequestId:async ()=>'request-id',
    nativeDeliveryResponse:async ()=>({response:{ok:true,status:202},result:{ok:true}}),
    sessionStorage:{removeItem(){throw Error('Unverified receipt must retain request ID');}},
  });
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,desktopControl:false});`);
  await h.run('startManagedTurn()');
  assert.equal(h.context.elements.composerInput.value,'message A');
  assert.equal(h.context.threadDrafts.get('a'),'message A');
  assert.equal(h.run('isSendingMessage'),false);
});

test('native continue binds paused turn and sends no synthetic text', async () => {
  const h=sendHarness();let request;
  h.context.elements.composerInput.value='';h.context.selectedThreadIsPaused=()=>true;
  Object.assign(h.context,{
    nativeDeliveryRequestId:async (_,message)=>{assert.equal(message,'resume:old-turn');return 'resume-id';},
    nativeDeliveryResponse:async (url,options)=>{
      request={url,body:JSON.parse(options.body)};
      return {response:{ok:true,status:202},result:{ok:true,mode:'desktop',desktop:{ok:true,confirmedBy:'threadHistory'}}};
    },
    sessionStorage:{removeItem(){}},
  });
  h.run(`applyBridgeCapabilities({platform:'windows',nativeTextSend:true,nativeResume:true});`);
  await h.run('startManagedTurn({continueOnly:true})');
  assert.equal(request.url,'/api/codex/threads/a/continue');
  assert.deepEqual(request.body,{requestId:'resume-id',expectedTurnId:'old-turn'});
  assert.equal(h.run('isSendingMessage'),false);
});

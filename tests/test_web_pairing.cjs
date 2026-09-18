const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
function harness() {
  let calls = 0;
  const context = vm.createContext({
    deviceToken:'',legacyToken:'',pairingTicket:'pair1.test',pairingConfirmed:false,
    enrollmentPromise:undefined,pairingDialogDismissed:false,
    defaultDeviceName:()=> 'phone',
    sessionStorage:{removeItem(){}},localStorage:{setItem(){}},
    LEGACY_TOKEN_KEY:'legacy',PAIRING_TICKET_KEY:'pair',DEVICE_TOKEN_KEY:'device',
    fetch:async()=>{calls++;return {ok:true,json:async()=>({device:{deviceToken:'paired'}})}},
    showTokenDialog:message=>{context.errorMessage=message;},
  });
  vm.runInContext(source.slice(source.indexOf('function clearBootstrapCredentials()'),source.indexOf('function handleUnauthorized()')),context);
  return {context,calls:()=>calls,run:code=>vm.runInContext(code,context)};
}
test('opening a pairing URL does not consume a ticket before explicit confirmation',async()=>{
  const h=harness();
  assert.equal(await h.run('enrollDevice()'),false);
  assert.equal(h.calls(),0);
});
test('confirmation enrolls once across concurrent refreshes',async()=>{
  const h=harness();h.context.pairingConfirmed=true;
  await h.run('Promise.all([enrollDevice(),enrollDevice()])');
  assert.equal(h.calls(),1);assert.equal(h.context.deviceToken,'paired');
});
test('already paired devices do not consume incoming tickets',async()=>{
  const h=harness();h.context.deviceToken='existing';
  assert.equal(await h.run('enrollDevice()'),true);assert.equal(h.calls(),0);
});
test('used tickets report a useful error without automatic retry',async()=>{
  const h=harness();h.context.pairingConfirmed=true;
  h.context.fetch=async()=>({ok:false,status:401,json:async()=>({pairingStatus:'used'})});
  await h.run('Promise.all([enrollDevice(),enrollDevice()])');
  assert.match(h.context.errorMessage,/已被使用/);
  assert.equal(h.context.pairingConfirmed,false);assert.equal(h.context.pairingTicket,'');
});
test('network failure retains ticket but requires another explicit confirmation',async()=>{
  const h=harness();h.context.pairingConfirmed=true;
  h.context.fetch=async()=>{throw Error('offline')};
  assert.equal(await h.run('enrollDevice()'),false);
  assert.equal(h.context.pairingTicket,'pair1.test');assert.equal(h.context.pairingConfirmed,false);
});

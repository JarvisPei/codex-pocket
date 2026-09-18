"use strict";
const capability = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const statusNode = document.querySelector('#status');
const qrNode = document.querySelector('#qr');
const renew = document.querySelector('#renew');
const code = document.querySelector('#code');
const copy = document.querySelector('#copy');
let expiresAt = 0;
let active = false;
let busy = false;
let checking = false;
let generation = 0;
function erase() { active = false; qrNode.hidden = true; code.textContent = ''; copy.disabled = true; }
function show(text, error = false) { statusNode.textContent = text; statusNode.dataset.error = String(error); }
async function request(path) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Pocket-Pairing':capability},body:'{}'});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error);
    error.bridgePort = result.bridgePort;
    throw error;
  }
  return result;
}
function paint(matrix) {
  const scale = Math.max(1, Math.floor(320 / (matrix.length + 8)));
  qrNode.width = qrNode.height = (matrix.length + 8) * scale;
  const context = qrNode.getContext('2d');
  context.fillStyle = '#fff'; context.fillRect(0,0,qrNode.width,qrNode.height);
  context.fillStyle = '#000';
  matrix.forEach((row,y) => row.forEach((dark,x) => {if(dark)context.fillRect((x+4)*scale,(y+4)*scale,scale,scale);}));
  qrNode.hidden = false;
}
function reportError(error) {
  if (error.message === 'tailscale_not_ready') {
    document.querySelector('#setup').hidden = false;
    const port = Number.isInteger(error.bridgePort) && error.bridgePort > 0 && error.bridgePort <= 65535 ? error.bridgePort : 4317;
    document.querySelector('#serveCommand').textContent = `tailscale serve --bg http://127.0.0.1:${port}`;
    renew.textContent = '已配置，重新检测';
    show('先完成 Tailscale Serve 配置，再生成二维码。', true);
    return;
  }
  show(error.message === 'bridge_update_required' ? '请先更新并重新启动 Bridge 服务，再生成二维码（无需重启 Codex）。' :
    error.message === 'local_authorization_required' ? '本机配对页面授权已失效，请重新运行配对命令打开页面。' :
    '暂时无法连接本机 Bridge。请确认服务正在运行，再点击重新生成。', true);
}
async function generate() {
  if (busy) return;
  busy = true; renew.disabled = true; erase(); generation++;
  document.querySelector('#setup').hidden = true;
  show('正在生成新的二维码…');
  try {
    const data = await request('/new');
    document.querySelector('#origin').textContent = data.origin;
    const address = document.querySelector('#address'); address.textContent = data.origin; address.href = data.origin;
    code.textContent = data.ticket; copy.disabled = false;
    expiresAt = Date.now() + data.expiresIn * 1000; active = true;
    paint(data.matrix); countdown();
    renew.textContent = '重新生成二维码';
  } catch(error) { reportError(error); }
  finally { busy = false; renew.disabled = false; }
}
function countdown() {
  if (!active) return;
  const seconds = Math.max(0,Math.ceil((expiresAt-Date.now())/1000));
  if (!seconds) { erase(); show('二维码已过期，请点击重新生成。',true); return; }
  show(`等待手机确认 · ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')} 后过期`);
}
async function check() {
  if (!active || busy || checking) return;
  checking = true; const current = generation;
  try {
    const data = await request('/status');
    if (current !== generation) return;
    if (data.status === 'used') { erase(); show('配对码已使用。请在手机确认是否连接成功；如需连接另一台设备，请重新生成。'); }
    else if (data.status !== 'valid') { erase(); show('二维码已过期或失效，请重新生成。',true); }
  } catch(error) { if(current === generation) { erase(); reportError(error); } }
  finally { checking = false; }
}
renew.addEventListener('click',generate);
copy.addEventListener('click',async()=>{
  try { await navigator.clipboard.writeText(code.textContent); copy.textContent='已复制'; }
  catch { show('无法自动复制，请展开后手动选择配对码。',true); }
});
if (capability) generate(); else { renew.disabled=true; show('请在电脑上运行配对命令打开此页面。',true); }
const clock = setInterval(countdown,1000);
const poll = setInterval(check,2000);
window.addEventListener('pagehide',()=>{clearInterval(clock);clearInterval(poll);});

// DIAL discovery and YouTube Lounge control; media stays in the native TV player.
import YouTubeCastReceiver, {Player, Constants} from 'yt-cast-receiver';
import readline from 'node:readline';
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';

const write = value => process.stdout.write(JSON.stringify(value)+'\n');
// The proof service stays on loopback; never publish its unauthenticated port.
const proofPath='/opt/youtube-proof/build/main.js';
const proof=existsSync(proofPath) ? spawn(process.execPath,[proofPath,'--host','127.0.0.1'],
  {cwd:'/opt/youtube-proof',stdio:'ignore'}) : null;
proof?.on('error',()=>write({event:'error'}));
process.on('exit',()=>{try {proof?.kill();} catch {}});
const pending = new Map();
let sequence = 0;
function request(command, args = {}) {
  return new Promise(resolve => {
    const id = ++sequence;
    const timer = setTimeout(() => {pending.delete(id);resolve(false);}, 12000);
    pending.set(id, value => {clearTimeout(timer);resolve(value);});
    write({id, command, ...args});
  });
}
class NativePlayer extends Player {
  doPlay(video, position) {return request('play', {video: video.id, position});}
  doPause() {return request('pause');}
  doResume() {return request('resume');}
  doStop() {return request('stop');}
  doSeek(position) {return request('seek', {position});}
  doSetVolume(volume) {return request('volume', {volume});}
  async doGetVolume() {const s=await request('status');return s?.volume || {level:100,muted:false};}
  async doGetPosition() {return (await request('status'))?.position || 0;}
  async doGetDuration() {return (await request('status'))?.duration || 0;}
}
const player = new NativePlayer();
const receiver = new YouTubeCastReceiver(player, {
  device: {name:'IPTV thuis',screenName:'IPTV thuis',brand:'IPTV thuis',model:'YouTube TV'},
  app: {resetPlayerOnDisconnectPolicy: Constants.RESET_PLAYER_ON_DISCONNECT_POLICIES.ALL_EXPLICITLY_DISCONNECTED},
  dial: {port:8008,prefix:'/youtube'},
  logLevel: Constants.LOG_LEVELS.NONE
});
const pairing = receiver.getPairingCodeRequestService();
pairing.on('response', code => write({event:'pairing',code:String(code)}));
pairing.on('error', () => write({event:'pairing-error'}));
let previous = null;
let polling = false;
const input = readline.createInterface({input:process.stdin});
input.on('line', line => {
  try {
    const message=JSON.parse(line);
    if(message.id && pending.has(message.id)) {pending.get(message.id)(message.result);pending.delete(message.id);}
    if(message.command==='pair') {pairing.stop();pairing.start();}
  } catch { /* Ignore malformed local messages. */ }
});
let stopping=false;
async function shutdown() {
  if(stopping)return;stopping=true;
  pairing.stop();
  try {await receiver.stop();} catch {}
  process.exit(0);
}
input.on('close',shutdown);
process.on('SIGTERM',shutdown);
process.on('SIGINT',shutdown);
process.on('uncaughtException',()=>{write({event:'error'});process.exit(1);});
process.on('unhandledRejection',()=>write({event:'error'}));
try {
  await receiver.start();
  write({event:'ready'});
  setInterval(async () => {
    if(polling || stopping)return;polling=true;
    try {
      const state=await request('status');
      if(!state)return;
      if(state.status==='eof' && previous!=='eof') {
        previous='eof';await player.pause();await player.next();
      } else if(state.status!==previous) {
        previous=state.status;
        const status={playing:Constants.PLAYER_STATUSES.PLAYING,paused:Constants.PLAYER_STATUSES.PAUSED,
          loading:Constants.PLAYER_STATUSES.LOADING,stopped:Constants.PLAYER_STATUSES.STOPPED}[state.status];
        if(status!==undefined)await player.notifyExternalStateChange(status);
      }
    } catch {write({event:'error'});} finally {polling=false;}
  },1000);
} catch {write({event:'error'});process.exit(1);}

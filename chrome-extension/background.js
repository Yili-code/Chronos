// Explicitly enabled read-only bulletin collection. No video or download calls.
const COURSES = ['189684','189687','189717','192072','189756','188571','193842'];
const HOURS = [8,12,18,22];
const PREFIX = 'chronos-bulletins-';
const ENDPOINT = 'http://127.0.0.1:8765/v1/browser-announcements';
let running = false;

function nextSlot(hour, now=Date.now()) {
  const local = new Date(now + 8*3600000);
  let due = Date.UTC(local.getUTCFullYear(),local.getUTCMonth(),local.getUTCDate(),hour)-8*3600000;
  if (due <= now) due += 86400000;
  return due;
}
async function post(payload) {
  const response = await fetch(ENDPOINT, {method:'POST',
    headers:{'Content-Type':'application/json','X-Chronos-Bridge':'1'},
    body:JSON.stringify(payload), signal:AbortSignal.timeout(5000)});
  if (!response.ok) throw new Error('local_handoff_unavailable');
  return response.json();
}
async function collectCourse(api, course, enabled) {
  if (!COURSES.includes(course)) throw new Error('unsupported_course');
  // Check receiver first: an offline companion must not open seven useless tabs.
  await post({status:'unknown',complete_course:false,announcements:[]});
  if (!await enabled()) return 'stopped';
  const url=`https://tronclass.ntou.edu.tw/course/${course}/bulletin`;
  const tab=await api.tabs.create({url,active:false});
  const cleanup=PREFIX+'cleanup-'+tab.id;
  await api.alarms.create(cleanup,{delayInMinutes:1});
  try {
    for (let attempt=0;attempt<20;attempt++) {
      if (!await enabled()) return 'stopped';
      const current=await api.tabs.get(tab.id);
      if (current.url?.startsWith('https://tccas.ntou.edu.tw/cas/login')) return 'reauth_required';
      if (current.url && current.url.split('#')[0] !== url) return 'unknown';
      try {
        const payload=await api.tabs.sendMessage(tab.id,{type:'chronos.observe_announcements'});
        if (payload?.status==='observed_partial') {
          const receipt=await post(payload);
          return receipt.status==='observed_partial' ? 'partial' : 'unknown';
        }
      } catch { /* loading content script is not proof of an empty course */ }
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    return 'unknown';
  } finally {
    try {
      const current=await api.tabs.get(tab.id);
      // Never close a tab that the owner took over or navigated elsewhere.
      if (!current.active && (current.url?.split('#')[0]===url ||
          current.url?.startsWith('https://tccas.ntou.edu.tw/cas/login')))
        await api.tabs.remove(tab.id);
    } catch { /* tab already closed */ }
    await api.alarms.clear(cleanup);
  }
}
if (typeof module!=='undefined') module.exports={nextSlot,collectCourse};
if (typeof chrome!=='undefined') {
  const enabled=async()=>Boolean(await chrome.alarms.get(PREFIX+'8'));
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if (sender.id!==chrome.runtime.id || sender.tab || !['chronos.monitor_start','chronos.monitor_stop','chronos.monitor_status'].includes(message?.type)) return false;
    (async()=>{
      if(message.type==='chronos.monitor_start') {
        for(const hour of HOURS) await chrome.alarms.create(PREFIX+hour,{when:nextSlot(hour),periodInMinutes:1440});
      }
      if(message.type==='chronos.monitor_stop') {
        for(const hour of HOURS) await chrome.alarms.clear(PREFIX+hour);
      }
      reply({enabled:await enabled()});
    })().catch(()=>reply({error:'monitor_unavailable'}));
    return true;
  });
  chrome.alarms.onAlarm.addListener(async alarm=>{
    if(alarm.name.startsWith(PREFIX+'cleanup-')) {
      const id=Number(alarm.name.slice((PREFIX+'cleanup-').length));
      try {
        const tab=await chrome.tabs.get(id);
        const url=new URL(tab.url);
        if(!tab.active && url.origin==='https://tronclass.ntou.edu.tw' &&
            COURSES.some(course=>url.pathname===`/course/${course}/bulletin`)) await chrome.tabs.remove(id);
      } catch { /* no secrets or raw errors logged */ }
      return;
    }
    if(!HOURS.some(hour=>alarm.name===PREFIX+hour) || running || !await enabled()) return;
    running=true;
    try {
      for(const course of COURSES) {
        const result=await collectCourse(chrome,course,enabled);
        if(result==='reauth_required' || result==='stopped') break;
      }
    } catch { /* companion unavailable: next scheduled run can try again */ }
    finally { running=false; }
  });
}

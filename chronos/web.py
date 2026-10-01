PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Chronos</title>
  <style>
    :root{color-scheme:dark;--bg:#0b0d10;--panel:#14181d;--line:#252b33;--text:#e8ecf0;--muted:#89939e;--accent:#89a7c2}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 ui-sans-serif,system-ui,sans-serif}
    main{max-width:680px;margin:0 auto;padding:48px 24px}header{display:flex;align-items:end;justify-content:space-between;margin-bottom:30px}
    h1{font:500 30px/1.1 Georgia,serif;letter-spacing:.03em;margin:0}small,.muted{color:var(--muted)}
    .panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}
    h2{font-size:13px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);margin:0 0 16px}
    form{display:flex;gap:8px;margin-bottom:14px}input{flex:1;background:#0e1115;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
    button{background:var(--accent);border:0;border-radius:8px;padding:10px 14px;color:#0b0d10;font-weight:650;cursor:pointer}
    .item{padding:12px 0;border-top:1px solid var(--line)}.item:first-child{border-top:0}.row{display:flex;justify-content:space-between;gap:12px}
    .tag{color:var(--accent);font-size:12px}.done{background:transparent;color:var(--muted);border:1px solid var(--line);padding:5px 8px}
    @media(max-width:720px){main{padding:30px 16px}}
  </style>
</head>
<body><main><header><div><h1>Chronos</h1><small>Task command center</small></div><small id="clock"></small></header>
<section class="panel"><h2>Open tasks</h2><form id="task-form"><input id="task" placeholder="e.g. Finish the report tomorrow at 17:00 #Chronos" autocomplete="off"><button>Add</button></form><div id="tasks"></div></section></main>
<script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const api=async(path,opt={})=>{const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opt});if(!r.ok)throw Error(await r.text());return r.json()};
async function loadTasks(){const xs=await api('/api/tasks');document.querySelector('#tasks').innerHTML=xs.length?xs.map(x=>{const meta=[x.due_at?new Date(x.due_at).toLocaleString('en-GB',{timeZone:'Asia/Taipei'}):'',x.project?'#'+esc(x.project):''].filter(Boolean).join(' · ');return `<div class="item row"><div>${esc(x.title)}${meta?`<div class="tag">${meta}</div>`:''}</div><button class="done" onclick="completeTask(${x.id})">Complete</button></div>`}).join(''):'<div class="muted">No open tasks.</div>'}
async function completeTask(id){await api('/api/tasks/'+id+'/complete',{method:'POST'});loadTasks()}
document.querySelector('#task-form').onsubmit=async e=>{e.preventDefault();const input=document.querySelector('#task');const button=e.target.querySelector('button');if(button.disabled||!input.value.trim())return;button.disabled=true;try{await api('/api/tasks/natural',{method:'POST',body:JSON.stringify({text:input.value})});input.value='';await loadTasks()}catch(error){let message=error.message;try{message=JSON.parse(message).detail||message}catch{}alert(message)}finally{button.disabled=false}};
setInterval(()=>document.querySelector('#clock').textContent=new Date().toLocaleString('en-GB',{timeZone:'Asia/Taipei'}),1000);loadTasks();
</script></body></html>"""

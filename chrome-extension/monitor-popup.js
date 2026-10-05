const monitorState=document.getElementById('monitor-state');
function monitorCommand(type) {
  chrome.runtime.sendMessage({type},result=>{
    monitorState.textContent=chrome.runtime.lastError || result?.error ? '無法確認監測狀態。' :
      result?.enabled ? '背景公告監測已啟用：08、12、18、22 點；需 Chrome 與本機接收程式運作。其他資料類型尚未自動收集。' : '背景監測已停止。';
  });
}
document.getElementById('monitor-start').addEventListener('click',()=>monitorCommand('chronos.monitor_start'));
document.getElementById('monitor-stop').addEventListener('click',()=>monitorCommand('chronos.monitor_stop'));
monitorCommand('chronos.monitor_status');

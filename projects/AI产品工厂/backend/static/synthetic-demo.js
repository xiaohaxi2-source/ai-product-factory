const $=id=>document.getElementById(id);
const interview=Number(new URLSearchParams(location.search).get('interview'));
let current=null,watching=false;
async function api(path,body){const response=await fetch('/api/v1'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(data.error?.message||'请求失败，请稍后查看记录。');return data;}
function render(value){current=value;$('question').textContent=value.question;$('result').hidden=value.status!=='passed';
if(value.status==='passed'){$('reply').textContent=JSON.stringify(value.reply,null,2);$('usage').textContent=`输入${value.tokens_in} token / 输出${value.tokens_out} token · 耗时${value.duration_ms}毫秒 · 费用上界${(value.cost_upper_micro_rmb/1000000).toFixed(6)}元（实际以账单为准）`;$('status').textContent='联调成功：已收到服务真实返回。结果已保存，刷新不会丢失。';$('generate').textContent='查看本次真实结果';$('generate').disabled=false;}
else if(value.job_id){$('generate').disabled=true;$('status').textContent='正在连接服务，请稍候；可以刷新后继续查看。';}
else if(value.status!=='not_run'){$('generate').disabled=true;$('status').textContent='已有未通过或中断的请求记录，已停止重复发送，请先核对报告。';}
else{$('generate').disabled=!value.configured;$('status').textContent=value.configured?'密钥已在本地配置，可以发送固定虚构问题。':'本地模型密钥尚未配置；本页不会要求输入或显示密钥。';}}
async function load(){const value=await api(`/interviews/${interview}/synthetic-demo`);render(value);if(value.job_id&&!watching)await watch(value.job_id);}
async function watch(id){watching=true;try{for(;;){const job=await api('/jobs/'+id);if(['succeeded','failed','cancelled','interrupted'].includes(job.status)){await load();if(job.error)$('status').textContent=job.error.message;return;}await new Promise(r=>setTimeout(r,700));}}finally{watching=false;}}
$('generate').onclick=async()=>{if(current?.status==='passed'){$('result').scrollIntoView({behavior:'smooth'});return;}$('generate').disabled=true;$('status').textContent='正在提交固定虚构问题…';try{const job=await api(`/interviews/${interview}/synthetic-demo`,{accepted:true});await load();if(!watching)await watch(job.id);}catch(error){$('status').textContent=error.message;await load().catch(()=>{});}};
if(Number.isSafeInteger(interview)&&interview>0)load().catch(error=>{$('status').textContent=error.message;});else $('status').textContent='缺少有效访谈编号，请从工厂中的虚构数据联调入口打开。';

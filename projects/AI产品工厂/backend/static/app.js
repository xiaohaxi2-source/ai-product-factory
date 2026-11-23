'use strict';
const $=id=>document.getElementById(id);
const friendly=FactoryConversation.displayText;
let selected=null,activeStage=null,projectRows=[], detail=null, planning=null, watching=null, execution=null, executionSignature='', verification=null, checkSignature='';
const labels={collecting:'访谈中',draft_pending:'PRD 待确认',auditing:'清单待确认',closed:'PRD已确认'};
const terminal=s=>['succeeded','failed','interrupted','cancelled'].includes(s);
const el=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=friendly(text||'');if(cls)n.className=cls;return n;};
async function api(path,options={}){const r=await fetch('/api/v1'+path,options);const data=await r.json();if(!r.ok)throw new Error(data.error?.message||'请求失败');return data;}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});
function notify(text){$('notice').textContent=friendly(text||'');}
async function guard(fn){try{notify('');await fn();}catch(e){notify(e.message);}}
async function sessions(){projectRows=await api('/workspace');$('sessions').replaceChildren();$('project-grid').replaceChildren();
for(const row of projectRows){const b=el('button','','session'+(row.id===selected?' active':''));b.setAttribute('aria-label',row.name);b.title=row.name;const glyph=el('span',row.name.slice(0,1),'session-glyph');glyph.setAttribute('aria-hidden','true');b.append(glyph,el('span',row.name,'nav-label'));b.onclick=()=>guard(()=>load(row.id));$('sessions').append(b);
const card=el('button','','project-card');card.type='button';card.setAttribute('aria-label','打开项目 '+row.name);const top=el('div','','card-top');top.append(el('span','PRODUCT '+String(row.id).padStart(2,'0')),el('span',row.running?'● 正在运行':'本地项目'));card.append(top,el('h3',row.name),el('span',row.state,'badge'));const bottom=el('div','','card-bottom');bottom.append(el('span',row.files_count?'已保存 '+row.files_count+' 个代码文件':stageNames[row.stage]),el('span','打开工作台 →'));card.append(bottom);card.onclick=()=>guard(()=>load(row.id));$('project-grid').append(card);}
$('count-projects').textContent=projectRows.length;$('count-running').textContent=projectRows.filter(r=>r.running).length;$('count-code').textContent=projectRows.filter(r=>r.files_count>0).length;$('no-projects').hidden=!!projectRows.length;}
const stageNames={prd:'需求与PRD',plan:'方案与任务',code:'开发执行',check:'检查与交付'};
function home(){selected=null;activeStage=null;$('work').hidden=true;$('create-panel').hidden=true;$('home-view').hidden=false;$('breadcrumbs').textContent='工作台 / 项目总览';guard(sessions);}
function newProject(){home();$('home-view').hidden=true;$('create-panel').hidden=false;$('breadcrumbs').textContent='工作台 / 新建产品';$('product').focus();}
function currentStage(){if(detail?.interview.status!=='closed')return 'prd';if(!planning?.development_plan||planning.source_changed)return 'plan';return execution?.status==='generated'?'check':'code';}
function showStage(stage){
showRightTab('questions');$('right-panel').classList.add('mobile-open');
const target=$('panel-'+stage)||(stage==='plan'?$('planning'):null);
if(target){const review=target.closest('details');if(review)review.open=true;target.scrollIntoView({block:'nearest',behavior:'smooth'});}
}
function renderStages(){
$('breadcrumbs').textContent='工作台 / '+detail.interview.product_name;
$('code-empty').hidden=!$('execution-panel').hidden;$('check-empty').hidden=!$('verification-panel').hidden;
$('draft-button').hidden=detail.interview.status!=='collecting';$('review-prd').hidden=!detail.draft;$('review-plan').hidden=detail.interview.status!=='closed';$('review-code').hidden=!planning?.development_plan;
if(detail.interview.status==='draft_pending')$('review-prd').open=true;
}
let localFileSignature='',fileRefreshAt=0,productionPosition='';
async function loadArtifacts(id){
const listing=await api(`/interviews/${id}/files`);if(selected!==id)return;
renderLocalFiles(listing,id);
$('download-code').hidden=!execution?.revision;$('code-download-note').hidden=!execution?.revision;if(execution?.revision)$('download-code').href=`/api/v1/executions/${execution.id}/download`;
}
function renderProduction(job){
if(!detail)return;
const view=FactoryConversation.production(detail,planning,execution,verification,job||detail.jobs.find(j=>!terminal(j.status))||detail.jobs.at(-1));
const states={complete:'已完成',running:'进行中',current:'当前阶段',pending:'未开始',future:'尚未接入',attention:'待处理'};
$('production-steps').replaceChildren();for(const [i,step] of view.steps.entries()){const row=el('li','','production-step '+step.status);if(['running','current','attention'].includes(step.status))row.setAttribute('aria-current','step');row.append(el('span',step.status==='complete'?'✓':String(i+1),'step-number'),el('strong',step.name),el('small',states[step.status]));$('production-steps').append(row);}
$('production-summary').textContent=view.caption;$('production-current').textContent=view.steps[view.index].name+' · '+states[view.steps[view.index].status];
const position=selected+':'+view.index;if(position!==productionPosition){productionPosition=position;const list=$('production-steps'),step=list.children[view.index];list.scrollLeft+=step.getBoundingClientRect().left-list.getBoundingClientRect().left-(list.clientWidth-step.clientWidth)/2;}
}
async function copyLocalPath(path){try{await navigator.clipboard.writeText(path);notify('保存路径已复制，可在访达“前往文件夹”中粘贴打开。');}catch(e){notify('无法自动复制，请选中页面上的保存路径复制。');}}
function renderLocalFiles(listing,id){
const signature=JSON.stringify([id,listing]);if(signature===localFileSignature)return;localFileSignature=signature;
$('artifact-count').textContent=listing.files.length;$('file-count').textContent=listing.files.length;
$('artifact-list').replaceChildren();$('local-directories').replaceChildren();
for(const [label,path] of [['文档保存目录',listing.document_directory],['当前源码目录',listing.code_directory]]){if(!path)continue;const row=el('div','','local-directory');row.append(el('strong',label));const location=el('code');location.textContent=path;row.append(location);const copy=el('button','复制路径','quiet');copy.type='button';copy.onclick=()=>guard(()=>copyLocalPath(path));row.append(copy);$('local-directories').append(row);}
if(!listing.files.length){$('artifact-list').append(el('p','还没有已生成的文件。完成一个生成步骤后，这里会自动更新。','muted'));}
for(const item of listing.files){
const row=el('div','','saved-file');const name=el('strong');name.textContent=item.name;row.append(name,el('span',(item.kind==='code'?'源码':item.kind==='report'?'检查报告':'文档')+' · 已保存到本机 · '+new Date(item.updated_at).toLocaleTimeString('zh-CN'),'file-state'));const location=el('code');location.textContent=item.local_path;row.append(location);const actions=el('div','','file-actions');
if(item.artifact_id){const preview=el('button','预览');preview.type='button';preview.onclick=()=>guard(()=>previewDocument(item.artifact_id));const download=el('a','下载');download.href=item.download_url;actions.append(preview,download);}
const copy=el('button','复制路径');copy.type='button';copy.onclick=()=>guard(()=>copyLocalPath(item.local_path));actions.append(copy);row.append(actions);$('artifact-list').append(row);
}

}
function openFilePanel(){showRightTab('files');$('right-panel').classList.add('mobile-open');}
$('show-files').onclick=openFilePanel;
$('home-button').onclick=home;$('new-button').onclick=newProject;$('hero-new').onclick=newProject;$('cancel-create').onclick=home;$('refresh-projects').onclick=()=>guard(sessions);$('close-artifact').onclick=()=>$('artifact-dialog').close();

function textBlock(parent,text,cls){parent.append(el('div',text,cls));}
function renderDecisionReview(data){
const section=$('decision-review');section.replaceChildren();section.hidden=!data.pending_review;if(!data.pending_review)return;
const review=data.pending_review, inputs=[];
section.append(el('h3','补充待确认决策'),el('p','此前回答会保留并合并使用，只需补充缺少的部分。要改变已有决定，请明确写出新的决定。提示来自上次复核，重新生成后会更新。','muted'));
for(const d of review.decisions){
const card=el('form','','question');card.append(el('strong',d.number+' · '+d.topic),el('p',d.conclusion));
const history=el('div','','saved-decision-history');
function showHistory(values){history.replaceChildren();if(values?.length){history.append(el('strong','已保存的补充记录（按提交顺序）'));for(const value of values)history.append(el('p',value));}}
showHistory(d.history);card.append(history);
const input=el('textarea');input.id='decision-'+d.number;input.setAttribute('aria-label',d.number+' '+d.topic+'的补充答案');input.required=true;input.maxLength=4000;input.value=d.answer;input.placeholder=({D1:'填写首版支持的具体类别和项目名称',D4:'填写每月预算金额、超额怎么办；也可明确当前暂不启用付费调用',D9:'填写是否复用已有环境、无可复用环境时选择免费或另行确认；实际环境信息上线前核实'})[d.number]||'填写你的具体决定';
const status=el('span',d.answer?'已保存，待重新生成核对':'尚未补充','muted');status.setAttribute('role','status');
const save=el('button','保存此项');card.append(input,save,status);inputs.push({number:d.number,input,status});
card.onsubmit=e=>{e.preventDefault();guard(async()=>{save.disabled=true;try{const saved=await post(`/interviews/${data.interview.id}/decision-answers`,{review_id:review.review_id,answers:[{number:d.number,content:input.value}]});showHistory(saved.decisions.find(row=>row.number===d.number)?.history);status.textContent='已保存，将与此前回答合并用于终稿';}finally{save.disabled=false;}});};
input.oninput=()=>status.textContent='尚未保存';section.append(card);
}
const next=el('button','保存全部并重新生成终稿','primary');next.type='button';next.onclick=()=>guard(async()=>{
for(const row of inputs)if(!row.input.reportValidity())return;
for(const n of section.querySelectorAll('button'))n.disabled=true;
try{await post(`/interviews/${data.interview.id}/decision-answers`,{review_id:review.review_id,answers:inputs.map(row=>({number:row.number,content:row.input.value}))});await submit('finalize',{accepted:true});}finally{if(!detail.jobs.some(j=>!terminal(j.status)))for(const n of section.querySelectorAll('button'))n.disabled=false;}
});section.append(next);
}
async function load(id){
const switched=selected!==id;if(switched)activeStage=null;selected=id;executionSignature='';checkSignature='';localStorage.setItem('factory-interview',id);
const data=await api('/interviews/'+id);if(selected!==id)return;detail=data;
$('home-view').hidden=true;$('create-panel').hidden=true;$('work').hidden=false;$('name').textContent=friendly(data.interview.product_name);$('status').textContent=labels[data.interview.status];
$('draft').hidden=!data.draft;$('prd').textContent=friendly(data.draft?.content||'');$('confirm').hidden=data.interview.status!=='draft_pending';$('revision').hidden=data.interview.status!=='draft_pending';$('assumptions').replaceChildren();$('assumptions-help').hidden=data.interview.status!=='draft_pending'||!data.draft?.assumptions?.length;
for(const a of data.draft?.assumptions||[]){const card=el('div','','question');card.append(el('strong',a.gap),el('p','建议：'+a.default),el('p','理由：'+a.reason));$('assumptions').append(card);}
renderDecisionReview(data);$('finalize').hidden=data.interview.status!=='auditing'||!!data.pending_review;$('finalize').disabled=data.checklist.some(it=>!it.closed);$('result').replaceChildren();
await loadPlanning(id);await loadExecution(id);await loadVerification(id);await loadArtifacts(id);if(selected!==id)return;
const running=data.jobs.find(j=>!terminal(j.status));$('job-actions').replaceChildren();$('live-activity').hidden=!running;$('task').textContent=running?FactoryConversation.progress(running.action):'';
for(const n of document.querySelectorAll('#chat button,#draft-button,#finalize,#revision button,#confirm,#decision-review button,#decision-review textarea,#generate-technical,#technical-revision button,#confirm-technical,#execute-start,#execution-feedback,#verify-start,#check-fix,#accept-check,#check-feedback'))n.disabled=!!running||n.dataset.blocked==='true';
renderStages();renderProduction(running);renderConversation(data,running);renderClarifications(data,!!running);configureComposer(data,!!running,switched);
if(running){if(running.action!=='finalize'){const b=el('button','停止本次处理');b.onclick=()=>guard(async()=>{await post('/jobs/'+running.id+'/cancel');await load(id);});$('job-actions').append(b);}watch(running);}
await sessions();
}
async function watch(job){if(watching===job.id)return;watching=job.id;const id=job.interview_id;const source=new EventSource('/api/v1/jobs/'+job.id+'/events');source.addEventListener('chunk',e=>{if(selected===id){const p=JSON.parse(e.data);$('task').textContent=FactoryConversation.progress(job.action,p.phase)+(p.elapsed_seconds?' · 已用'+p.elapsed_seconds+'秒':'');}});let ended=false;try{while(!ended){const current=await api('/jobs/'+job.id);if(selected===id&&!terminal(current.status)){if(['execute','check_fix'].includes(current.action))await loadExecution(id);if(['verify','check_fix'].includes(current.action))await loadVerification(id);updateDevelopmentActivity(current);renderProduction(current);if(Date.now()-fileRefreshAt>1800){fileRefreshAt=Date.now();await loadArtifacts(id);}}if(terminal(current.status)){ended=true;if(selected===id){if(current.status==='succeeded')activeStage=null;await load(id);}else await sessions();break;}await new Promise(r=>setTimeout(r,900));}}catch(e){if(selected===id)notify(e.message);}finally{source.close();if(watching===job.id)watching=null;}}
async function submit(action,body,id=selected){const job=await post(`/interviews/${id}/${action}`,body);if(selected===id)await load(id);watch(job);return job;}
$('create').onsubmit=e=>{e.preventDefault();const button=$('create').querySelector('button[type=submit]');if(button.disabled)return;guard(async()=>{button.disabled=true;try{const form=new FormData($('create'));const iv=await api('/interviews',{method:'POST',body:form});await load(iv.id);await submit('messages',{content:'请根据我填写的想法或上传的PRD，按工具包开始梳理需求。'});}finally{button.disabled=false;}});};
$('chat').onsubmit=e=>{e.preventDefault();guard(sendConversation);};
$('draft-button').onclick=()=>guard(()=>submit('draft'));
$('confirm').onclick=()=>guard(()=>submit('confirm',{accepted:true,draft_version:detail.draft.version}));
$('finalize').onclick=()=>guard(()=>submit('finalize',{accepted:true}));
guard(async()=>{const h=await api('/health');$('health').textContent=h.status==='ok'?'本机服务正常':'服务暂不可用';await sessions();});

$('revision').onsubmit=e=>{e.preventDefault();guard(()=>submit('revise',{content:$('feedback').value}));};

async function loadPlanning(id){
const p=await api(`/interviews/${id}/workbench`);if(selected!==id)return;planning=p;
const states={prd_pending:'先完成PRD与体检清单，并确认写回。完成后即可生成技术方案。',technical_ready:'PRD已确认。下一步生成技术方案。',technical_review:'请审阅技术方案；可提出修改意见，再确认当前版本。',technical_approved:'技术方案已确认，开发任务尚未生成。可重试任务，或再次点击生成。',tasks_ready:'方案与开发任务已准备好。可以先检查任务范围、产物与验收方法。',source_changed:'PRD或台账已变化，旧方案与任务已过期。请基于最新来源重新生成。',source_unavailable:p.source_error||'无法读取已确认PRD，请先恢复来源文件。'};
$('flow').textContent=`${p.state==='prd_pending'?'① PRD待确认':'① PRD已确认'} → ${p.proposal&&!p.source_changed?'② 技术方案 v'+p.proposal.version+(p.proposal.confirmed?'已确认':'待确认'):'② 技术方案'} → ${p.development_plan?'③ 开发任务已就绪':'③ 开发任务'} → ④ 开发执行 → ⑤ 检查验收`;
$('planning-hint').textContent=states[p.state]||p.state;
$('generate-technical').hidden=['prd_pending','source_unavailable'].includes(p.state)||!!p.proposal&&!p.source_changed;
$('generate-technical').textContent=p.source_changed?'基于最新PRD重新生成方案':'生成技术方案';
$('technical-panel').hidden=!p.proposal;$('development-panel').hidden=!p.development_plan;
if(p.proposal){const proposal=p.proposal;$('technical-title').textContent=`技术方案 v${proposal.version}${proposal.confirmed?' · 已确认':''}`;$('technical-markdown').textContent=friendly(proposal.markdown);$('technical-paths').textContent='方案提案路径：'+proposal.paths.join('、');$('technical-questions').replaceChildren();for(const q of proposal.content.questions)textBlock($('technical-questions'),`需要你决定：${q.question}\n选项：${q.options.join(' / ')}\n推荐：${q.recommendation}\n不选的后果：${q.consequence}`,'question');
$('technical-revision').hidden=p.source_changed||p.state==='source_unavailable';$('confirm-technical').hidden=!!p.development_plan||p.source_changed||p.state==='source_unavailable';$('confirm-technical').dataset.blocked=String(proposal.content.questions.length>0);$('confirm-technical').disabled=proposal.content.questions.length>0;$('confirm-technical').textContent=proposal.confirmed?'生成开发任务':'确认当前方案，生成开发任务';}
$('development-tasks').replaceChildren();if(p.development_plan){for(const task of p.development_plan.content.tasks){const card=el('div','','question');card.append(el('h3',task.id+' · '+task.title));textBlock(card,`阶段：${task.phase}\n目标：${task.goal}\n输入：${task.inputs.join('；')}\n产物：${task.deliverables.join('；')}\n检查：${task.checks.join('；')}\n人工确认：${task.confirmations.join('；')||'无'}\n依赖：${task.depends_on.join('、')||'无'}`,'bubble');$('development-tasks').append(card);}$('development-paths').textContent='任务文件：'+p.development_plan.paths.join('、');}
const target=p.target;$('target-status').textContent=`目标产品管家阶段：${target.kit_stage}\n管家状态：${target.kit_status}\n上次停在：${target.last_stop||'未记录'}\n已有证据目录：${target.evidence.join('、')||'无'}\n\n${target.current_task||'本阶段任务文件尚未生成。工厂不会自动替目标产品翻页。'}`;
}
$('generate-technical').onclick=()=>guard(()=>submit('technical'));
$('technical-revision').onsubmit=e=>{e.preventDefault();guard(()=>submit('technical/revise',{content:$('technical-feedback').value}));};
$('confirm-technical').onclick=()=>guard(()=>{const p=planning.proposal;return submit('technical/confirm',{accepted:true,proposal_version:p.version,source_digest:p.source_digest,proposal_digest:p.proposal_digest});});

async function loadExecution(id){
const run=await api(`/interviews/${id}/execution`);if(selected!==id)return;execution=run;
const allowed=planning?.state==='tasks_ready';$('execution-panel').hidden=!run&&!planning?.development_plan;
const blocked=!allowed||run?.status==='generated';$('execute-start').dataset.blocked=String(blocked);
const active=detail?.jobs.some(j=>!terminal(j.status));$('execute-start').disabled=blocked||active;$('execution-feedback').disabled=!!active;
$('execution-cancel').hidden=!detail?.jobs.some(j=>j.action==='execute'&&!terminal(j.status));
$('execute-start').textContent=!run?'开始开发执行':run.status==='generated'?'代码已生成，待功能检查':['pending','running'].includes(run.status)?'正在开发…':'继续开发执行';
const signature=JSON.stringify(run);
const statuses={pending:'等待执行',running:'正在实现',generated:'代码已生成，待功能检查',failed:'执行失败，已保留进度',blocked:'需要补充信息',cancelled:'已停止，进度已保留',interrupted:'执行中断，可继续'};
const activeStep=run?.steps.find(s=>s.status==='running');const elapsed=activeStep?.batch_started_at?Math.max(0,Math.floor(Date.now()/1000-activeStep.batch_started_at)):0;
$('execution-status').textContent=run?(statuses[run.status]||run.status)+(activeStep?' · '+activeStep.task_id+' 第'+activeStep.active_batch+'批 · 已保存'+activeStep.chunks+'批 · 已等待'+elapsed+'秒（每次生成上限'+run.timeout_per_batch+'秒）':'')+(run.error?'\n'+run.error.message:''):'准备执行已确认的方案 v'+(planning?.proposal?.version||'—')+' 和当前任务清单。点击后开始本次开发。';
$('execution-workspace').textContent=run?.workspace?'当前代码目录：'+run.workspace+'\n'+run.note:'';
if(signature===executionSignature)return;executionSignature=signature;
$('execution-steps').replaceChildren();
for(const step of run?.steps||[]){const card=el('div','','question');card.append(el('h3',step.task_id+' · '+step.title+' · '+(statuses[step.status]||step.status)+(step.chunks?' · 已保存'+step.chunks+'批':'')));if(step.summary)textBlock(card,'生成说明：'+step.summary);for(const q of step.questions||[])textBlock(card,'请补充：'+q,'bubble');
for(const change of step.changes){const row=el('div');row.append(el('span',change.path+`（+${change.added} / -${change.removed}） `));const view=el('button','查看代码改动');view.type='button';const diff=el('pre');diff.hidden=true;view.onclick=()=>guard(async()=>{if(!diff.hidden){diff.hidden=true;return;}const d=await api(`/executions/${run.id}/diff?task_id=${encodeURIComponent(step.task_id)}&path=${encodeURIComponent(change.path)}`);diff.textContent=d.diff+(d.truncated?'\n（预览较长，完整内容请查看代码目录）':'');diff.hidden=false;});row.append(view,diff);card.append(row);}
for(const check of step.checks)textBlock(card,`${check.path} · ${check.kind} · ${check.status==='passed'?'通过':check.status==='failed'?'失败':'待验'}：${check.message}`,'muted');$('execution-steps').append(card);}
}
$('execution-form').onsubmit=e=>{e.preventDefault();guard(()=>{const p=planning.proposal;return submit('execute',{accepted:true,proposal_version:p.version,source_digest:p.source_digest,proposal_digest:p.proposal_digest,plan_digest:planning.development_plan.plan_digest,execution_id:execution?.id||null,feedback:$('execution-feedback').value});});};
$('execution-cancel').onclick=()=>guard(async()=>{const job=detail.jobs.find(j=>j.action==='execute'&&!terminal(j.status));if(job){await post('/jobs/'+job.id+'/cancel');await load(selected);}});
async function loadVerification(id){
const check=await api(`/interviews/${id}/verification`);if(selected!==id)return;verification=check;
$('verification-panel').hidden=execution?.status!=='generated'&&!check;
const active=!!detail?.jobs.some(j=>!terminal(j.status));
$('synthetic-entry').hidden=detail?.interview.product_name!=='医美小助手';$('synthetic-link').href='/synthetic-demo?interview='+id;
$('verify-start').dataset.blocked=String(execution?.status!=='generated');$('verify-start').disabled=active||execution?.status!=='generated';
$('check-fix').dataset.blocked=String(!check||check.status!=='failed'||check.source_changed);$('check-fix').disabled=active||$('check-fix').dataset.blocked==='true';
$('accept-check').dataset.blocked=String(!check||check.status!=='passed'||check.source_changed||check.accepted);$('accept-check').disabled=active||$('accept-check').dataset.blocked==='true';
const signature=JSON.stringify(check);if(signature===checkSignature)return;checkSignature=signature;
const states={pending:'等待检查',running:'正在检查',passed:'工程检查通过，真实业务条件见下方',failed:'检查发现失败项',interrupted:'检查中断，请重新检查',cancelled:'检查已停止'};
$('verification-status').textContent=check?(check.source_changed?'此报告对应旧代码，请检查最新版本。':states[check.status]||check.status)+(check.accepted?'\n当前版本工程验收已记录。':'')+(check.error?'\n'+friendly(check.error.message):''):'代码已生成。点击上方按钮运行真实测试与本机启动检查。';
$('verification-steps').replaceChildren();for(const step of check?.steps||[]){const card=el('div','','question');const status={passed:'通过',failed:'失败',running:'进行中',pending:'待验',not_run:'未运行'}[step.status]||step.status;card.append(el('strong',step.name+' · '+status),el('p',step.summary));if(step.details){const more=el('details');more.append(el('summary','查看检查诊断'),el('pre',step.details));card.append(more);}$('verification-steps').append(card);}
$('verification-preview').replaceChildren();if(check?.preview_url){const link=el('a','打开本地预览');link.href=check.preview_url;link.target='_blank';link.rel='noopener';$('verification-preview').append(link);if(check.preview_login_available){const login=el('button','使用本地验收账号进入');login.type='button';login.onclick=()=>{const tab=window.open('about:blank','_blank');if(tab)tab.opener=null;guard(async()=>{try{const result=await post(`/verifications/${check.id}/preview-login`);if(tab)tab.location=result.url;else window.location.assign(result.url);}catch(error){if(tab)tab.close();throw error;}});};$('verification-preview').append(login,el('p','仅创建本轮隔离环境的合成账号，凭据不显示、不写入报告；不代表获准开放业务试用。','muted'));}}
const blockerNames={MODEL_KEY_MISSING:'真实模型密钥未配置',VERIFIED_PRICE_MISSING:'厂商价格与预算核实待完成',TRUSTED_ADAPTER_MISSING:'真实模型适配器未接入',REVIEWED_KNOWLEDGE_INSUFFICIENT:'经审核的业务资料尚不完整'};
$('verification-blockers').textContent=check?.blockers.length?'业务待验项\n'+check.blockers.map(b=>'• '+(blockerNames[b]||b)).join('\n'):'';
$('check-repair-form').hidden=!check||check.status!=='failed'||check.source_changed;$('check-accept-form').hidden=!check||check.status!=='passed'||check.source_changed;
$('adoption-section').hidden=!check||check.status!=='passed'||check.source_changed||check.adopted;$('adoption-plan').replaceChildren();$('confirm-adoption').hidden=true;
$('verification-evidence').textContent=check?'本轮报告：'+check.evidence_path+(check.adopted?' · 当前代码已采用':''):'';
}
$('verify-start').onclick=()=>guard(()=>submit('verify',{accepted:true,execution_id:execution.id,revision:execution.revision,manifest_digest:execution.manifest_digest}));
$('check-repair-form').onsubmit=e=>{e.preventDefault();guard(()=>submit('check-fix',{accepted:true,verification_id:verification.id,feedback:$('check-feedback').value}));};
$('check-accept-form').onsubmit=e=>{e.preventDefault();guard(async()=>{await post(`/verifications/${verification.id}/accept`,{accepted:true,revision:verification.revision,comment:$('check-comment').value});await loadVerification(selected);renderProduction();});};
let adoptionPlan=null;
$('review-adoption').onclick=()=>guard(async()=>{adoptionPlan=await api(`/verifications/${verification.id}/adoption`);$('adoption-plan').replaceChildren(el('p','目标目录：'+adoptionPlan.target));for(const f of adoptionPlan.files)textBlock($('adoption-plan'),f.action+' · '+f.path);if(adoptionPlan.conflicts.length)textBlock($('adoption-plan'),'存在独立改动，不能自动采用：'+adoptionPlan.conflicts.join('、'),'bubble');$('confirm-adoption').hidden=!!adoptionPlan.conflicts.length||adoptionPlan.adopted;});
$('confirm-adoption').onclick=()=>guard(async()=>{await post(`/verifications/${verification.id}/adopt`,{accepted:true,plan_digest:adoptionPlan.plan_digest});await loadVerification(selected);renderProduction();notify('已采用当前代码；真实业务待验项和目标产品管家阶段保持原状。');});


setInterval(()=>{if(!document.hidden&&!$('home-view').hidden)sessions().catch(()=>{});},5000);

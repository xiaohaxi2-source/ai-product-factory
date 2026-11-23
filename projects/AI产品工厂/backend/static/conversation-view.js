/* The central transcript and the contextual question panel share existing APIs. */
let conversationProject=null;
let questionSetKey='',rightTabProject=null;
const actionTitles={message:'需求澄清',draft:'需求草稿已整理',revise:'需求草稿已更新',confirm:'需求检查已完成',finalize:'需求终稿已保存',technical:'实现方案已生成',technical_revise:'实现方案已更新',technical_confirm:'方案已确认，开发任务已准备',execute:'本轮开发已结束',verify:'运行检查已结束',check_fix:'问题修复与重检已结束'};

function readDraft(key){try{return JSON.parse(localStorage.getItem(key)||'{}');}catch{return {};}}
function saveDraft(key,value){localStorage.setItem(key,JSON.stringify(value));}

function questionCard(question,key,busy){
  const draft=readDraft(key),card=el('div','','clarification-card');
  card.append(el('h3',question.title));
  if(question.why)card.append(el('p',question.why,'question-context'));
  const choices=el('div','','answer-options'),buttons=[];
  function paint(){buttons.forEach((button,index)=>button.setAttribute('aria-pressed',String(draft.choice===index&&!draft.recommended)));recommended?.setAttribute('aria-pressed',String(!!draft.recommended));}
  (question.options||[]).forEach((option,index)=>{const button=el('button',option,'answer-option');button.type='button';button.disabled=busy;button.onclick=()=>{draft.choice=draft.choice===index&&!draft.recommended?null:index;draft.recommended=false;saveDraft(key,draft);paint();};buttons.push(button);choices.append(button);});
  card.append(choices);
  let recommended=null;
  if(question.recommendation){const row=el('div','','recommendation-row');row.append(el('span','建议：'+question.recommendation));recommended=el('button','按推荐');recommended.type='button';recommended.disabled=busy;recommended.onclick=()=>{draft.choice=null;draft.recommended=!draft.recommended;saveDraft(key,draft);paint();};row.append(recommended);card.append(row);}
  const input=el('textarea');input.rows=2;input.maxLength=4000;input.placeholder='也可以写下你的想法，或补充选项没有表达清楚的地方';input.setAttribute('aria-label',question.title+'：我的想法');input.value=draft.thought||'';input.disabled=busy;input.oninput=()=>{draft.thought=input.value;saveDraft(key,draft);};card.append(input);
  if(question.consequence)card.append(el('p',question.consequence,'question-consequence'));
  paint();return {card,draft,key,input};
}

function renderClarifications(data,busy){
  const id=data.interview.id,box=$('interview-questions');box.replaceChildren();$('items').replaceChildren();$('checklist').hidden=data.interview.status!=='auditing';
  const key=[id,data.interview.status,data.messages.filter(m=>m.role==='assistant').at(-1)?.id,data.checklist.filter(it=>it.closed).length,planning?.proposal?.version,data.pending_review?.review_id].join(':');
  const changed=key!==questionSetKey;questionSetKey=key;
  let count=0;
  if(data.interview.status==='collecting'){
    const message=data.messages.filter(m=>m.role==='assistant').at(-1),questions=message?.questions||[];
    count=questions.length;
    if(count){box.append(el('p','点选一个选项，或直接写自己的想法；两者可以一起提交。','panel-intro'));
      const cards=questions.map((q,i)=>questionCard(q,`factory-question:${id}:${message.id}:${i}`,busy));cards.forEach(row=>box.append(row.card));
      const submitAnswers=el('button','确认并发送回答','primary full');submitAnswers.type='button';submitAnswers.disabled=busy;
      submitAnswers.onclick=()=>guard(async()=>{const content=FactoryConversation.interviewAnswers(questions,cards.map(c=>c.draft));submitAnswers.disabled=true;try{await submit('messages',{content},id);cards.forEach(c=>localStorage.removeItem(c.key));}finally{submitAnswers.disabled=false;}});
      box.append(submitAnswers,el('p','未发送的选项和想法会在本机保留；确认发送后进入对话记录。','question-consequence'));
    }
  }
  if(data.interview.status==='auditing'){
    const open=data.checklist.filter(it=>!it.closed),answered=data.checklist.filter(it=>it.closed);count=open.length;
    const renderItem=(item,editing=false)=>{
      const question={title:item.question,options:item.options,recommendation:item.recommendation,why:item.why,consequence:item.consequence};
      const row=questionCard(question,`factory-checklist:${id}:${item.id}`,busy),save=el('button',editing?'保存修改':'确认这一项','primary');save.type='button';save.disabled=busy;
      save.onclick=()=>guard(async()=>{const content=FactoryConversation.answer(question,row.draft);save.disabled=true;try{await post('/checklist-items/'+item.id+'/answer',{content});localStorage.removeItem(row.key);if(selected===id)await load(id);}finally{save.disabled=false;}});row.card.append(save);return row.card;
    };
    open.slice(0,5).forEach(item=>$('items').append(renderItem(item)));
    if(open.length>5)$('items').append(el('p',`本轮先确认5项，还有${open.length-5}项会接着显示。`,'muted'));
    if(answered.length){const history=el('details','','answered-questions');history.append(el('summary','已确认 '+answered.length+' 项'));for(const item of answered){const saved=el('div','','saved-answer');saved.append(el('strong',item.question),el('p',item.answer));const edit=el('button','修改答案');edit.type='button';edit.disabled=busy;edit.onclick=()=>{edit.hidden=true;saved.append(renderItem(item,true));};saved.append(edit);history.append(saved);}$('items').append(history);}
  }
  if(data.interview.status==='closed'&&planning?.proposal?.content.questions.length){
    const questions=planning.proposal.content.questions.map(q=>({title:q.question,...q}));count=questions.length;
    const cards=questions.map((q,i)=>questionCard(q,`factory-plan-question:${id}:${planning.proposal.version}:${i}`,busy));cards.forEach(row=>box.append(row.card));
    const button=el('button','确认回答并更新方案','primary full');button.type='button';button.disabled=busy;button.onclick=()=>guard(async()=>{const content=FactoryConversation.interviewAnswers(questions,cards.map(c=>c.draft));button.disabled=true;try{await submit('technical/revise',{content},id);cards.forEach(c=>localStorage.removeItem(c.key));}finally{button.disabled=false;}});box.append(button);
  }
  if(data.pending_review)count=data.pending_review.decisions.length;
  $('question-count').textContent=count;$('questions-tab').textContent=count?'需求与验收 · '+count:'需求与验收';$('question-empty').hidden=!!count||data.interview.status==='auditing';$('question-empty').textContent=busy?'正在整理本轮内容，需要你确认的问题会显示在这里。':'当前没有需要你确认的问题。接下来的进度会同步在左侧对话里。';
  if(rightTabProject!==id){showRightTab('questions');rightTabProject=id;}
  if(changed)requestAnimationFrame(()=>$('right-panel').scrollTop=0);
}

function showRightTab(tab){$('right-questions').hidden=tab!=='questions';$('right-files').hidden=tab!=='files';$('questions-tab').setAttribute('aria-selected',String(tab==='questions'));$('files-tab').setAttribute('aria-selected',String(tab==='files'));}
$('questions-tab').onclick=()=>showRightTab('questions');$('files-tab').onclick=()=>showRightTab('files');$('toggle-questions').onclick=()=>{$('right-panel').classList.toggle('mobile-open');showRightTab('questions');};$('close-questions').onclick=()=>$('right-panel').classList.remove('mobile-open');

function conversationBubble(role,content,kind=''){
  const row=el('article','','conversation-message '+role+' '+kind),avatar=el('div',role==='user'?'你':'','message-avatar'),body=el('div','','message-body');
  if(role!=='user'){const portrait=document.createElement('img');portrait.src='/static/technical-partner.svg';portrait.alt='技术伙伴头像';portrait.width=36;portrait.height=36;portrait.className='assistant-portrait';avatar.append(portrait);}
  body.append(el('div',role==='user'?'你':'产品助手','message-author'),el('div',content,'message-copy'));row.append(avatar,body);return row;
}
function conversationAction(label,handler,disabled=false,primary=false){const button=el('button',label,primary?'primary':'');button.type='button';button.disabled=disabled;button.onclick=()=>guard(handler);return button;}
function forwardAction(id){const button=$(id);if(button&&!button.disabled)button.click();}
function previewDocument(id){return api(`/interviews/${selected}/artifacts/${id}`).then(doc=>{$('artifact-title').textContent=friendly(doc.name);$('artifact-content').textContent=friendly(doc.content);$('artifact-download').href=doc.download_url;$('artifact-dialog').showModal();});}

function renderConversation(data,running){
  const container=$('messages'),scroll=$('conversation-scroll'),stick=conversationProject!==data.interview.id||scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<120;
  const oldTop=scroll.scrollTop;container.replaceChildren();const events=[];
  for(const message of data.messages){
    if(message.role==='user'&&message.content==='请根据我填写的想法或上传的PRD，按工具包开始梳理需求。')continue;
    let content=message.content;
    if(message.questions?.length){const note=message.content&&!message.questions.some(q=>message.content.includes(q.title))&&!/选项[：:]/.test(message.content)?message.content+'\n\n':'';content=note+'本轮有'+message.questions.length+'个问题需要确认。你可以在右侧选择答案并补充想法，也可以直接在这里告诉我。';}
    events.push({at:message.created_at,kind:'message',role:message.role,content});
  }
  for(const item of data.checklist.filter(it=>it.closed))events.push({at:data.jobs.find(j=>j.action==='confirm')?.updated_at||data.interview.updated_at,kind:'message',role:'user',content:'已确认：'+item.question+'\n'+item.answer});
  for(const job of data.jobs){
    if(job.user_content&&terminal(job.status)&&!data.messages.some(m=>m.role==='user'&&m.content===job.user_content))events.push({at:job.created_at,role:'user',kind:'message',content:job.user_content});
    if(['technical','technical_revise','technical_confirm','execute','verify','check_fix'].includes(job.action)&&terminal(job.status)){
      const text=job.status==='succeeded'?(actionTitles[job.action]||'本轮处理已完成')+(job.action==='execute'?'。已保存开发成果，接下来可以运行检查。':job.action==='verify'?'。具体通过项和待验项可在检查报告中查看。':'。'):(job.error?.message||'本轮处理已停止，进度和记录已保留。');
      events.push({at:job.updated_at,role:'assistant',kind:'event',content:text});
    }
  }
  if(running?.user_content&&!data.messages.some(m=>m.role==='user'&&m.content===running.user_content))events.push({at:running.created_at,kind:'message',role:'user',content:running.user_content});
  events.sort((a,b)=>a.at.localeCompare(b.at));
  if(!events.length)container.append(conversationBubble('assistant','把产品想法告诉我，我们会先把需求说清楚，再逐步完成方案、开发和检查。'));
  for(const event of events)container.append(conversationBubble(event.role,event.content,event.kind));
  const latest=data.jobs.at(-1);
  if(latest?.error&&!running&&!['execute','verify','check_fix','technical','technical_revise','technical_confirm'].includes(latest.action))container.append(conversationBubble('assistant',data.pending_review?'需求终稿还缺几项决定，请在右侧补充；之前的回答会保留。':latest.error.message,'error'));
  const actions=$('conversation-next');actions.replaceChildren();
  if(!running){
    const card=el('div','','conversation-action-card');
    const title=el('strong'),copy=el('p'),buttons=el('div','','conversation-actions');card.append(title,copy,buttons);
    const add=(label,id,primary=false)=>buttons.append(conversationAction(label,()=>forwardAction(id),$(id)?.disabled||false,primary));
    if(data.interview.status==='collecting'){
      title.textContent='当前阶段 · 需求澄清';copy.textContent='确认目标用户、使用场景和首版范围。你觉得信息足够时，可以先整理成需求草稿。';add('整理需求草稿','draft-button',true);
    }else if(data.interview.status==='draft_pending'){
      title.textContent='需求草稿 v'+data.draft.version+' 已准备好';copy.textContent='先看看是否准确表达你的想法。要修改，直接在下方告诉我；方向确认后，我会继续检查遗漏事项。';buttons.append(conversationAction('查看需求草稿',()=>previewDocument('prd')));add('方向确认，继续补全','confirm',true);
    }else if(data.interview.status==='auditing'){
      const open=data.checklist.filter(it=>!it.closed).length;title.textContent=open?'还有 '+open+' 项需求待确认':'需求清单已确认';copy.textContent=open?'问题集中在右侧。可以选择建议，也可以填写自己的答案。':'下一步保存需求终稿，再开始规划实现方案。';if(!open&&!data.pending_review)add('确认并保存需求终稿','finalize',true);
    }else if(!planning?.proposal||planning.source_changed){
      title.textContent='需求已确认 · 准备实现方案';copy.textContent='我会根据已确认的需求规划首版实现方式和交付步骤。';add('开始制定方案','generate-technical',true);
    }else if(!planning?.development_plan){
      title.textContent='实现方案 v'+planning.proposal.version+' 已准备好';copy.textContent=planning.proposal.content.questions.length?'方案中还有几个决定，请在右侧确认。':'可以在对话中提出修改意见，或确认方案并拆分开发任务。';buttons.append(conversationAction('查看方案',()=>previewDocument('technical')));add('确认方案，准备开发','confirm-technical',true);
    }else if(execution?.status!=='generated'){
      title.textContent='开发任务已准备好';copy.textContent='按已确认的方案实现功能，过程中会持续保存成果。遇到问题或需要你决定的内容，会在这里同步。';buttons.append(conversationAction('查看开发任务',()=>previewDocument('tasks')));add(execution?'继续开发':'开始开发','execute-start',true);
    }else if(!verification||verification.source_changed){
      title.textContent='本轮开发成果已保存';copy.textContent='接下来运行检查，确认能否正常启动和使用。';add('开始检查与预览','verify-start',true);
    }else if(verification.status==='failed'){
      title.textContent='检查发现需要修复的问题';copy.textContent='我会根据检查报告修复。你也可以在下方补充修复要求。';buttons.append(conversationAction('查看检查报告',()=>showStage('check')));add('根据报告修复并重检','check-fix',true);
    }else{
      title.textContent=verification.status==='passed'?'工程检查已通过':'检查记录已保留';copy.textContent='请查看运行结果和待验事项。需要采用到项目目录时，先审阅具体文件再确认。';buttons.append(conversationAction('查看检查与交付',()=>showStage('check')));if(verification.preview_url){const link=el('a','打开产品预览');link.href=verification.preview_url;link.target='_blank';link.rel='noopener';buttons.append(link);}
    }
    actions.append(card);
    const expected={message:'collecting',draft:'collecting',revise:'draft_pending',confirm:'draft_pending',finalize:'auditing',technical:'closed',technical_revise:'closed',technical_confirm:'closed',execute:'closed',verify:'closed',check_fix:'closed'};
    if(latest?.error&&expected[latest.action]===data.interview.status&&!data.pending_review){actions.append(conversationAction('重试上一次处理',async()=>{const job=await post('/jobs/'+latest.id+'/retry');await load(data.interview.id);watch(job);}));}
  }
  conversationProject=data.interview.id;
  requestAnimationFrame(()=>{if(stick)scroll.scrollTop=scroll.scrollHeight;else scroll.scrollTop=oldTop;});
}

function configureComposer(data,busy,switched){
  const input=$('answer'),button=$('send-message');let action='messages',label='告诉我你的想法';
  button.textContent='发送';
  if(data.interview.status==='draft_pending'){action='revise';label='说说需求草稿需要怎么改';button.textContent='发送修改意见';}
  else if(data.interview.status==='auditing'){action='';label='请在右侧补充待确认的需求';button.textContent='发送';}
  else if(data.interview.status==='closed'){
    if(!planning?.proposal){action='';label='需求已确认，可以开始制定方案';}
    else if(planning.development_plan&&!planning.source_changed&&execution?.status!=='generated'){action='execute';label='补充这次开发需要注意的地方';button.textContent=execution?'发送并继续开发':'发送并开始开发';}
    else if(verification?.status==='failed'&&!verification.source_changed){action='check-fix';label='补充你希望怎样修复';button.textContent='发送并修复';}
    else{action='technical/revise';label='告诉我方案或产品需要调整的地方';button.textContent='发送修改意见';}
  }else button.textContent='发送';
  $('chat').hidden=false;$('chat').dataset.action=action;$('composer-label').textContent=label;
  input.placeholder=label+'…';input.disabled=busy||!action;button.disabled=busy||!action;
  if(switched)input.value=localStorage.getItem('factory-compose:'+data.interview.id)||'';
  input.oninput=()=>localStorage.setItem('factory-compose:'+data.interview.id,input.value);
}
async function sendConversation(){
  if($('send-message').disabled)return;
  const id=selected,value=$('answer').value.trim(),action=$('chat').dataset.action;if(!value||!action)return;
  if(action==='execute'){const p=planning.proposal;await submit('execute',{accepted:true,proposal_version:p.version,source_digest:p.source_digest,proposal_digest:p.proposal_digest,plan_digest:planning.development_plan.plan_digest,execution_id:execution?.id||null,feedback:value},id);}
  else if(action==='check-fix')await submit('check-fix',{accepted:true,verification_id:verification.id,feedback:value},id);
  else await submit(action,{content:value},id);
  localStorage.removeItem('factory-compose:'+id);if(selected===id)$('answer').value='';
}
function updateDevelopmentActivity(job){if(job.action!=='execute'&&job.action!=='check_fix')return;const step=execution?.steps.find(s=>s.status==='running');if(step)$('task').textContent='正在实现：'+friendly(step.title)+(step.chunks?' · 已保存'+step.chunks+'次进度':'');}

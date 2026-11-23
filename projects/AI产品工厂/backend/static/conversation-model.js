/* Presentation and answer composition only; saved source documents remain unchanged. */
(function(root){
  function displayText(value){return String(value??'').replace(/codex\s*cli/gi,'开发助手').replace(/codex/gi,'开发助手').replace(/idea-to-launch(?:工具包)?/gi,'产品开发流程').replace(/app-server/gi,'开发服务');}
  function answer(question,draft={}){
    const choice=Number.isInteger(draft.choice)?question.options?.[draft.choice]:null;
    const thought=String(draft.thought||'').trim();
    const recommended=draft.recommended?question.recommendation:null;
    if(!choice&&!thought&&!recommended)throw new Error('请选择一个选项，或写下你的想法。');
    const lines=[];
    if(choice)lines.push('选择：'+choice);
    if(recommended)lines.push('按推荐：'+recommended);
    if(thought)lines.push('我的想法：'+thought);
    return lines.join('\n');
  }
  function interviewAnswers(questions,drafts){
    const content=questions.map((q,i)=>'问题'+(i+1)+'：'+q.title+'\n'+answer(q,drafts[i])).join('\n\n');
    if(content.length>4000)throw new Error('这轮回答较长，请精简到4000字以内；已填写内容会保留。');
    return content;
  }
  function progress(action,phase){
    const labels={message:'正在理解你的想法',draft:'正在整理需求草稿',revise:'正在修改需求草稿',confirm:'正在检查需求是否完整',finalize:'正在保存需求终稿',technical:'正在制定实现方案',technical_revise:'正在调整方案',technical_confirm:'正在拆分开发任务',execute:'正在实现产品功能',verify:'正在检查运行结果',check_fix:'正在修复发现的问题'};
    const detail=typeof phase==='string'&&/[\u3400-\u9fff]/.test(phase)&&!/[a-z]+\//i.test(phase)?displayText(phase):'';
    return (labels[action]||'正在整理')+(detail?' · '+detail:'');
  }
  function production(data,plan,run,check,job){
    const names=['需求澄清','PRD确认','技术方案','开发任务','开发实现','检查验收','本机交付','上线发布'];
    let index=0,caption='请在右侧回答问题，或在对话中补充想法。',complete=false;
    if(data?.draft||data?.interview.status==='auditing'){index=1;caption='请确认需求方向，并补齐待确认事项。';}
    if(data?.interview.status==='closed'){
      index=2;caption='需求已确认，接下来制定并确认实现方案。';
      if(plan?.source_changed)caption='需求已更新，需要重新确认方案。';
      else if(plan?.proposal?.confirmed){index=3;caption='方案已确认，正在准备开发任务。';}
      if(!plan?.source_changed&&plan?.development_plan){
        index=4;caption='按已确认的开发任务实现，代码分批保存在本机。';
        if(run?.status==='generated'){
          index=5;caption='代码已保存，接下来运行检查与本地预览。';
          if(check?.revision===run.revision&&!check?.source_changed){
            if(check.status==='failed')caption='检查发现问题，修复后需要重新检查。';
            if(check.status==='passed'){index=6;caption='工程检查通过，请审阅文件并确认采用。';
              if(check.adopted){complete=true;caption='当前代码已采用，本机交付完成；业务待验项以检查报告为准。';}}
          }
        }
      }
    }
    const active=job&&['queued','running','cancelling'].includes(job.status);
    const actionIndex={message:0,draft:1,revise:1,confirm:1,finalize:1,technical:2,technical_revise:2,technical_confirm:3,execute:4,check_fix:4,verify:5};
    if(active&&actionIndex[job.action]!==undefined){index=actionIndex[job.action];complete=false;caption=progress(job.action);}
    const failed=!active&&((index===4&&['failed','blocked','interrupted','cancelled'].includes(run?.status))||(index===5&&check?.revision===run?.revision&&check?.status==='failed')||(actionIndex[job?.action]===index&&['failed','interrupted','cancelled'].includes(job?.status)));
    if(failed&&index===4)caption='开发暂停，已保存文件保留，可补充说明后继续。';
    return {index,caption,steps:names.map((name,i)=>({name,status:i===7?'future':i<index||i===index&&complete?'complete':i===index?(active?'running':failed?'attention':'current'):'pending'}))};
  }
  const api={displayText,answer,interviewAnswers,progress,production};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.FactoryConversation=api;
})(globalThis);

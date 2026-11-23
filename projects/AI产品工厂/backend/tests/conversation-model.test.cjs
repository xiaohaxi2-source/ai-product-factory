const test=require('node:test');
const assert=require('node:assert/strict');
const model=require('../static/conversation-model.js');
const question={title:'首版给谁用？',options:['先自己用','邀请朋友'],recommendation:'先自己用'};
test('selection and personal thought both reach the message without overwriting either',()=>{
  const content=model.interviewAnswers([question],[{choice:1,thought:'先邀请三位朋友，暂不开放注册'}]);
  assert.match(content,/选择：邀请朋友/);assert.match(content,/我的想法：先邀请三位朋友/);
});
test('custom answers work without selecting an option; empty drafts never imply consent',()=>{
  assert.match(model.answer(question,{thought:'先给同事内部用'}),/同事内部用/);
  assert.throws(()=>model.answer(question,{}));assert.throws(()=>model.answer(question,{choice:99}));
});
test('recommendation requires an explicit draft flag and oversized batches are rejected',()=>{
  assert.match(model.answer(question,{recommended:true}),/按推荐：先自己用/);
  assert.throws(()=>model.interviewAnswers([question],[{thought:'字'.repeat(4001)}]),/4000/);
});
test('customer-facing progress does not expose runtime names or protocol phases',()=>{
  assert.equal(model.displayText('Codex CLI / idea-to-launch'),'开发助手 / 产品开发流程');
  assert.equal(model.progress('draft','item/completed'),'正在整理需求草稿');
  assert.equal(model.progress('execute','item_completed'),'正在实现产品功能');
  assert.match(model.progress('execute','正在保存代码'),/正在实现产品功能/);
});

test('timeline follows confirmed state and never treats generated code as accepted delivery',()=>{
  const data={interview:{status:'closed'}};
  const plan={proposal:{confirmed:true},development_plan:{}};
  const run={status:'generated',revision:'new'};
  assert.equal(model.production(data,plan,run,null).index,5);
  assert.equal(model.production(data,plan,run,{status:'passed',revision:'old',adopted:true}).index,5);
  const ready=model.production(data,plan,run,{status:'passed',revision:'new'});
  assert.equal(ready.index,6);assert.equal(ready.steps[6].status,'current');
  const done=model.production(data,plan,run,{status:'passed',revision:'new',adopted:true});
  assert.equal(done.steps[6].status,'complete');assert.equal(done.steps[7].status,'future');
  assert.equal(model.production(data,{...plan,source_changed:true},run,null).index,2);
});
test('timeline shows the active task and pauses without claiming completion',()=>{
  const data={interview:{status:'closed'}};
  const plan={proposal:{confirmed:true},development_plan:{}};
  const active=model.production(data,plan,{status:'running'},null,{action:'execute',status:'running'});
  assert.equal(active.index,4);assert.equal(active.steps[4].status,'running');
  assert.equal(model.production(data,plan,{status:'failed'},null).steps[4].status,'attention');
  assert.equal(model.production({interview:{status:'auditing'},draft:{}},null,null,null).index,1);
});

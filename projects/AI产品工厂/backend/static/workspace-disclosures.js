/* Transient workspace chrome; durable project state remains on the server. */
(function(){
  const rail=document.getElementById('sidebar-rail');
  const tracker=document.getElementById('production-tracker');
  const navButton=document.getElementById('toggle-navigation');
  const flowButton=document.getElementById('toggle-production');
  const popover=document.getElementById('production-popover');
  const mobileNavigation=()=>matchMedia('(max-width:650px)').matches;
  function navigation(open){
    open=!!open&&!mobileNavigation();
    rail.classList.toggle('expanded',open);
    navButton.setAttribute('aria-expanded',String(open));
    navButton.setAttribute('aria-label',open?'收起项目导航':'展开项目导航');
  }
  function production(open){
    tracker.classList.toggle('expanded',!!open);
    flowButton.setAttribute('aria-expanded',String(!!open));
    popover.setAttribute('aria-hidden',String(!open));
    popover.inert=!open;
  }
  function bindHover(surface,setOpen){
    surface.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse')setOpen(true);});
    surface.addEventListener('pointerleave',event=>{if(event.pointerType==='mouse')setOpen(false);});
    surface.addEventListener('focusin',event=>{if(event.target.matches(':focus-visible'))setOpen(true);});
    surface.addEventListener('focusout',event=>{if(!surface.contains(event.relatedTarget))setOpen(false);});
  }
  bindHover(rail,navigation);bindHover(tracker,production);
  navButton.addEventListener('click',()=>navigation(!rail.classList.contains('expanded')));
  flowButton.addEventListener('click',()=>production(!tracker.classList.contains('expanded')));
  document.addEventListener('pointerdown',event=>{
    if(!rail.contains(event.target))navigation(false);
    if(!tracker.contains(event.target))production(false);
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){navigation(false);production(false);}});
  matchMedia('(max-width:650px)').addEventListener('change',()=>navigation(false));
  navigation(false);production(false);
})();

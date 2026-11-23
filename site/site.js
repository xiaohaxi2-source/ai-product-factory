'use strict';
const tabs=[...document.querySelectorAll('[data-image]')];
for(const tab of tabs){tab.addEventListener('click',()=>{for(const item of tabs)item.setAttribute('aria-selected',String(item===tab));const image=document.getElementById('workspace-image');image.src='./assets/'+tab.dataset.image;image.alt=tab.dataset.alt;document.getElementById('preview').setAttribute('aria-labelledby',tab.id);});}

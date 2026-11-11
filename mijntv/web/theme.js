'use strict';
// Shared preference for mobile and administration, applied before the stylesheet.
(() => {
  const key='iptv-mobile-theme', valid=value=>['system','light','dark'].includes(value), media=window.matchMedia?.('(prefers-color-scheme: dark)');
  let selected='system';try{const value=localStorage.getItem(key);if(valid(value))selected=value;}catch(_){}
  function apply(){
    document.documentElement.dataset.theme=selected;
    const meta=document.querySelector('meta[name="theme-color"]');if(meta)meta.content=selected==='dark'||selected==='system'&&media?.matches?'#142321':'#f7f7f2';
    document.querySelectorAll('[data-theme-control]').forEach(control=>control.value=selected);
  }
  function set(value){selected=valid(value)?value:'system';try{localStorage.setItem(key,selected);}catch(_){}apply();}
  document.addEventListener('change',event=>{if(event.target.matches('[data-theme-control]'))set(event.target.value);});
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null){selected=valid(event.newValue)?event.newValue:'system';apply();}});
  if(media?.addEventListener)media.addEventListener('change',apply);else media?.addListener?.(apply);
  document.addEventListener('DOMContentLoaded',apply);apply();
  window.IPTVTheme={set,get:()=>selected};
})();

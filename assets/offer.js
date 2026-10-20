window.KO=async function(html,price,daysId,stages){
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));const fire=(el,...ev)=>ev.forEach(n=>el.dispatchEvent(new Event(n,{bubbles:true})));
 const ta=jQuery('textarea[name=description]');ta.trumbowyg('html',html);const ed=ta.closest('.trumbowyg-box').find('.trumbowyg-editor')[0];fire(ed,'input','keyup','blur');ta.trigger('tbwchange');
 await sleep(800);
 const p=document.getElementById('offer-custom-price');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(p,String(price));fire(p,'input','change','blur');
 await sleep(500);
 if(stages){document.querySelectorAll('.offer-payment-type__item')[1].click();await sleep(1200);
   for(let i=0;i<stages.length;i++){const t=jQuery('textarea[name=stageTitle-'+(i+1)+']');t.trumbowyg('html',stages[i][0]);const e=t.closest('.trumbowyg-box').find('.trumbowyg-editor')[0];fire(e,'input','keyup','blur');t.trigger('tbwchange');
     const pi=document.querySelectorAll('.stages__stage-price-input')[i];Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(pi,String(stages[i][1]));fire(pi,'input','change','blur');await sleep(300);}}
 const v=document.querySelector('.duration-select');let e=v,vm=null;while(e&&!vm){vm=e.__vue__;e=e.parentElement}
 const opts=vm.options||vm.$props.options;const o=opts.find(x=>x.id===daysId||x.value===daysId);if(vm.select)vm.select(o);else vm.$emit('input',o);
 await sleep(800);
 const info={len:ed.innerText.length,we:[...document.querySelectorAll('word-error')].map(w=>w.textContent),price:p.value,days:JSON.stringify(vm.value||vm.$props.value),total:(document.body.innerText.match(/Итого:[^\n]*/)||[''])[0]};
 if(info.we.length)return {stop:'spell',info};
 const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Предложить'&&b.offsetParent);b.click();await sleep(5000);
 info.sent=/предложение отправлено/i.test(document.body.innerText);info.url=location.href;
 info.errs=[...document.querySelectorAll('.form-item__error,[class*=field-error],.error-text')].filter(x=>x.offsetParent&&x.textContent.trim()).map(x=>x.textContent.trim().slice(0,100));
 return info;};

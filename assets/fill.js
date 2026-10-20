// Заполнение формы кворка без посимвольного ввода (работает и в фоновой вкладке)
(function(){
const W=window, sleep=ms=>new Promise(r=>setTimeout(r,ms));
const leaf=t=>[...document.querySelectorAll('button,a,div,span,label,td')].filter(e=>e.children.length<=1&&e.textContent.trim()===t&&e.offsetParent);
const fire=(el,...ev)=>ev.forEach(n=>el.dispatchEvent(new Event(n,{bubbles:true})));
const setCE=(el,text)=>{el.focus();el.innerText=text;fire(el,'input','keyup','change','blur');};
const setVal=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value');d.set.call(el,v);fire(el,'input','change','blur');};
const tbw=(ta,html)=>{const $t=jQuery(ta);$t.trumbowyg('html',html);const ed=$t.closest('.trumbowyg-box').find('.trumbowyg-editor')[0];fire(ed,'input','keyup','blur');$t.trigger('tbwchange').trigger('tbwblur');return ed.innerText.length;};
const P=t=>t.split('\n\n').map(p=>'<p>'+p.replace(/\n/g,'<br>')+'</p>').join('');
const setSel=(s,v)=>{s.value=String(v);fire(s,'change');jQuery(s).trigger('chosen:updated');return s.value;};
const pick=(name,label)=>{const r=[...document.querySelectorAll('input[name="'+name+'"]')].find(e=>(document.querySelector('label[for="'+e.id+'"]')||e.closest('label')||e.parentElement)?.textContent.trim()===label);if(r&&!r.checked)(document.querySelector('label[for="'+r.id+'"]')||r).click();return r?r.checked:'none';};
const groups=()=>{const g={};[...document.querySelectorAll('input[type=radio],input[type=checkbox]')].filter(e=>/^attribute/.test(e.name)).forEach(r=>{const l=(document.querySelector('label[for="'+r.id+'"]')||r.closest('label')||r.parentElement)?.textContent.trim().slice(0,30);(g[r.name]=g[r.name]||[]).push(l+(r.checked?'*':''))});return g;};
const setFile=async(inp,url,name)=>{const b=await (await fetch(url)).blob();const f=new File([b],name,{type:'image/jpeg'});const dt=new DataTransfer();dt.items.add(f);inp.files=dt.files;fire(inp,'change');return b.size;};
const rows=()=>[...document.querySelectorAll('.add-extra__item-main')].filter(r=>r.offsetParent);
W.KF={sleep,leaf,setCE,setVal,tbw,P,setSel,pick,groups,setFile,rows,
 cont:()=>leaf('Продолжить').forEach(b=>b.click()),
 async start(title,cat2,attrs,cover){
   setCE(document.querySelector('.js-kwork-title-editor'),title);await sleep(800);
   jQuery(document.querySelectorAll('select.js-field-input-cat')[0]).val('11').trigger('chosen:updated').trigger('change');await sleep(1500);
   jQuery('select[name=category_id]').val(String(cat2)).trigger('chosen:updated').trigger('change');await sleep(2500);
   for(const [n,l] of attrs){if(n==='*lang'){const g=groups();const k=Object.keys(g).find(k=>g[k].some(x=>/^Python/.test(x)));if(k)pick(k,l);}else pick(n,l);await sleep(1200);}
   await setFile(document.querySelector('input[name="first-kwork-photo[]"]'),'https://raw.githubusercontent.com/ellischment/kwork-radar/main/assets/covers/'+cover,'cover.jpg');await sleep(2500);
   return {url:location.href,groups:groups(),cover:/Изменить обложку/.test(document.body.innerText)};
 },
 async texts(desc,req){this.cont();await sleep(1500);const tas=document.querySelectorAll('textarea[name=description],textarea[name=instruction]');return [tbw(tas[0],P(desc)),tbw(tas[1],P(req))];},
 async single(size,price,days,extras){
   this.cont();await sleep(1500);
   const sw=document.getElementById('bundle-switch');if(sw&&sw.checked)sw.click();await sleep(500);
   setCE(document.getElementById('editor-service_size'),size);setVal(document.getElementById('step2-service-size'),size);
   setSel(document.getElementById('typicalPriceSelect'),price);setSel(document.getElementById('step2-work-time'),days);
   for(let i=0;i<extras.length;i++){if(i>=rows().length){document.querySelector('.js-extra-add-btn').click();await sleep(600);}
     const r=rows()[i];const [n,p,d,h]=extras[i];
     setCE(r.querySelector('.add-extra__item-name-input'),n);setCE(r.querySelector('.add-extra__item-description-input'),h);
     const ins=[...r.querySelectorAll('input[name="my_extras_name[]"],input[name="my_extras_description[]"]')];if(ins[0])setVal(ins[0],n);if(ins[1])setVal(ins[1],h);
     setSel(r.querySelector('select.js-extra-price'),p);setSel(r.querySelector('select.js-extra-duration'),d);await sleep(300);}
   return {ss:document.getElementById('step2-service-size').value,price:document.getElementById('typicalPriceSelect').value,rows:rows().map(r=>[r.querySelector('.add-extra__item-name-input').innerText,r.querySelector('select.js-extra-price').value])};
 },
 async faq(list){this.cont();await sleep(1500);
   for(const [q,a] of list){document.activeElement&&document.activeElement.blur();await sleep(500);const add=document.querySelector('.faq-list-editable__add');if(!add)return 'noadd';add.click();await sleep(900);
     tbw(document.querySelector('textarea[name^=question]'),q);tbw(document.querySelector('textarea[name^=answer]'),a);await sleep(600);
     const ed=document.querySelector('textarea[name^=answer]').closest('.trumbowyg-box').querySelector('.trumbowyg-editor');ed.focus();await sleep(200);ed.blur();document.body.click();await sleep(1200);}
   return document.querySelectorAll('[class*=faq-list-editable__item]').length;},
 async save(){this.cont();await sleep(2000);const log=[];const o=XMLHttpRequest.prototype.open,s=XMLHttpRequest.prototype.send;XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return o.apply(this,arguments)};XMLHttpRequest.prototype.send=function(b){this.addEventListener('load',()=>{if(/save_kwork/.test(this.__u))log.push(String(this.responseText).slice(0,300))});return s.apply(this,arguments)};
   const b=document.querySelector('.js-save-kwork');if(!b.offsetParent)return {err:'save hidden',errs:[...document.querySelectorAll('.form-item__error,.kwork-save-step__field-error')].filter(x=>x.offsetParent&&x.textContent.trim()).map(x=>x.textContent.trim().slice(0,80)),we:[...document.querySelectorAll('word-error')].map(w=>w.textContent)};
   b.click();await sleep(6000);return {url:location.href,log};}
};
})();

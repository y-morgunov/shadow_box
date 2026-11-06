import {Viewer} from './viewer.js';
const $=id=>document.getElementById(id);let csrf='',specs={},image=null,job=null,result=null,view='model',viewer=null,timer=null;const key='shadow-cube-settings-v1';
function error(e){$('error').textContent=e.message||String(e);$('error').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('error').hidden=true,12000)}
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const body=await r.json();if(!r.ok){if(r.status===401){$('login').hidden=false;$('workspace').hidden=true}throw Error(body.error||'Ошибка сервера')}return body}
function snapshot(){return {version:1,controls:Object.fromEntries(Object.keys(specs).map(k=>[k,Number($(k).value)])),light_model:$('light_model').value,preview_pixels:Number($('preview_pixels').value),invert:$('invert').checked,supports:$('supports').checked,image:''}}
function persist(){try{localStorage.setItem(key,JSON.stringify(snapshot()))}catch{}if(result)$('status').textContent='Настройки изменены — пересоздайте модель'}
function restore(s){if(s.version!==1||!s.controls||!['led','isotropic'].includes(s.light_model)||![300,600].includes(s.preview_pixels)||typeof s.invert!=='boolean')throw Error('Неверный формат настроек');if(s.supports!==undefined&&typeof s.supports!=='boolean')throw Error('Неверная настройка: supports');const values={};for(const [k,[,min,max,def]]of Object.entries(specs)){const v=s.controls[k]??def;if(typeof v!=='number'||!Number.isFinite(v)||v<min||v>max||(['threshold','resolution'].includes(k)&&v%1))throw Error('Неверная настройка: '+k);values[k]=v}for(const[k,v]of Object.entries(values))$(k).value=v;$('light_model').value=s.light_model;$('preview_pixels').value=s.preview_pixels;$('invert').checked=s.invert;$('supports').checked=s.supports??true;persist()}
async function init(){try{const c=await api('/api/config');csrf=c.csrf;specs=c.specs;$('controls').replaceChildren();for(const[k,[label,min,max,def,step]]of Object.entries(specs)){const l=document.createElement('label');l.textContent=label;const input=document.createElement('input');Object.assign(input,{id:k,type:'number',min,max,step:['threshold','resolution'].includes(k)?1:'any',value:def});l.append(input);$('controls').append(l);input.onchange=persist}$('login').hidden=true;$('workspace').hidden=false;try{const old=localStorage.getItem(key);if(old)restore(JSON.parse(old))}catch(e){error(e)}try{viewer=new Viewer($('model'))}catch(e){error(e)}await restoreJobs()}catch{$('login').hidden=false}}
$('login-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/login',{password:$('password').value});$('password').value='';await init()}catch(e){error(e)}};
$('image').onchange=async()=>{const f=$('image').files[0];if(!f)return;if(f.size>10*1024*1024){error(Error('Максимум 10 МБ'));return}try{image=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(f)});$('image-name').textContent=f.name;persist()}catch(e){error(e)}};
$('demo').onclick=()=>{image=null;$('image').value='';$('image-name').textContent='Астронавт';persist()};
$('draft').onclick=()=>{$('resolution').value=80;$('preview_pixels').value=300;persist()};$('print').onclick=()=>{$('resolution').value=600;$('preview_pixels').value=600;persist()};
for(const id of ['invert','supports','light_model','preview_pixels'])$(id).onchange=persist;
$('save').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot(),null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='shadow-settings.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
$('load').onclick=()=>$('settings-file').click();$('settings-file').onchange=async()=>{try{const f=$('settings-file').files[0];if(!f)return;if(f.size>100000)throw Error('Файл настроек слишком большой');restore(JSON.parse(await f.text()))}catch(e){error(e)}finally{$('settings-file').value=''}};
function busy(value){for(const e of document.querySelectorAll('aside input, aside select, aside button'))if(e.id!=='cancel')e.disabled=value;$('generate').disabled=value;$('cancel').hidden=!value;$('cancel').disabled=!value||!job;$('badge').textContent=value?'Расчёт…':'Готов к работе'}
$('generate').onclick=async()=>{try{for(const k of Object.keys(specs))if(!$(k).reportValidity())return;job=null;busy(true);persist();const r=await api('/api/jobs',{settings:snapshot(),image});job=r.id;localStorage.setItem('shadow-job',job);busy(true);await poll(job)}catch(e){busy(false);error(e)}};
$('cancel').onclick=async()=>{if(!job)return;const current=job;$('cancel').disabled=true;try{await api(`/api/jobs/${current}/cancel`,{});$('status').textContent='Отмена…'}catch(e){error(e)}};
async function displayResult(id,r){
 result=id;localStorage.setItem('shadow-result',id);$('empty').hidden=true;$('downloads').hidden=false;
 const base=`/api/jobs/${result}/`;$('stl').href=base+'radial-box.stl';$('kit').href=base+'print-k1.zip';
 const p=r.report;let info=`${p.watertight?'Замкнутый объём':'Проверьте геометрию'} · ${p.components} компонент · перемычки: ${p.bridges_per_side.join(', ')}\n6 деталей для K1; сборка на клей. Проверьте размеры деталей в отчёте ZIP.`;
 if(p.printing?.warnings)info+='\n'+p.printing.warnings.join('\n');if(p.lighting?.warnings)info+='\n'+p.lighting.warnings.join('\n');$('result-info').textContent=info;
 await show();if(view!=='model'&&viewer)await viewer.load(base+$('part').value)
}
async function restoreJobs(){
 const saved=localStorage.getItem('shadow-result'),active=localStorage.getItem('shadow-job');
 if(saved){try{const r=await api(`/api/jobs/${saved}`);if(r.status==='done'){await displayResult(saved,r);$('status').textContent='Готово'}else localStorage.removeItem('shadow-result')}catch(e){localStorage.removeItem('shadow-result');error(e)}}
 if(active){job=active;busy(true);await poll(active)}
}
async function poll(id){
 try{
  const r=await api(`/api/jobs/${id}`);if(job!==id)return;$('status').textContent=r.stage;
  if(['running','canceling'].includes(r.status)){busy(true);if(r.status==='canceling')$('cancel').disabled=true;setTimeout(()=>poll(id),1000);return}
  job=null;localStorage.removeItem('shadow-job');busy(false);
  if(r.status==='done'){await displayResult(id,r);$('status').textContent='Готово'}
  else if(r.status==='error'){$('status').textContent='Расчёт не завершён';error(Error(r.error))}
  else $('status').textContent='Расчёт отменён'
 }catch(e){if(job===id){job=null;localStorage.removeItem('shadow-job');busy(false)}error(e)}
}
async function show(){for(const b of $('tabs').children)b.classList.toggle('selected',b.dataset.view===view);$('model').hidden=view!=='model';$('preview').hidden=view==='model';if(!result)return;const base=`/api/jobs/${result}/`;if(view==='model'){if(viewer)await viewer.load(base+$('part').value)}else $('preview').src=base+view}
$('tabs').onclick=e=>{if(e.target.dataset.view){view=e.target.dataset.view;show().catch(error)}};$('part').onchange=()=>{view='model';show().catch(error)};$('reset-view').onclick=()=>viewer?.reset();
await init();

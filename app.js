// ===== Thai Re-entry Predictor app.js v4.0 =====
// หมายเหตุ: ผลลัพธ์เป็นการประมาณจากข้อมูล TLE สาธารณะ ไม่ใช่การพยากรณ์ระดับปฏิบัติการ
const DAY=86400000,HOUR=3600000;
const MU_A=42241.1;            // กม. : a = MU_A / n^(2/3)  (n = รอบ/วัน)
const RE_KM=6378.137;
const REENTRY_ALT=120;         // กม. ระดับที่ถือว่าเข้าสู่ชั้นบรรยากาศหนาแน่น (entry interface)
const REBOOST_KM=0.8;          // ความสูงเพิ่มขึ้นเกินนี้ระหว่าง TLE ติดกัน = ถือว่ามีการเร่งวงโคจร
const MIN_REAL_TLE=4;          // จำนวน TLE จริงขั้นต่ำที่ใช้ฟิตแนวโน้ม
const estAlt=mm=>MU_A/Math.pow(mm,2/3)-RE_KM;
const REENTRY_MM=Math.pow(MU_A/(REENTRY_ALT+RE_KM),1.5); // ≈16.57 รอบ/วัน

// ---------- API ----------
const PROXIES=[u=>u,u=>'https://api.codetabs.com/v1/proxy/?quest='+encodeURIComponent(u),u=>'https://api.allorigins.win/raw?url='+encodeURIComponent(u)];
let proxyPref=0;
// accept(text) ต้องคืนค่า true จึงจะถือว่าข้อมูลใช้ได้ (กัน proxy ส่งข้อมูลเพี้ยน)
async function fetchText(url,ms,accept){
  const order=[0,1,2].map(i=>(proxyPref+i)%3);let err;
  for(const idx of order){
    const c=new AbortController();const t=setTimeout(()=>c.abort(),ms||8000);
    try{
      const r=await fetch(PROXIES[idx](url),{signal:c.signal});
      if(!r.ok){err=new Error('HTTP '+r.status);continue;}
      const txt=await r.text();
      if(accept&&!accept(txt)){err=new Error('ข้อมูลไม่ถูกต้อง');continue;}
      proxyPref=idx;return txt;
    }catch(e){err=e;}finally{clearTimeout(t);}
  }
  throw err||new Error('fetch fail');
}
function tleChecksumOK(line){let s=0;for(let i=0;i<68;i++){const ch=line[i];if(ch>='0'&&ch<='9')s+=+ch;else if(ch==='-')s++;}return s%10===parseInt(line[68],10);}
function validTLE(l1,l2,id){
  return !!(l1&&l2&&l1.length>=69&&l2.length>=69&&l1[0]==='1'&&l2[0]==='2'&&tleChecksumOK(l1)&&tleChecksumOK(l2)
    &&parseInt(l1.substring(2,7),10)===parseInt(id,10)&&parseInt(l2.substring(2,7),10)===parseInt(id,10));
}
function parseTLEEpoch(l1){const s=l1.substring(18,32);const yy=parseInt(s.substring(0,2),10);const y=yy>56?1900+yy:2000+yy;const d=parseFloat(s.substring(2));return Date.UTC(y,0,1)+(d-1)*DAY;}
function parseTLEBulk(text,id){
  const L=text.split('\n').map(l=>l.trim()).filter(Boolean);const out=[];
  for(let i=0;i<L.length-1;i++){
    if(L[i].startsWith('1 ')&&L[i+1].startsWith('2 ')&&validTLE(L[i],L[i+1],id)){
      const nm=(i>0&&!L[i-1].startsWith('1 ')&&!L[i-1].startsWith('2 '))?L[i-1]:'UNKNOWN';
      out.push({name:nm,line1:L[i],line2:L[i+1],epoch:parseTLEEpoch(L[i])});
    }
  }
  out.sort((a,b)=>a.epoch-b.epoch);
  const seen=new Set();return out.filter(o=>{const k=Math.round(o.epoch/HOUR);if(seen.has(k))return false;seen.add(k);return true;});
}
const HKEY=id=>'reentry_hist_'+id,CKEY=id=>'reentry_cache_'+id;
function loadLocalHist(id){try{return JSON.parse(localStorage.getItem(HKEY(id)))||[];}catch(e){return[];}}
function saveLocalHist(id,arr){try{localStorage.setItem(HKEY(id),JSON.stringify(arr.slice(-200)));}catch(e){}}
function collectTLE(id,tle){const arr=loadLocalHist(id);if(!arr.some(x=>Math.abs(x.epoch-tle.epoch)<HOUR)){arr.push(tle);arr.sort((a,b)=>a.epoch-b.epoch);saveLocalHist(id,arr);}return arr;}
function loadCache(id){try{return JSON.parse(localStorage.getItem(CKEY(id)));}catch(e){return null;}}
function saveCache(id,tle){try{localStorage.setItem(CKEY(id),JSON.stringify({t:Date.now(),tle:tle}));}catch(e){}}
async function fetchTLE(id){
  let tle=null;
  try{
    const t=await fetchText('https://celestrak.org/NORAD/elements/gp.php?CATNR='+id+'&FORMAT=tle',8000,
      txt=>parseTLEBulk(txt,id).length>0||/No GP data/i.test(txt));
    const a=parseTLEBulk(t,id);if(a.length)tle=a[a.length-1];
  }catch(e){console.warn('Celestrak failed:',e.message);}
  if(!tle){
    try{
      const r=await fetch('https://tle.ivanstanojevic.me/api/tle/'+id);
      if(!r.ok)throw new Error('ไม่พบวัตถุรหัส '+id);
      const j=await r.json();let l1,l2;
      if(Array.isArray(j.tle)){l1=j.tle[0];l2=j.tle[1];}else if(j.tle){l1=j.tle.line1;l2=j.tle.line2;}else{l1=j.line1;l2=j.line2;}
      if(!validTLE(l1,l2,id))throw new Error('รูปแบบข้อมูลผิด');
      tle={name:j.name||('NORAD '+id),line1:l1,line2:l2,epoch:parseTLEEpoch(l1)};
    }catch(e){throw new Error('ไม่สามารถดึงข้อมูล NORAD '+id+' ได้ (ไม่พบวัตถุ หรือเครือข่ายมีปัญหา)');}
  }
  saveCache(id,tle);return tle;
}
// ประวัติ "จริง" เท่านั้น: CelesTrak ให้ TLE ล่าสุดชุดเดียว จึงสะสมเก็บในเครื่องทีละครั้งที่เปิดแอป
async function fetchTLEHistory(id,days,ttl){
  const c=loadCache(id);let cur=null,offline=false;
  if(c&&c.tle&&Date.now()-c.t<ttl)cur=c.tle;
  else{
    try{cur=await fetchTLE(id);}
    catch(e){if(c&&c.tle&&Date.now()-c.t<3*DAY){cur=c.tle;offline=true;}else throw e;}
  }
  const all=collectTLE(id,cur);
  const cut=Date.now()-days*DAY;
  const hist=all.filter(x=>x.epoch>=cut);
  return{history:hist.length?hist:[cur],latest:cur,source:offline?'offline':'live',count:hist.length};
}
async function fetchSpaceWeather(){
  try{
    const d=JSON.parse(await fetchText('https://services.swpc.noaa.gov/json/planetary_k_index_1m.json',5000));
    const l=d[d.length-1];
    const v=Array.isArray(l)?parseFloat(l[1]):parseFloat(l.kp_index!=null?l.kp_index:(l.estimated_kp!=null?l.estimated_kp:l.kp));
    return{kp:isFinite(v)?v:null};
  }catch(e){return{kp:null};}
}

// ---------- ORBIT HELPERS ----------
const exMM=l2=>parseFloat(l2.substring(52,63));
const exInc=l2=>parseFloat(l2.substring(8,16));
const exEcc=l2=>parseFloat('0.'+l2.substring(26,33).trim());
function exBStar(l1){try{const s=l1.substring(53,61).trim();if(!s)return 0;const exp=parseInt(s.slice(-2),10);const man=parseInt(s.slice(0,-2),10)/1e5;if(!isFinite(exp)||!isFinite(man))return 0;return man*Math.pow(10,exp);}catch(e){return 0;}}

// ---------- DECAY MODEL ----------
// ความสูงสเกลของความหนาแน่นบรรยากาศ H(h) โดยประมาณ (กม.) — ขึ้นกับกิจกรรมดวงอาทิตย์ จึงใส่ใน uncertainty
const H_TAB=[[120,15],[150,25],[200,33],[300,45],[400,57],[500,68],[600,80],[800,110],[1500,200]];
function scaleH(h){
  if(h<=H_TAB[0][0])return H_TAB[0][1];
  for(let i=0;i<H_TAB.length-1;i++){if(h<=H_TAB[i+1][0]){const f=(h-H_TAB[i][0])/(H_TAB[i+1][0]-H_TAB[i][0]);return H_TAB[i][1]+(H_TAB[i+1][1]-H_TAB[i][1])*f;}}
  return H_TAB[H_TAB.length-1][1];
}
// สมมติ dh/dt = -r0·exp(-(h-h0)/H)  ⇒  exp((h-hL)/H) = 1 + s·x   (x = วันย้อนหลังจากจุดล่าสุด, s = r0/H)
// ฟิตค่า s จากข้อมูลจริง แล้วหาเวลาที่ h ลงถึง REENTRY_ALT :  t = (1 - exp(-(hL-h_end)/H)) / s
function scaleHeightFit(w,opt){
  const last=w[w.length-1],H=scaleH(last.h);
  const pts=w.filter(p=>p.h-last.h<=2.5*H);
  if(pts.length<opt.minPts)return null;
  const drop=pts[0].h-last.h;
  if(drop<0.1)return null;
  let sy=0,sx=0;
  for(const p of pts){const x=(last.t-p.t)/DAY;if(x<=0)continue;sy+=Math.exp((p.h-last.h)/H)-1;sx+=x;}
  if(sx<=0)return null;
  const s=sy/sx; // น้ำหนัก 1/x : จุดใกล้ปัจจุบันมีผลมากกว่า
  if(!(s>1e-9))return null;
  let tDays=last.h<=REENTRY_ALT?0:(1-Math.exp(-(last.h-REENTRY_ALT)/H))/s;
  let ss=0,k=0;
  for(const p of pts){const x=(last.t-p.t)/DAY;if(x<=0)continue;const hp=last.h+H*Math.log(1+s*x);ss+=(hp-p.h)**2;k++;}
  const rms=k?Math.sqrt(ss/k):0;
  const relU=0.25+(pts.length<6?0.1:0)+Math.min(0.3,rms/Math.max(0.2,drop));
  return{t:tDays*DAY,time:last.t+tDays*DAY,rms:rms,drop:drop,nPts:pts.length,H:H,uncertainty:Math.max(3*HOUR,tDays*DAY*relU)};
}
// ตารางหยาบ ใช้เฉพาะตอนยังไม่มีประวัติ TLE จริงพอ
function estRemainingDays(alt,bstar){
  const pts=[[150,0.5],[180,1.5],[200,4],[220,8],[250,25],[300,90],[350,270],[400,730],[450,1825],[500,3650],[600,9125],[800,20000]];
  let days=20000;
  if(alt<=pts[0][0])days=pts[0][1];
  else{for(let i=0;i<pts.length-1;i++){if(alt<=pts[i+1][0]){const f=(alt-pts[i][0])/(pts[i+1][0]-pts[i][0]);days=pts[i][1]+(pts[i+1][1]-pts[i][1])*f;break;}}}
  const bs=Math.max(Math.abs(bstar),1e-5);
  return days*Math.min(5,Math.max(0.2,1e-3/bs));
}
function predictReentry(res,weather){
  const hist=res.history,last=hist[hist.length-1];
  const kp=weather&&weather.kp!=null?weather.kp:null;
  const base={inclination:exInc(last.line2),bstar:exBStar(last.line1),ecc:exEcc(last.line2),currentAltitude:estAlt(exMM(last.line2)),
    tleAge:(Date.now()-last.epoch)/HOUR,nPts:hist.length,kp:kp,models:[]};
  if(base.ecc>0.1)return Object.assign(base,{status:'unsupported',note:'วงโคจรรีมาก (e='+base.ecc.toFixed(2)+') โมเดลนี้ไม่รองรับ'});
  const ser=hist.map(h=>({t:h.epoch,h:estAlt(exMM(h.line2))}));
  const w=ser.filter(p=>p.t>=last.epoch-30*DAY);
  // ตรวจการเร่งวงโคจร (ความสูงเพิ่มขึ้นผิดธรรมชาติ)
  for(let i=1;i<w.length;i++){
    if(w[i].h-w[i-1].h>REBOOST_KM)return Object.assign(base,{status:'maneuver',note:'🛰️ ตรวจพบการเร่งวงโคจร (ความสูงเพิ่มขึ้น '+(w[i].h-w[i-1].h).toFixed(1)+' กม.) → ไม่ใช่การตกตามธรรมชาติ'});
  }
  if(w.length<MIN_REAL_TLE){
    const days=estRemainingDays(base.currentAltitude,base.bstar);
    return Object.assign(base,{status:'estimate',method:'table',days:days,
      rangeLo:last.epoch+days*0.4*DAY,rangeHi:last.epoch+days*2.5*DAY,time:last.epoch+days*DAY,
      horizonDays:(last.epoch+days*DAY-Date.now())/DAY,quality:15,
      note:'ยังมี TLE จริงเพียง '+w.length+'/'+MIN_REAL_TLE+' ชุด → แสดงเป็นช่วงประมาณจากตารางทั่วไป ใช้ตัดสินใจไม่ได้ (เปิดแอปสะสมข้อมูลต่อเนื่อง)'});
  }
  const lng=scaleHeightFit(w,{minPts:MIN_REAL_TLE});
  const wS=w.filter(p=>last.epoch-p.t<=3*DAY).slice(-5);
  const sht=wS.length>=3?scaleHeightFit(wS,{minPts:3}):null;
  const ms=[];
  if(lng)ms.push({name:'scale-height (ยาว, '+lng.nPts+' จุด)',r:lng,wt:1});
  if(sht&&(!lng||sht.t<5*DAY))ms.push({name:'scale-height (สั้น, '+sht.nPts+' จุด)',r:sht,wt:lng?1.5:1});
  if(!ms.length){
    if(last.epoch-w[0].t>=2*DAY)return Object.assign(base,{status:'stable',note:'ไม่พบการสลายตัวที่วัดได้ในข้อมูลที่มี (อาจควบคุมวงโคจร หรืออยู่สูงมาก)'});
    const days=estRemainingDays(base.currentAltitude,base.bstar);
    return Object.assign(base,{status:'estimate',method:'table',days:days,rangeLo:last.epoch+days*0.4*DAY,rangeHi:last.epoch+days*2.5*DAY,time:last.epoch+days*DAY,
      horizonDays:(last.epoch+days*DAY-Date.now())/DAY,quality:15,note:'ช่วงข้อมูลยังสั้นเกินไปที่จะวัดแนวโน้ม → แสดงเป็นช่วงประมาณ'});
  }
  let tw=0,tt=0;ms.forEach(m=>{tw+=m.wt;tt+=m.r.time*m.wt;});
  const time=tt/tw;
  const times=ms.map(m=>m.r.time);
  const spread=ms.length>1?Math.max(...times)-Math.min(...times):0;
  let unc=Math.max(...ms.map(m=>m.r.uncertainty),spread*0.5,3*HOUR);
  if(kp!=null&&kp>=5)unc*=1.25;
  const primary=ms[0].r;
  const tRel=Math.max(HOUR,time-last.epoch);
  let q=25+Math.min(30,primary.nPts*3)+15*(1-Math.min(1,primary.rms/(0.5*primary.drop+0.3)))+(ms.length>1?15*(1-Math.min(1,spread/Math.max(HOUR,tRel*0.5))):7);
  const hd=(time-Date.now())/DAY;
  q*=hd>365?0.5:hd>45?0.7:hd>7?0.9:1;
  q=Math.max(5,Math.min(90,q));
  return Object.assign(base,{status:'ok',method:'scale-height',time:time,uncertainty:unc,quality:q,horizonDays:hd,
    models:ms.map(m=>({name:m.name})),spread:spread});
}

// ---------- LEARNING (บันทึกผลจริง → สถิติความแม่นยำ ไม่ปรับโมเดลอัตโนมัติ) ----------
class Learning{
  constructor(){try{this.d=JSON.parse(localStorage.getItem('reentry_learn'))||this.blank();}catch(e){this.d=this.blank();}if(!Array.isArray(this.d.predictions))this.d=this.blank();}
  blank(){return{predictions:[]};}
  save(){try{localStorage.setItem('reentry_learn',JSON.stringify(this.d));}catch(e){}}
  record(id,p){
    const now=Date.now();
    for(let i=this.d.predictions.length-1;i>=0;i--){
      const x=this.d.predictions[i];
      if(x.noradId==id&&x.status==='pending'){
        if(now-x.timestamp<6*HOUR){x.predictedTime=p.time;x.quality=p.quality;x.uncertainty=p.uncertainty;x.timestamp=now;this.save();return;}
        break;
      }
    }
    this.d.predictions.push({noradId:String(id),predictedTime:p.time,quality:p.quality,uncertainty:p.uncertainty,timestamp:now,status:'pending',actualTime:null,error:null});
    if(this.d.predictions.length>300)this.d.predictions.shift();
    this.save();
  }
  recordActual(id,actual){
    let n=0;
    this.d.predictions.forEach(p=>{
      if(p.noradId==id&&p.status==='pending'){p.status='matched';p.actualTime=actual;p.error=actual-p.predictedTime;p.lead=actual-p.timestamp;n++;}
    });
    this.save();return n;
  }
  pendingIds(){return Array.from(new Set(this.d.predictions.filter(p=>p.status==='pending').map(p=>p.noradId)));}
  stats(){
    const m=this.d.predictions.filter(p=>p.status==='matched'&&p.error!=null);
    const objs=new Set(m.map(p=>p.noradId)).size;
    const inside=m.length?m.filter(p=>Math.abs(p.error)<=(p.uncertainty||0)).length/m.length*100:0;
    const buckets=[['<1 วัน',0,DAY],['1–7 วัน',DAY,7*DAY],['>7 วัน',7*DAY,1e15]].map(b=>{
      const s=m.filter(p=>p.lead>=b[1]&&p.lead<b[2]);
      return{label:b[0],n:s.length,mae:s.length?s.reduce((a,p)=>a+Math.abs(p.error),0)/s.length/HOUR:null};
    });
    return{matched:m.length,objects:objs,pending:this.pendingIds().length,inside:inside,buckets:buckets,progress:Math.min(100,objs/10*100)};
  }
  history(){return this.d.predictions.slice(-20).reverse();}
  clear(){this.d=this.blank();this.save();}
}
const LS=new Learning();

// ---------- STATE ----------
let watchlist=[];try{watchlist=JSON.parse(localStorage.getItem('reentry_watch'))||[];}catch(e){watchlist=[];}
let summaries={},lastWeather={kp:null};
let curNorad=localStorage.getItem('lastNorad')||'25544';
let map=null,orbitGroup=null,decayChart=null,probChart=null,tileFailed=false,lastMapObj=null;
let simMap=null,simLayer=null;
let refreshing=false,pendingRefresh=false,lastManual=0;
function saveWatch(){try{localStorage.setItem('reentry_watch',JSON.stringify(watchlist.slice(0,10)));}catch(e){}}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function addToWatch(id){
  id=String(parseInt(id,10));
  if(!id||id==='NaN'||id==='0'){toast('กรอกเลข NORAD 1-99999');return false;}
  if(watchlist.length>=10&&!watchlist.includes(id)){toast('ติดตามได้สูงสุด 10 วัตถุ');return false;}
  if(!watchlist.includes(id))watchlist.push(id);
  saveWatch();return true;
}
function removeFromWatch(id){
  watchlist=watchlist.filter(x=>x!==id);saveWatch();
  if(curNorad===id)curNorad=watchlist[0]||id;
  refreshAll(false);
}
function selectObject(id){
  curNorad=id;localStorage.setItem('lastNorad',id);
  document.getElementById('noradInput').value=id;
  const s=summaries[id];
  if(s&&s.pred&&s.res)render(s.pred,s.res,lastWeather);else refreshAll(false);
  window.scrollTo({top:0,behavior:'smooth'});
}
document.addEventListener('DOMContentLoaded',()=>{
  const wl=document.createElement('section');
  wl.className='glass rounded-xl p-4';
  wl.innerHTML='<div class="flex items-center justify-between mb-3"><h3 class="font-semibold">📡 รายการติดตาม</h3><span id="wlCount" class="text-xs text-gray-400">0/10</span></div><div id="watchlist" class="space-y-2"></div>';
  document.getElementById('noradInput').closest('section').after(wl);
  if(!watchlist.includes(curNorad))watchlist.push(curNorad);
  saveWatch();
  document.getElementById('noradInput').value=curNorad;
  const apiField=document.getElementById('apiBaseUrl');if(apiField)apiField.value=localStorage.getItem('reentryApiBase')||'';
  map=L.map('map').setView([10,100],3);
  const esri=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Tiles © Esri',maxZoom:18}).addTo(map);
  esri.on('tileerror',()=>{if(!tileFailed){tileFailed=true;map.removeLayer(esri);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap',maxZoom:18}).addTo(map);}});
  orbitGroup=L.layerGroup().addTo(map);
  setTimeout(()=>map.invalidateSize(),300);
  // ล้าง Service Worker เก่า (ถ้ามี) แทนการลงทะเบียนใหม่ — กันหน้ารีโหลดวน
  if('serviceWorker' in navigator){try{navigator.serviceWorker.getRegistrations().then(rs=>rs.forEach(r=>r.unregister())).catch(()=>{});}catch(e){}}
  renderLearning();
  refreshAll(false);
  setInterval(()=>refreshAll(false),15*60000);
});
function predictFromInput(){
  const v=document.getElementById('noradInput').value.trim();
  if(addToWatch(v)){curNorad=String(parseInt(v,10));localStorage.setItem('lastNorad',curNorad);refreshAll(false);}
}
// อัตโนมัติ: ใช้แคช TLE ≤2 ชม. (ข้อมูลต้นทางอัปเดตราวทุก 2 ชม.) / กดปุ่ม 🔄: ข้ามแคชได้ถ้าเก่ากว่า 10 นาที
async function refreshAll(manual){
  if(refreshing){pendingRefresh=true;return;}
  refreshing=true;
  const st=document.getElementById('status');
  try{
    let ttl=2*HOUR;
    if(manual===true&&Date.now()-lastManual>10*60000){ttl=10*60000;lastManual=Date.now();}
    if(!watchlist.length){noPred();horizonNote('','hidden');renderWatchlist();st.textContent='ไม่มีรายการติดตาม';st.className='text-gray-400';return;}
    st.textContent='กำลังอัปเดต '+watchlist.length+' วัตถุ...';st.className='text-yellow-400';
    try{lastWeather=await fetchSpaceWeather();}catch(e){}
    let okCount=0;
    for(const id of watchlist){
      try{
        const res=await fetchTLEHistory(id,30,ttl);
        const pred=predictReentry(res,lastWeather);
        summaries[id]={res:res,pred:pred};
        if(pred.status==='ok'&&pred.horizonDays>0&&pred.horizonDays<=45)LS.record(id,pred);
        okCount++;
      }catch(e){summaries[id]={error:e.message};}
      await new Promise(r=>setTimeout(r,300));
    }
    renderWatchlist();
    const s=summaries[curNorad];
    if(s&&s.pred&&s.res)render(s.pred,s.res,lastWeather);
    else{noPred();horizonNote('','hidden');toast('ดึงข้อมูลไม่ได้: '+((s&&s.error)||'ไม่ทราบสาเหตุ'));}
    renderLearning();
    st.textContent='อัปเดต '+okCount+'/'+watchlist.length+' วัตถุ '+new Date().toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'});
    st.className='text-green-400';
  }finally{
    refreshing=false;
    if(pendingRefresh){pendingRefresh=false;refreshAll(false);}
  }
}

// ---------- RENDER ----------
const DT_OPT={day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'};
const fmtDT=(ms,tz)=>new Date(ms).toLocaleString('th-TH',Object.assign({timeZone:tz},DT_OPT));
const fmtD=(ms,tz)=>new Date(ms).toLocaleDateString('th-TH',{day:'numeric',month:'short',year:'numeric',timeZone:tz});
const fmtMY=(ms,tz)=>new Date(ms).toLocaleDateString('th-TH',{month:'short',year:'numeric',timeZone:tz});
function fmtUnc(ms){const h=ms/HOUR;if(h<48)return'± '+h.toFixed(1)+' ชม.';const d=h/24;if(d<90)return'± '+Math.round(d)+' วัน';return'± '+Math.round(d/30)+' เดือน';}
function setText(id,v){const el=document.getElementById(id);if(el)el.textContent=v;}
function horizonNote(text,color){let el=document.getElementById('horizonNote');if(!el){el=document.createElement('p');el.id='horizonNote';document.getElementById('confidenceBadge').closest('section').appendChild(el);}el.textContent=text;el.className='text-xs mt-3 '+color;}
function noPred(){['utcTime','utcUncertainty','ictTime','ictUncertainty','impactZone'].forEach(id=>setText(id,'-'));const b=document.getElementById('confidenceBadge');b.textContent='N/A';b.className='text-xs px-2 py-1 rounded-full bg-gray-600';}
function latBand(inc){return inc>90?180-inc:inc;}
function renderWatchlist(){
  const box=document.getElementById('watchlist'),cnt=document.getElementById('wlCount');
  if(!box)return;
  if(cnt)cnt.textContent=watchlist.length+'/10';
  const key=id=>{const s=summaries[id];return(s&&s.pred&&s.pred.status==='ok')?s.pred.horizonDays:1e9;};
  const ids=watchlist.slice().sort((a,b)=>key(a)-key(b));
  if(!ids.length){box.innerHTML='<p class="text-xs text-gray-400 text-center py-2">ยังไม่มีรายการ - กรอก NORAD แล้วกด 🔍</p>';return;}
  box.innerHTML=ids.map(id=>{
    const s=summaries[id],sel=(id===curNorad);
    let name='...',altTxt='-',whenTxt='...',badge='⏳',conf='';
    if(s&&s.error){whenTxt='ดึงข้อมูลไม่ได้';badge='❌';}
    else if(s&&s.pred){
      const p=s.pred;
      name=esc(s.res.latest.name||('NORAD '+id));
      altTxt=p.currentAltitude.toFixed(0)+' กม.';
      if(p.status==='ok'){
        const hd=p.horizonDays;
        if(hd>3650){whenTxt='>10 ปี';badge='⚪';}
        else if(hd>365){whenTxt=fmtMY(p.time);badge='🟡';}
        else{whenTxt=fmtD(p.time);badge=hd<=30?'🔥':'🟠';}
        conf='คุณภาพ '+Math.round(p.quality)+'%';
      }else if(p.status==='estimate'){whenTxt='ประมาณ (ข้อมูลน้อย)';badge='❔';}
      else if(p.status==='unsupported'){whenTxt='ไม่รองรับวงโคจรนี้';badge='➖';}
      else{whenTxt='ควบคุมได้/ไม่สลายตัว';badge='🛰️';}
    }else{whenTxt='กำลังคำนวณ...';}
    return '<div class="rounded-lg p-3" style="background:rgba(255,255,255,.06);border:1px solid '+(sel?'#3b82f6':'rgba(255,255,255,.08)')+'" onclick="selectObject(\''+id+'\')">'
      +'<div class="flex justify-between items-center">'
      +'<div class="min-w-0"><div class="font-semibold text-sm truncate">'+name+' <span class="text-gray-400 text-xs">#'+id+'</span></div>'
      +'<div class="text-xs text-gray-400">'+altTxt+' • คาดตก: '+whenTxt+' '+conf+'</div></div>'
      +'<div class="flex items-center space-x-1 shrink-0"><span>'+badge+'</span>'
      +'<button class="text-red-400 px-2 py-1" onclick="event.stopPropagation();removeFromWatch(\''+id+'\')">✖</button></div>'
      +'</div></div>';
  }).join('');
}
function render(p,res,weather){
  const hd=p.horizonDays;
  let note='',nc='text-yellow-300',tU='-',tI='-',uU='-',uI='-',badge='N/A',bcls='bg-gray-600';
  if(p.status==='ok'){
    if(hd>3650){tU=tI='> 10 ปี';uU=uI='±หลายปี';note='🛰️ ยังอยู่สูง (~'+p.currentAltitude.toFixed(0)+' กม.) → อาจนานกว่า 10 ปี';}
    else{
      const f=hd>365?fmtMY:(hd>60?fmtD:fmtDT);
      tU=f(p.time,'UTC');tI=f(p.time,'Asia/Bangkok');uU=uI=fmtUnc(p.uncertainty);
      if(hd<0){note='⚠️ เวลาที่คำนวณได้ผ่านไปแล้ว — อาจตกไปแล้วหรือ TLE ล้าสมัย';nc='text-red-300';}
      else if(hd<3){note='🔥 คาดตกภายในไม่กี่วัน ติดตามอย่างใกล้ชิด';nc='text-red-300 font-bold';}
      else if(hd<7){note='🔥 คาดตกภายในสัปดาห์นี้';nc='text-red-300';}
      else if(hd<30){note='🔥 กำลังลดระดับเร็ว ติดตามอัปเดตรายวัน';nc='text-orange-300';}
      else if(hd>730){note='⏳ พยากรณ์ระยะไกลมาก (~'+(hd/365).toFixed(1)+' ปี) ขึ้นกับกิจกรรมดวงอาทิตย์ ±หลายปี';}
      else if(hd>365){note='📅 พยากรณ์ระยะไกล (>1 ปี) ±หลายเดือน';}
      else if(hd>30){note='🔭 พยากรณ์ระยะกลาง ±สัปดาห์-เดือน';nc='text-blue-300';}
      if(hd>=0&&hd<30&&p.tleAge>72)note+=' (TLE อายุ '+(p.tleAge/24).toFixed(1)+' วัน)';
      if(hd<=365&&hd>30&&p.currentAltitude>350)note+=' • ถ้าไม่มีการเร่งวงโคจร';
    }
    badge='คุณภาพ '+Math.round(p.quality)+'%';
    bcls=p.quality>=65?'bg-green-500':p.quality>=40?'bg-yellow-500':'bg-red-500';
  }else if(p.status==='estimate'){
    const hiDays=(p.rangeHi-Date.now())/DAY;
    const f=hiDays>3650?fmtMY:fmtD;
    const rng=p.days>3650?'> 10 ปี':fmtMY(Math.max(p.rangeLo,Date.now()),'UTC')+' – '+(hiDays>3650?'> 10 ปี':f(p.rangeHi,'UTC'));
    tU=tI=rng;uU=uI='ประมาณหยาบ';note='⚠️ '+p.note;nc='text-orange-300';badge='ประมาณ';bcls='bg-gray-600';
  }else{
    tU=tI='N/A';uU=uI='-';note=p.note||'';nc='text-red-300';
  }
  horizonNote(note,nc);
  setText('utcTime',tU);setText('ictTime',tI);setText('utcUncertainty',uU);setText('ictUncertainty',uI);
  const b=document.getElementById('confidenceBadge');b.textContent=badge;b.className='text-xs px-2 py-1 rounded-full '+bcls;
  const inc=p.inclination;const band=latBand(inc);
  setText('impactZone','แนวผ่านละติจูด ±'+band.toFixed(0)+'° (ระบุจุดตกไม่ได้)'+(band>=6?' • ครอบคลุมละติจูดไทย แต่ไม่ได้แปลว่าจะตกที่ไทย':''));
  setText('noradId',curNorad);
  setText('inclination',inc.toFixed(2)+'°');
  setText('currentAlt',p.currentAltitude.toFixed(1)+' กม.');
  setText('bstar',p.bstar?p.bstar.toExponential(2):'-');
  setText('kpIndex',p.kp!=null?p.kp.toFixed(1):'-');
  setText('tleAge',p.tleAge.toFixed(1)+' ชม.');
  setText('ensembleModels',p.models&&p.models.length?p.models.map(m=>m.name).join(', '):(p.method==='table'?'ตารางหยาบ':'-'));
  const real=res.history.length;
  const src=res.source==='offline'?'📴 ออฟไลน์: แคช + ประวัติในเครื่อง ':'🌐 CelesTrak (ล่าสุด) + ประวัติสะสมในเครื่อง ';
  setText('dataSource',src+real+' ชุด'+(real<MIN_REAL_TLE?' (น้อยกว่า '+MIN_REAL_TLE+' → ใช้ค่าประมาณ)':''));
  renderLearning();
  setTimeout(()=>{
    try{drawDecay(res.history);}catch(e){console.error(e);}
    try{drawProb(p);}catch(e){console.error(e);}
    try{drawMap(res,p);}catch(e){console.error(e);}
  },100);
}
function splitAntimeridian(pts){if(pts.length<2)return[];const segs=[];let cur=[pts[0]];for(let i=1;i<pts.length;i++){if(Math.abs(pts[i][1]-pts[i-1][1])>180){segs.push(cur);cur=[pts[i]];}else cur.push(pts[i]);}segs.push(cur);return segs.filter(s=>s.length>1);}
function drawMap(res,p){
  try{
    orbitGroup.clearLayers();
    const t=res.latest;
    const rec=satellite.twoline2satrec(t.line1,t.line2);
    const now=Date.now(),pts=[];let cur=null;
    // 30-second SGP4 sampling: denser computed positions, not cosmetic curve smoothing.
    const STEP_MS=30*1000,MAX_POINTS=2880;
    for(let i=0;i<MAX_POINTS;i++){
      const tm=new Date(now+i*STEP_MS);
      try{
        const pv=satellite.propagate(rec,tm);
        if(!pv.position)break;
        const gd=satellite.eciToGeodetic(pv.position,satellite.gstime(tm));
        if(gd.height<REENTRY_ALT)break;
        const la=satellite.degreesLat(gd.latitude),lo=satellite.degreesLong(gd.longitude);
        pts.push([la,lo]);
        if(i===0)cur={la:la,lo:lo};
      }catch(e){break;}
    }
    const band=latBand(p.inclination);
    [band,-band].forEach(b=>L.polyline([[b,-180],[b,180]],{color:'#ef4444',weight:1,opacity:.6,dashArray:'6 6'}).addTo(orbitGroup));
    splitAntimeridian(pts).forEach(s=>{L.polyline(s,{color:'#fbbf24',weight:2,opacity:.8}).addTo(orbitGroup);});
    if(cur){
      L.circleMarker([cur.la,cur.lo],{radius:8,fillColor:'#3b82f6',color:'#fff',weight:2,fillOpacity:.9}).addTo(orbitGroup);
      if(lastMapObj!==curNorad){map.setView([cur.la,cur.lo],3);lastMapObj=curNorad;}
    }
  }catch(e){console.error('drawMap error:',e);}
}
function labelsFor(hist){
  const span=hist.length>1?hist[hist.length-1].epoch-hist[0].epoch:0;
  return hist.map(h=>new Date(h.epoch).toLocaleString('th-TH',span<3*DAY?{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short'}));
}
function drawDecay(hist){
  const ctx=document.getElementById('decayChart').getContext('2d');
  if(decayChart)decayChart.destroy();
  decayChart=new Chart(ctx,{type:'line',data:{labels:labelsFor(hist),datasets:[
    {label:'ความสูงเฉลี่ย (กม.) จาก TLE จริง',data:hist.map(h=>estAlt(exMM(h.line2))),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.1)',tension:.3,fill:true,pointRadius:hist.length<15?3:0},
    {label:'ระดับตก ~'+REENTRY_ALT+' กม.',data:hist.map(()=>REENTRY_ALT),borderColor:'#ef4444',borderDash:[6,6],pointRadius:0,fill:false}
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#fff'}}},scales:{y:{ticks:{color:'#9ca3af'},grid:{color:'rgba(255,255,255,.1)'}},x:{ticks:{color:'#9ca3af',maxRotation:45},grid:{color:'rgba(255,255,255,.1)'}}}}});
}
function drawProb(p){
  const ctx=document.getElementById('probabilityChart').getContext('2d');
  if(probChart){probChart.destroy();probChart=null;}
  if(p.status!=='ok'||p.horizonDays>365)return;
  const uncH=p.uncertainty/HOUR;
  const half=Math.max(12,Math.ceil(uncH*1.5));
  const step=Math.max(1,Math.round(half/12));
  const longSpan=half*2>48;
  const lb=[],dt=[];
  for(let i=-half;i<=half;i+=step){
    const d=new Date(p.time+i*HOUR);
    lb.push(longSpan?d.toLocaleString('th-TH',{day:'numeric',month:'short',hour:'2-digit',timeZone:'UTC'}):d.getUTCHours()+':00');
    dt.push(Math.exp(-0.5*Math.pow((i*HOUR)/p.uncertainty,2))*100);
  }
  const g=ctx.createLinearGradient(0,0,0,180);g.addColorStop(0,'rgba(59,130,246,.8)');g.addColorStop(1,'rgba(59,130,246,.1)');
  probChart=new Chart(ctx,{type:'line',data:{labels:lb,datasets:[{label:'ความเป็นไปได้สัมพัทธ์ (UTC, ไม่ใช่ความน่าจะเป็นที่สอบเทียบแล้ว)',data:dt,borderColor:'#3b82f6',backgroundColor:g,tension:.4,fill:true,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#fff'}}},scales:{y:{beginAtZero:true,max:100,ticks:{color:'#9ca3af',callback:v=>v+'%'},grid:{color:'rgba(255,255,255,.1)'}},x:{ticks:{color:'#9ca3af',maxRotation:45},grid:{color:'rgba(255,255,255,.1)'}}}}});
}

// ---------- LEARNING UI ----------
function renderLearning(){
  const s=LS.stats();
  const bar=document.getElementById('learningProgress');if(bar)bar.style.width=s.progress+'%';
  setText('learningPercent',s.objects+'/10');
  const lines=['บันทึกผลจริงแล้ว '+s.objects+' วัตถุ ('+s.matched+' การพยากรณ์) | รอผล '+s.pending+' วัตถุ'];
  if(s.matched)lines.push('อยู่ในช่วง ± ที่ประกาศ: '+s.inside.toFixed(0)+'%');
  s.buckets.forEach(b=>{if(b.n)lines.push('ล่วงหน้า '+b.label+': คลาดเฉลี่ย '+b.mae.toFixed(1)+' ชม. (n='+b.n+')');});
  const li=document.getElementById('learningInfo');if(li){li.textContent=lines.join('\n');li.style.whiteSpace='pre-line';}
  setText('learningStatus',s.matched?'📈 มีสถิติ':'⏳ รอบันทึกผลจริง');
  drawHistory();
}
function drawHistory(){
  const h=LS.history(),c=document.getElementById('learningHistory');
  const sel=document.getElementById('actualId');
  if(sel){
    const ids=LS.pendingIds();
    sel.innerHTML=ids.length?ids.map(i=>'<option value="'+esc(i)+'">NORAD '+esc(i)+'</option>').join(''):'<option value="">ยังไม่มีรายการรอผล</option>';
  }
  if(!c)return;
  if(!h.length){c.innerHTML='<p class="text-gray-400 text-center py-8">ยังไม่มีข้อมูล</p>';return;}
  let html='';
  h.forEach(x=>{
    const q=x.quality!=null?x.quality:x.confidence;
    html+='<div class="glass rounded-lg p-3">';
    html+='<div class="flex justify-between mb-1"><span class="font-semibold">NORAD '+esc(x.noradId)+'</span><span class="text-xs '+(x.status==='matched'?'text-green-400':'text-yellow-400')+'">'+(x.status==='matched'?'บันทึกผลจริงแล้ว':'รอผล')+'</span></div>';
    html+='<div class="text-xs text-gray-400"><div>ทำนาย: '+new Date(x.predictedTime).toLocaleString('th-TH')+'</div>';
    if(x.error!=null)html+='<div class="text-green-400">คลาดเคลื่อน: '+(x.error/HOUR).toFixed(1)+' ชม. (จริง − ทำนาย)</div>';
    if(q!=null)html+='<div>คุณภาพ: '+Number(q).toFixed(0)+'%</div>';
    html+='</div></div>';
  });
  c.innerHTML=html;
}
function submitActual(){
  const id=document.getElementById('actualId').value,v=document.getElementById('actualTime').value;
  if(!id){toast('ยังไม่มีรายการรอผลให้บันทึก');return;}
  const t=new Date(v).getTime();
  if(!v||!isFinite(t)){toast('กรอกวันเวลาที่ตกจริง');return;}
  const n=LS.recordActual(id,t);
  document.getElementById('actualTime').value='';
  renderLearning();
  toast(n?'บันทึกแล้ว '+n+' การพยากรณ์ของ NORAD '+id:'ไม่พบรายการรอผลของ NORAD '+id,true);
}
window.recordActualReentry=function(id,dateStr){const n=LS.recordActual(id,new Date(dateStr).getTime());renderLearning();return n;};

function renderSimulationMap(track, impact){
  if(!simMap){
    simMap=L.map('simulationMap').setView([0,0],2);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap',maxZoom:18}).addTo(simMap);
    simLayer=L.layerGroup().addTo(simMap);
  }
  simLayer.clearLayers();
  const segments=[];let current=[];
  for(const p of track){
    const point=[Number(p.latitude_deg),Number(p.longitude_deg)];
    if(!Number.isFinite(point[0])||!Number.isFinite(point[1]))continue;
    if(current.length&&Math.abs(point[1]-current[current.length-1][1])>180){if(current.length>1)segments.push(current);current=[point];}
    else current.push(point);
  }
  if(current.length>1)segments.push(current);
  const lines=[];
  for(const seg of segments)lines.push(L.polyline(seg,{color:'#f97316',weight:3,opacity:.9}).addTo(simLayer));
  if(impact&&track.length){
    const last=track[track.length-1];
    L.circleMarker([last.latitude_deg,last.longitude_deg],{radius:8,color:'#fff',weight:2,fillColor:'#ef4444',fillOpacity:1})
      .bindPopup('Modelled ground intersection').addTo(simLayer);
  }
  if(lines.length){
    const group=L.featureGroup(lines);
    simMap.fitBounds(group.getBounds().pad(0.12));
  }else if(track.length){
    const p=track[track.length-1];simMap.setView([p.latitude_deg,p.longitude_deg],4);
  }
  setTimeout(()=>simMap.invalidateSize(),120);
}


// ---------- CLOUD TLE ACQUISITION ----------
let cloudTle = null;
function setCloudTleInfo(message, color='text-gray-300'){
  const el=document.getElementById('cloudTleInfo');
  if(el){el.textContent=message;el.className='text-xs '+color;}
}
function tleAgeLabel(epoch){
  const hours=(Date.now()-new Date(epoch).getTime())/HOUR;
  if(!Number.isFinite(hours))return 'อ่าน Epoch ไม่ได้';
  const age=hours<0?'Epoch อยู่ในอนาคต':hours<24?hours.toFixed(1)+' ชั่วโมง':(hours/24).toFixed(1)+' วัน';
  return age;
}
async function fetchCloudTLE(){
  const btn=document.getElementById('fetchCloudTleBtn');
  const status=document.getElementById('simRunStatus');
  const base=document.getElementById('apiBaseUrl').value.trim().replace(/\\/+$/,'');
  const id=String(document.getElementById('cloudNoradInput').value||document.getElementById('noradInput').value||curNorad||'').trim();
  try{
    if(!base)throw new Error('กรุณาใส่ Backend API URL จาก Render ก่อน');
    if(!/^\\d{1,6}$/.test(id)||Number(id)<1)throw new Error('กรุณากรอก NORAD ID ตั้งแต่ 1 ถึง 999999');
    localStorage.setItem('reentryApiBase',base);
    btn.disabled=true;btn.classList.add('opacity-50');
    setCloudTleInfo('กำลังติดต่อ Backend… หาก Render เพิ่งพักบริการ อาจใช้เวลาเริ่มระบบ 30–60 วินาที','text-yellow-300');
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),90000);
    let response;
    try{response=await fetch(base+'/api/v1/tle/'+encodeURIComponent(id),{method:'GET',headers:{'Accept':'application/json'},signal:controller.signal});}
    finally{clearTimeout(timer);}
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.detail||('Backend ตอบ HTTP '+response.status));
    if(Number(data.norad_id)!==Number(id)||!validTLE(data.line1,data.line2,id))throw new Error('TLE ที่ได้รับไม่ผ่านการตรวจสอบ NORAD ID หรือ checksum');
    const epochMs=new Date(data.epoch_utc).getTime();
    if(!Number.isFinite(epochMs))throw new Error('Backend ส่ง Epoch ที่อ่านไม่ได้');
    cloudTle={norad_id:Number(id),name:data.name||('NORAD '+id),line1:data.line1,line2:data.line2,epoch:epochMs,epoch_utc:data.epoch_utc,source:data.source||'ไม่ระบุ',source_url:data.source_url||'',fetched_at_utc:data.fetched_at_utc||new Date().toISOString()};
    document.getElementById('cloudNoradInput').value=id;
    const ageHours=(Date.now()-epochMs)/HOUR;
    const freshness=ageHours>72?'ข้อมูลเก่ากว่า 72 ชั่วโมง — ควรตรวจสอบก่อนจำลอง':ageHours>24?'ข้อมูลเก่ากว่า 24 ชั่วโมง':'ข้อมูลค่อนข้างใหม่';
    const color=ageHours>72?'text-red-300':ageHours>24?'text-yellow-300':'text-green-300';
    setCloudTleInfo('✓ '+cloudTle.name+' (#'+id+') · Epoch '+new Date(epochMs).toLocaleString('th-TH',{timeZone:'UTC'})+' UTC · อายุ '+tleAgeLabel(data.epoch_utc)+' · '+cloudTle.source+' · ดึงเมื่อ '+new Date(cloudTle.fetched_at_utc).toLocaleString('th-TH')+' · '+freshness,color);
    status.textContent='รับ TLE แล้ว — พร้อมส่งเข้าแบบจำลอง';
    status.className='text-xs text-green-300';
  }catch(err){
    cloudTle=null;
    const msg=err.name==='AbortError'?'หมดเวลารอ Backend (90 วินาที) — ตรวจสอบ Render ว่าบริการทำงานอยู่':err.message;
    setCloudTleInfo('ดึง TLE ไม่สำเร็จ: '+msg,'text-red-300');
    status.textContent='ดึง TLE ไม่สำเร็จ: '+msg;
    status.className='text-xs text-red-300';
  }finally{
    btn.disabled=false;btn.classList.remove('opacity-50');
  }
}
window.fetchCloudTLE=fetchCloudTLE;

async function runCloudSimulation(){
  const btn=document.getElementById('runSimulationBtn');
  const status=document.getElementById('simRunStatus');
  btn.disabled=true;btn.classList.add('opacity-50');
  try{
    const base=document.getElementById('apiBaseUrl').value.trim().replace(/\/+$/,'');
    if(!base)throw new Error('กรุณาใส่ Backend API URL จาก Render ก่อน');
    localStorage.setItem('reentryApiBase',base);
    if(!cloudTle)throw new Error('กรุณากรอก NORAD ID แล้วกด “ดึง TLE” ก่อนเริ่มจำลอง');
    const number=id=>Number(document.getElementById(id).value);
    const payload={
      tle_line1:cloudTle.line1,
      tle_line2:cloudTle.line2,
      start_time_utc:new Date().toISOString(),
      max_search_hours:168,
      max_simulation_seconds:1800,
      sample_interval_s:2,
      mass_kg:number('simMass'),
      reference_area_m2:number('simArea'),
      drag_coefficient:number('simCd'),
      nose_radius_m:number('simNose'),
      f107_sfu:number('simF107'),
      f107a_sfu:number('simF107a'),
      ap:number('simAp')
    };
    for(const k of ['mass_kg','reference_area_m2','drag_coefficient','nose_radius_m','f107_sfu','f107a_sfu','ap']){
      if(!Number.isFinite(payload[k]))throw new Error('กรุณาตรวจสอบค่าพารามิเตอร์การจำลอง');
    }
    status.textContent='กำลังส่ง TLE ของ '+cloudTle.name+' (#'+cloudTle.norad_id+') ไปยัง Cloud และคำนวณ…';
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),180000);
    let response;
    try{response=await fetch(base+'/api/v1/simulate',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal
    });}finally{clearTimeout(timer);}
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.detail||('HTTP '+response.status));
    const track=data.track||[];
    const last=track.length?track[track.length-1]:null;
    document.getElementById('simStatus').textContent=data.status||'-';
    document.getElementById('simElapsed').textContent=(Number(data.elapsed_simulation_s||0)/60).toFixed(1)+' นาที';
    document.getElementById('simPeakQ').textContent=(Number(data.peak_dynamic_pressure_Pa||0)/1000).toFixed(2)+' kPa';
    document.getElementById('simPeakHeat').textContent=(Number(data.peak_convective_heat_flux_W_m2||0)/1e6).toFixed(3)+' MW/m²';
    document.getElementById('simPeakDecel').textContent=(Number(data.peak_drag_deceleration_m_s2||0)/9.80665).toFixed(2)+' g';
    document.getElementById('simImpact').textContent=data.impact&&last
      ?Number(last.latitude_deg).toFixed(3)+'°, '+Number(last.longitude_deg).toFixed(3)+'°'
      :'ยังไม่ถึงพื้นในช่วงจำลอง';
    document.getElementById('simModelNote').textContent=
      'เริ่มที่ '+Number(data.entry_interface.altitude_km).toFixed(1)+' km · '+data.model.atmosphere+
      ' · '+track.length+' จุดแสดงผล · '+data.model.integration;
    renderSimulationMap(track,!!data.impact);
    status.textContent='จำลองเสร็จแล้ว — '+cloudTle.name+' (#'+cloudTle.norad_id+') · ตรวจสอบข้อจำกัดและสมมติฐานก่อนตีความผล';
    status.className='text-xs text-green-300';
  }catch(err){
    status.textContent='ไม่สำเร็จ: '+(err.name==='AbortError'?'หมดเวลารอผลจำลอง (180 วินาที)':err.message);
    status.className='text-xs text-red-300';
  }finally{
    btn.disabled=false;btn.classList.remove('opacity-50');
  }
}
window.runCloudSimulation=runCloudSimulation;

function showTab(name,btn){
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el=>el.classList.remove('tab-active'));
  document.getElementById('tab-'+name).classList.remove('hidden');
  btn.classList.add('tab-active');
  if(name==='map')setTimeout(()=>map.invalidateSize(),100);
  if(name==='simulation'&&simMap)setTimeout(()=>simMap.invalidateSize(),150);
  if(name==='decay'){if(decayChart)decayChart.resize();if(probChart)probChart.resize();}
}
function clearLearningData(){if(confirm('ล้างสถิติและบันทึกผลทั้งหมด?')){LS.clear();renderLearning();}}
function toast(msg,ok){const n=document.createElement('div');n.className='fixed top-24 left-1/2 transform -translate-x-1/2 '+(ok?'bg-green-600/90':'bg-red-500/90')+' px-4 py-2 rounded-lg shadow-xl z-50 text-sm max-w-xs text-center';n.textContent=(ok?'✅ ':'⚠️ ')+msg;document.body.appendChild(n);setTimeout(()=>n.remove(),5000);}
window.selectObject=selectObject;
window.removeFromWatch=removeFromWatch;

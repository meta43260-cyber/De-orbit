// ================= API v3.2 =================
const PROXIES=[u=>u,u=>'https://api.codetabs.com/v1/proxy/?quest='+encodeURIComponent(u),u=>'https://api.allorigins.win/raw?url='+encodeURIComponent(u)];
let proxyPref=0;
async function fetchText(url,ms=9000){const order=[0,1,2].map(i=>(proxyPref+i)%3);let err;for(const idx of order){try{const c=new AbortController();const t=setTimeout(()=>c.abort(),ms);const r=await fetch(PROXIES[idx](url),{signal:c.signal});clearTimeout(t);if(r.ok){proxyPref=idx;return await r.text();}err=new Error('HTTP '+r.status);}catch(e){err=e;}}throw err||new Error('fetch fail');}
function parseTLEEpoch(l1){const s=l1.substring(18,32);const yy=parseInt(s.substring(0,2),10);const y=yy>56?1900+yy:2000+yy;const d=parseFloat(s.substring(2));return Date.UTC(y,0,1)+(d-1)*86400000;}
function parseTLEBulk(text){const L=text.split('\n').map(l=>l.trim()).filter(Boolean);const out=[];for(let i=0;i<L.length-1;i++){if(L[i].startsWith('1 ')&&L[i+1].startsWith('2 ')){const nm=(i>0&&!L[i-1].startsWith('1 ')&&!L[i-1].startsWith('2 '))?L[i-1]:'UNKNOWN';out.push({name:nm,line1:L[i],line2:L[i+1],epoch:parseTLEEpoch(L[i])});}}out.sort((a,b)=>a.epoch-b.epoch);const seen=new Set();return out.filter(o=>{const k=Math.round(o.epoch/3600000);if(seen.has(k))return false;seen.add(k);return true;});}

// ---- ที่เก็บข้อมูลในเครื่อง (สะสมประวัติจริง + แคชออฟไลน์) ----
const HKEY=id=>'reentry_hist_'+id, CKEY=id=>'reentry_cache_'+id;
function loadLocalHist(id){try{return JSON.parse(localStorage.getItem(HKEY(id)))||[];}catch(e){return[];}}
function saveLocalHist(id,arr){try{localStorage.setItem(HKEY(id),JSON.stringify(arr.slice(-120)));}catch(e){}}
function collectTLE(id,tle){const arr=loadLocalHist(id);if(!arr.some(x=>Math.abs(x.epoch-tle.epoch)<3600000)){arr.push(tle);arr.sort((a,b)=>a.epoch-b.epoch);saveLocalHist(id,arr);}return arr;}
function loadCache(id){try{return JSON.parse(localStorage.getItem(CKEY(id)));}catch(e){return null;}}
function saveCache(id,tle){try{localStorage.setItem(CKEY(id),JSON.stringify({t:Date.now(),tle:tle}));}catch(e){}}

async function fetchTLE(id){
  let tle=null;
  try{const t=await fetchText('https://celestrak.org/NORAD/elements/gp.php?CATNR='+id+'&FORMAT=tle');const a=parseTLEBulk(t);if(a.length)tle=a[a.length-1];}catch(e){}
  if(!tle){
    const r=await fetch('https://tle.ivanstanojevic.me/api/tle/'+id);
    if(!r.ok)throw new Error('ไม่พบวัตถุรหัส '+id);
    const j=await r.json();let l1,l2;
    if(Array.isArray(j.tle)){l1=j.tle[0];l2=j.tle[1];}
    else if(j.tle){l1=j.tle.line1;l2=j.tle.line2;}
    else{l1=j.line1;l2=j.line2;}
    if(!l1||!l2)throw new Error('รูปแบบข้อมูลผิด');
    tle={name:j.name||('NORAD '+id),line1:l1,line2:l2,epoch:parseTLEEpoch(l1)};
  }
  saveCache(id,tle);
  return tle;
}

async function fetchTLEHistory(id,days=30){
  // 1) ประวัติจริงจาก CelesTrak archive
  try{
    const t=await fetchText('https://celestrak.org/NORAD/archives/gp.php?CATNR='+id+'&FORMAT=tle',9000);
    const h=parseTLEBulk(t);const cut=Date.now()-days*86400000;
    const r=h.filter(x=>x.epoch>=cut);
    const use=r.length>=7?r:(h.length>=7?h.slice(-60):null);
    if(use){use.forEach(u=>collectTLE(id,u));return{history:use,simulated:false,source:'celestrak'};}
  }catch(e){}
  // 2) TLE ปัจจุบัน (ถ้าเน็ตล่มใช้แคช)
  let cur=null;
  try{cur=await fetchTLE(id);}
  catch(e){
    const c=loadCache(id);
    if(c&&Date.now()-c.t<3*86400000){
      const loc=loadLocalHist(id);
      if(loc.length>=7)return{history:loc,simulated:false,source:'local-offline'};
      return{history:simulateHistory(c.tle,days),simulated:true,source:'cache-offline',localCount:loc.length};
    }
    throw e;
  }
  // 3) ประวัติจริงที่สะสมไว้ในเครื่อง
  const loc=collectTLE(id,cur);
  const cut2=Date.now()-days*86400000;
  const recentLoc=loc.filter(x=>x.epoch>=cut2);
  if(recentLoc.length>=7)return{history:recentLoc,simulated:false,source:'local'};
  // 4) จำลอง (แต่บอกจำนวนที่เก็บจริงแล้ว)
  return{history:simulateHistory(cur,days),simulated:true,source:'simulated',localCount:recentLoc.length};
}

function simulateHistory(cur,days){const out=[];const mm=parseFloat(cur.line2.substring(52,63));for(let d=days;d>=0;d--){out.push(buildTLECopy(cur,Date.now()-d*86400000,mm-d*0.00045+(Math.random()-0.5)*0.0008));}return out;}
function buildTLECopy(t,epochMs,mm){const dt=new Date(epochMs);const doy=(Date.UTC(dt.getUTCFullYear(),dt.getUTCMonth(),dt.getUTCDate())-Date.UTC(dt.getUTCFullYear(),0,1))/86400000+1+dt.getUTCHours()/24;const yy=String(dt.getUTCFullYear()%100).padStart(2,'0');const l1=t.line1.substring(0,18)+yy+doy.toFixed(8).padStart(12,'0')+t.line1.substring(32);const l2=t.line2.substring(0,52)+mm.toFixed(8).padStart(11,'0')+t.line2.substring(63);return{name:t.name,line1:l1,line2:l2,epoch:epochMs};}
async function fetchSpaceWeather(){try{const d=JSON.parse(await fetchText('https://services.swpc.noaa.gov/json/planetary_k_index_1m.json',6000));const l=d[d.length-1];return{kp:parseFloat(l[1])||2};}catch(e){return{kp:2};}}

// ================= ALGORITHMS =================
const REENTRY_MM=16.7;
const exMM=l2=>parseFloat(l2.substring(52,63));
const exInc=l2=>parseFloat(l2.substring(8,16));
function exBStar(l1){const s=l1.substring(53,61);return parseFloat(s.substring(0,6))*Math.pow(10,parseInt(s.substring(6,8),10));}
const estAlt=mm=>Math.max(0,Math.pow(86666/mm,2/3)-6378);
function segmentedMA(hist,size=5){const mms=hist.map(h=>exMM(h.line2));const segs=[];for(let i=0;i+size<=mms.length;i++){const s=mms.slice(i,i+size);const avg=s.reduce((a,b)=>a+b,0)/size;const v=s.reduce((a,b)=>a+(b-avg)**2,0)/size;segs.push({meanMotion:avg,stdDev:Math.sqrt(v),epoch:hist[i+Math.floor(size/2)].epoch});}let rate=0;if(segs.length>=2){const f=segs[0],l=segs[segs.length-1];rate=(l.meanMotion-f.meanMotion)/(l.epoch-f.epoch)*86400000;}return{segments:segs,decayRate:rate};}
function kalmanFilter(hist){if(!hist.length)return[];let x=exMM(hist[0].line2),P=1;const Q=0.0005,R=0.002;return hist.map(h=>{const z=exMM(h.line2);const Pp=P+Q;const K=Pp/(Pp+R);x=x+K*(z-x);P=(1-K)*Pp;return{value:x,uncertainty:Math.sqrt(P)};});}
function linearModel(segs){if(segs.length<2)return null;const l=segs[segs.length-1],p=segs[segs.length-2];const dt=l.epoch-p.epoch;if(dt<=0)return null;const sl=(l.meanMotion-p.meanMotion)/dt;if(sl<=1e-12)return null;const t=(REENTRY_MM-l.meanMotion)/sl;if(t<=0||t>1095*86400000)return null;return{time:l.epoch+t,confidence:Math.max(30,Math.min(70,70-l.stdDev*2000)),uncertainty:Math.max(6*3600000,t*0.25)};}
function expModel(segs){if(segs.length<3)return null;const y3=segs[segs.length-3].meanMotion,y2=segs[segs.length-2].meanMotion,y1=segs[segs.length-1].meanMotion;const t1=segs[segs.length-1].epoch;const s1=y2-y3,s2=y1-y2;if(s2<=0)return null;const dt=segs[segs.length-1].epoch-segs[segs.length-2].epoch;const need=REENTRY_MM-y1;let t;if(s1>0){const k=Math.log(s2/s1)/dt;t=k>1e-12?Math.log(1+need*k/s2)/k:need/s2;}else t=need/s2;if(!isFinite(t)||t<=0||t>1095*86400000)return null;return{time:t1+t,confidence:Math.max(30,Math.min(75,75-Math.abs(s2-s1)*50000)),uncertainty:Math.max(4*3600000,t*0.2)};}
function smaModel(hist){const s=segmentedMA(hist,5);if(s.segments.length<2||s.decayRate<=0)return null;const l=s.segments[s.segments.length-1];const need=REENTRY_MM-l.meanMotion;if(need<=0)return{time:l.epoch,confidence:90,uncertainty:12*3600000};const t=need/(s.decayRate/86400000);if(t>1095*86400000)return null;return{time:l.epoch+t,confidence:Math.max(40,Math.min(85,85-l.stdDev*1500)),uncertainty:Math.max(3*3600000,t*0.15)};}
function ensemble(hist){const ms=[['linear',linearModel(segmentedMA(hist,3).segments)],['exponential',expModel(segmentedMA(hist,5).segments)],['segmentedMA',smaModel(hist)]].filter(m=>m[1]);if(!ms.length)return null;if(ms.length===1)return Object.assign({method:ms[0][0]},ms[0][1]);let tw=0,tt=0;ms.forEach(m=>{tw+=m[1].confidence;tt+=m[1].time*m[1].confidence;});const times=ms.map(m=>m[1].time);const spread=Math.max(...times)-Math.min(...times);return{time:tt/tw,confidence:Math.max(...ms.map(m=>m[1].confidence))*0.95,uncertainty:Math.max(...ms.map(m=>m[1].uncertainty),spread*0.3),method:'ensemble',models:ms.map(m=>({name:m[0]}))};}
function predictReentry(hist,weather,learning){const p=ensemble(hist);if(!p)return null;const last=hist[hist.length-1];if(weather&&weather.kp>3){p.time-=(p.time-last.epoch)*(weather.kp-3)*0.02;p.confidence*=0.95;}if(learning&&learning.correctionBias){p.time+=learning.correctionBias;p.confidence=Math.min(95,p.confidence+learning.confidenceBoost);}p.inclination=exInc(last.line2);p.bstar=exBStar(last.line1);p.currentAltitude=estAlt(exMM(last.line2));p.tleAge=(Date.now()-last.epoch)/3600000;return p;}

// ================= LEARNING =================
class Learning{
  constructor(){try{this.d=JSON.parse(localStorage.getItem('reentry_learn'))||this.blank();}catch(e){this.d=this.blank();}}
  blank(){return{predictions:[],correctionBias:0,confidenceBoost:0,total:0,success:0};}
  save(){localStorage.setItem('reentry_learn',JSON.stringify(this.d));}
  record(noradId,p){this.d.predictions.push({noradId:noradId,predictedTime:p.time,confidence:p.confidence,uncertainty:p.uncertainty,timestamp:Date.now(),status:'pending',actualTime:null,error:null});if(this.d.predictions.length>100)this.d.predictions.shift();this.d.total++;this.save();}
  recordActual(noradId,actualTime){let best=null,min=Infinity;this.d.predictions.forEach(p=>{if(p.noradId==noradId&&p.status==='pending'){const d=Math.abs(p.predictedTime-actualTime);if(d<min){min=d;best=p;}}});if(best){best.status='matched';best.actualTime=actualTime;best.error=min;if(min<86400000)this.d.success++;}this.recalc();this.save();}
  recalc(){const m=this.d.predictions.filter(p=>p.status==='matched');if(m.length<3){this.d.correctionBias=0;this.d.confidenceBoost=0;return;}const errs=m.map(p=>p.actualTime-p.predictedTime);const avg=errs.reduce((a,b)=>a+b,0)/errs.length;const w=Math.min(1,m.length/30);this.d.correctionBias=avg*w;this.d.confidenceBoost=(this.d.success/Math.max(1,m.length))*15*w;}
  info(){const m=this.d.predictions.filter(p=>p.status==='matched').length;return{correctionBias:this.d.correctionBias,confidenceBoost:this.d.confidenceBoost,matched:m,pending:this.d.predictions.filter(p=>p.status==='pending').length,accuracy:this.d.total?this.d.success/this.d.total*100:0,progress:Math.min(100,this.d.predictions.length/30*100)};}
  history(){return this.d.predictions.slice(-20).reverse();}
  clear(){this.d=this.blank();this.save();}
}
const LS=new Learning();

// ================= UI =================
let curNorad=localStorage.getItem('lastNorad')||'25544';
let map=null,orbitGroup=null,decayChart=null,probChart=null;

document.addEventListener('DOMContentLoaded',async()=>{
  document.getElementById('noradInput').value=curNorad;
  map=L.map('map').setView([10,100],3);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Tiles © Esri',maxZoom:18}).addTo(map);
  orbitGroup=L.layerGroup().addTo(map);
  setTimeout(()=>map.invalidateSize(),300);
  refreshData();
  setInterval(refreshData,15*60000);
  if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js').catch(()=>{});
});

function predictFromInput(){const v=document.getElementById('noradInput').value.trim();if(!v||v<1||v>99999){toast('กรอกเลข NORAD 1-99999');return;}curNorad=v;localStorage.setItem('lastNorad',v);refreshData();}

async function refreshData(){
  const st=document.getElementById('status');st.textContent='กำลังอัปเดต...';st.className='text-yellow-400';
  try{
    const res=await fetchTLEHistory(curNorad,30);
    if(res.history.length<5)throw new Error('ข้อมูล TLE ไม่เพียงพอ');
    const weather=await fetchSpaceWeather();
    const pred=predictReentry(res.history,weather,LS.info());
    if(!pred||!pred.time){noPred();toast('ดาวเทียมดวงนี้ยังไม่แสดงอาการลดระดับชัดเจน');}
    else{LS.record(curNorad,pred);render(pred,res,weather);}
    st.textContent='อัปเดต '+new Date().toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'});st.className='text-green-400';
  }catch(e){console.error(e);st.textContent='เชื่อมต่อไม่ได้';st.className='text-red-400';toast('ดึงข้อมูลไม่ได้: '+((e&&e.message)||'ตรวจสอบอินเทอร์เน็ต'));}
}

function noPred(){['utcTime','utcUncertainty','ictTime','ictUncertainty','impactZone'].forEach(id=>{document.getElementById(id).textContent='-';});const b=document.getElementById('confidenceBadge');b.textContent='N/A';b.className='text-xs px-2 py-1 rounded-full bg-gray-600';}

function render(p,res,weather){
  const hist=res.history;
  const d=new Date(p.time),u=(p.uncertainty/3600000).toFixed(1);
  document.getElementById('utcTime').textContent=d.toLocaleDateString('th-TH',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC'});
  document.getElementById('utcUncertainty').textContent='± '+u+' ชม.';
  document.getElementById('ictTime').textContent=d.toLocaleDateString('th-TH',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Bangkok'});
  document.getElementById('ictUncertainty').textContent='± '+u+' ชม.';
  const b=document.getElementById('confidenceBadge');b.textContent=Math.round(p.confidence)+'%';
  b.className='text-xs px-2 py-1 rounded-full '+(p.confidence>=70?'bg-green-500':p.confidence>=50?'bg-yellow-500':'bg-red-500');
  const inc=p.inclination||51.6;
  document.getElementById('impactZone').textContent=inc<20?'เขตร้อน (ไทย/อินโดนีเซีย)':inc<40?'กึ่งร้อน (เอเชียใต้)':inc<60?'กลาง (ยุโรป/จีน/ญี่ปุ่น)':'ละติจูดสูง';
  document.getElementById('noradId').textContent=curNorad;
  document.getElementById('inclination').textContent=inc.toFixed(2)+'°';
  document.getElementById('currentAlt').textContent=p.currentAltitude.toFixed(1)+' กม.';
  document.getElementById('bstar').textContent=p.bstar?p.bstar.toExponential(2):'-';
  document.getElementById('kpIndex').textContent=weather.kp.toFixed(1);
  document.getElementById('tleAge').textContent=p.tleAge.toFixed(1)+' ชม.';
  document.getElementById('ensembleModels').textContent=p.models?p.models.map(m=>m.name).join(', '):(p.method||'-');
  const srcMap={
    'celestrak':'✅ CelesTrak (ประวัติจริง)',
    'local':'📱 ประวัติจริงในเครื่อง '+hist.length+' ชุด',
    'local-offline':'📴 ออฟไลน์: ประวัติจริงในเครื่อง',
    'cache-offline':'📴 ออฟไลน์: แคชล่าสุด + จำลอง',
    'simulated':'⚠️ ประวัติจำลอง (เก็บจริงแล้ว '+(res.localCount||0)+' ชุด)'
  };
  document.getElementById('dataSource').textContent=srcMap[res.source]||'-';
  const li=LS.info();
  document.getElementById('learningProgress').style.width=li.progress+'%';
  document.getElementById('learningPercent').textContent=Math.round(li.progress)+'%';
  document.getElementById('learningInfo').textContent='จับคู่แล้ว '+li.matched+' ครั้ง / เป้าหมาย 30 | แม่นยำ '+li.accuracy.toFixed(0)+'%';
  document.getElementById('learningStatus').textContent=li.correctionBias?'✅ ปรับแก้แล้ว':'⏳ รวบรวมข้อมูล';
  drawDecay(hist);drawProb(p);drawMap(hist,p);drawHistory();
}

function drawMap(hist,p){
  orbitGroup.clearLayers();
  const t=hist[hist.length-1];
  const rec=satellite.twoline2satrec(t.line1,t.line2);
  const now=Date.now(),pts=[];let cur=null,re=null;
  for(let i=0;i<1440;i+=5){const tm=new Date(now+i*60000);const pv=satellite.propagate(rec,tm);if(!pv.position)continue;const gd=satellite.eciToGeodetic(pv.position,satellite.gstime(tm));const la=satellite.degreesLat(gd.latitude),lo=satellite.degreesLong(gd.longitude);if(gd.height<80){re={la:la,lo:lo};break;}pts.push([la,lo]);if(i===0)cur={la:la,lo:lo};}
  if(pts.length>1)L.polyline(pts,{color:'#fbbf24',weight:2,opacity:.8}).addTo(orbitGroup);
  if(cur){L.circleMarker([cur.la,cur.lo],{radius:8,fillColor:'#3b82f6',color:'#fff',weight:2,fillOpacity:.9}).addTo(orbitGroup);map.setView([cur.la,cur.lo],3);}
  if(re){L.circle([re.la,re.lo],{radius:Math.min((p.uncertainty/3600000)*2000000,20000000),color:'#ef4444',fillColor:'#ef4444',fillOpacity:.2,weight:2}).addTo(orbitGroup);L.marker([re.la,re.lo],{icon:L.divIcon({html:'💥',className:'text-2xl',iconSize:[30,30]})}).addTo(orbitGroup);}
}

function drawDecay(hist){
  const ctx=document.getElementById('decayChart').getContext('2d');
  if(decayChart)decayChart.destroy();
  decayChart=new Chart(ctx,{type:'line',data:{labels:hist.map(h=>new Date(h.epoch).toLocaleDateString('th-TH',{day:'numeric',month:'short'})),datasets:[{label:'ความสูง (กม.)',data:hist.map(h=>estAlt(exMM(h.line2))),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.1)',tension:.4,fill:true,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#fff'}}},scales:{y:{ticks:{color:'#9ca3af'},grid:{color:'rgba(255,255,255,.1)'}},x:{ticks:{color:'#9ca3af',maxRotation:45},grid:{color:'rgba(255,255,255,.1)'}}}}});
}

function drawProb(p){
  const ctx=document.getElementById('probabilityChart').getContext('2d');
  if(probChart)probChart.destroy();
  const lb=[],dt=[];
  for(let i=-12;i<=12;i++){lb.push(new Date(p.time+i*3600000).getUTCHours()+':00');dt.push(Math.exp(-0.5*Math.pow((i*3600000)/p.uncertainty,2))*100);}
  const g=ctx.createLinearGradient(0,0,0,180);g.addColorStop(0,'rgba(59,130,246,.8)');g.addColorStop(1,'rgba(59,130,246,.1)');
  probChart=new Chart(ctx,{type:'line',data:{labels:lb,datasets:[{label:'ความน่าจะเป็น',data:dt,borderColor:'#3b82f6',backgroundColor:g,tension:.4,fill:true,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#fff'}}},scales:{y:{beginAtZero:true,ticks:{color:'#9ca3af',callback:v=>v+'%'},grid:{color:'rgba(255,255,255,.1)'}},x:{ticks:{color:'#9ca3af',maxRotation:45},grid:{color:'rgba(255,255,255,.1)'}}}}});
}

function drawHistory(){
  const h=LS.history(),c=document.getElementById('learningHistory');
  if(!h.length){c.innerHTML='<p class="text-gray-400 text-center py-8">ยังไม่มีข้อมูล</p>';return;}
  let html='';
  h.forEach(x=>{
    html+='<div class="glass rounded-lg p-3">';
    html+='<div class="flex justify-between mb-1"><span class="font-semibold">NORAD '+x.noradId+'</span><span class="text-xs '+(x.status==='matched'?'text-green-400':'text-yellow-400')+'">'+(x.status==='matched'?'บันทึกแล้ว':'รอผล')+'</span></div>';
    html+='<div class="text-xs text-gray-400"><div>ทำนาย: '+new Date(x.predictedTime).toLocaleString('th-TH')+'</div>';
    if(x.error!=null)html+='<div class="text-green-400">คลาดเคลื่อน: '+(x.error/3600000).toFixed(1)+' ชม.</div>';
    html+='<div>มั่นใจ: '+x.confidence.toFixed(0)+'%</div></div></div>';
  });
  c.innerHTML=html;
}

function showTab(name,btn){
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el=>el.classList.remove('tab-active'));
  document.getElementById('tab-'+name).classList.remove('hidden');
  btn.classList.add('tab-active');
  if(name==='map')setTimeout(()=>map.invalidateSize(),100);
  if(name==='decay'){if(decayChart)decayChart.resize();if(probChart)probChart.resize();}
}

function clearLearningData(){if(confirm('ล้างข้อมูลการเรียนรู้ทั้งหมด?')){LS.clear();refreshData();}}
function toast(msg){const n=document.createElement('div');n.className='fixed top-24 left-1/2 transform -translate-x-1/2 bg-red-500/90 px-4 py-2 rounded-lg shadow-xl z-50 text-sm max-w-xs text-center';n.textContent='⚠️ '+msg;document.body.appendChild(n);setTimeout(()=>n.remove(),5000);}
window.recordActualReentry=function(id,dateStr){LS.recordActual(id,new Date(dateStr).getTime());refreshData();alert('บันทึกแล้ว ระบบจะแม่นยำขึ้น');};

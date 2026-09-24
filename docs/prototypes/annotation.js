// Standalone design prototype. Only browser-local drafts are written.
const messages = {
 "ja": {
  "copy": "コピー",
  "copied": "コピーしました",
  "copyFallback": "選択したテキストをコピーしてください。",
  "recordHint": "試用中に入力した生き物の名前、選択範囲、回答をJSONで確認できます。",
  "unknownHint": "名前がわからない声の範囲をなぞってください。",
  "prototype": "操作見本",
  "language": "言語",
  "title": "録音を聴いて確かめる",
  "intro": "音を聴いて、生き物の名前を選び、鳴き声が聞こえる範囲に印を付けます。AIが見つけなかった生き物も追加できます。",
  "threeSteps": "一つの音で行うこと",
  "step1": "15秒の音を再生する",
  "step2": "生き物の名前を選び、図で鳴き声の範囲をなぞる",
  "step3": "ほかの生き物の声にも印を付け、次の音へ進む",
  "paletteHint": "種のリストは、この地点のAI検出種から始めます。音を切り替えても同じリストを使い、足りない種は追加できます。",
  "scopeNote": "操作見本です。テスト録音から切り出した30区間を使います。入力内容はこのブラウザ内に保存し、本番の検証データや評価には反映しません。",
  "download": "入力データを見る",
  "back": "← 聞こえた生き物の一覧へ",
  "listTitle": "生き物の名前",
  "listHint": "名前を選び、図で鳴き声の範囲をなぞってください。",
  "filter": "リスト内を絞り込む",
  "unknown": "種がわからない",
  "addSpecies": "＋ リストに種を追加",
  "keyboard": "秒数を入力して範囲を追加",
  "startTime": "開始（秒）",
  "endTime": "終了（秒）",
  "addRange": "この範囲を追加",
  "present": "聞こえる",
  "absent": "聞こえない",
  "uncertain": "判断できない",
  "previous": "← 前の音",
  "close": "閉じる",
  "catalogHint": "AIが検出していない種も、和名・英名・学名で探せます。",
  "speciesName": "種名",
  "loading": "音の準備をしています…",
  "loadError": "操作見本の音を読み込めませんでした。poc/multispecies_sampling.py で試用データを作ってから、再読み込みしてください。",
  "storageError": "このブラウザに保存できません。閉じる前に「入力データを見る」から内容をコピーしてください。",
  "start": "始める",
  "resume": "続きから",
  "progress": "確認を終えた音 {done} / {total}",
  "position": "{n} / {total} 音",
  "annotate": "聞こえた生き物を選ぶ",
  "checkTitle": "「{name}」の有無を確かめる",
  "selectHint": "リストで生き物を選び、図で鳴き声の範囲をなぞってください。",
  "dragHint": "「{name}」の声が聞こえる範囲をなぞってください。",
  "empty": "範囲を選ぶと、生き物の名前と開始・終了の秒数がここに表示されます。",
  "saved": "変更を保存しました",
  "noneSelected": "先にリストから生き物を選んでください。",
  "invalidRange": "開始と終了を0〜15秒で指定してください。終了は開始より後にします。",
  "remove": "削除",
  "playRange": "ここを聴く",
  "noMatch": "該当する種がありません。",
  "added": "追加済み",
  "checkQuestion": "この音に「{name}」の声はありますか？",
  "checkHelp": "この音を聴いて答えてください。わからない場合は「判断できない」で残せます。",
  "answered": "保存した回答：{answer}",
  "next": "保存して次の音へ →",
  "finish": "保存して一覧へ",
  "footer": "入力内容は自動保存されます。いつでも戻れます。",
  "checkFooter": "回答はいつでも前の音に戻って訂正できます。",
  "spectrogram": "この15秒の音を表した図。横軸は時間です。",
  "catalogCount": "{count} 種を表示",
  "catalogSearch": "種名を入力してください。",
  "filterPlaceholder": "名前で絞り込み",
  "catalogPlaceholder": "例：ウグイス",
  "reviewTitle": "聞こえた生き物",
  "backHome": "← 最初の画面に戻る",
  "reviewIntro": "これまでに選んだ生き物と、その音声を確認できます。",
  "qualityTitle": "AIの正確さを調べる",
  "qualityWhy": "AIの見逃しや取り違えを調べるには、その種の声が聞こえない音も必要です。調べたい種を選び、まだ回答していない音を聴いて確かめます。",
  "qualitySpecies": "正確さを調べたい種",
  "chooseSpecies": "種を選択",
  "qualityCounts": "聞こえる {present}音 · 聞こえない {absent}音 · 判断できない {unknown}音 · 未確認 {missing}音",
  "beginCheck": "この種の有無を確かめる",
  "reviewAnswers": "保存した回答を見直す",
  "qualityPrototype": "この操作見本では回答の保存まで試せます。AIの正確さはまだ計算しません。",
  "continueRecording": "録音の確認を続ける",
  "heardCount": "声が聞こえた区間：{count}",
  "revisit": "音を見直す",
  "noNamedCalls": "声が聞こえる範囲を選ぶと、ここに生き物の名前が表示されます。",
  "unknownCount": "種がわからない声：{count}か所",
  "stageNote": "この種をAIが正しく見つけられるか調べるため、声が聞こえるか答えます。すでに範囲を選んだ音への回答は不要です。",
  "alreadyMarked": "この音では、声が聞こえる範囲を選択済みです。もう一度答える必要はありません。",
  "zoomIn": "拡大",
  "zoomOut": "縮小",
  "zoomFit": "全体に戻す",
  "zoomShort": "スクロールで拡大・縮小",
  "viewLabel": "{start}–{end} 秒 / 15秒",
  "panHint": "スクロールバーで表示位置を動かせます。",
  "playView": "表示中の範囲を聴く",
  "audioError": "音を再生できませんでした。もう一度再生してください。",
  "zoomHint": "スクロールで拡大・縮小。下の時間軸なら横だけ、左の周波数軸なら縦だけ変えられます。",
  "plotLabel": "スペクトログラム。スクロールで縦横を同じ倍率で拡大・縮小します。",
  "timeAxis": "時間（秒）",
  "frequencyAxis": "周波数（kHz）",
  "axisHelp": "スクロール、または＋・−キーでこの軸を拡大・縮小できます。"
 },
 "en": {
  "copy": "Copy",
  "copied": "Copied",
  "copyFallback": "Copy the selected text.",
  "recordHint": "Trial data in JSON: species names, selected time ranges, and answers.",
  "unknownHint": "Drag across the call you cannot identify.",
  "prototype": "Interaction prototype",
  "language": "Language",
  "title": "Listen and record what you hear",
  "intro": "Listen, choose an animal, and mark when its call is audible. You can also add animals missed by AI.",
  "threeSteps": "For each clip",
  "step1": "Play the 15-second clip",
  "step2": "Choose an animal, then drag across its call on the diagram",
  "step3": "Mark any other animal calls, then move to the next clip",
  "paletteHint": "The initial species list comes from AI detections across this site. It stays the same between clips. Add any missing species.",
  "scopeNote": "This prototype uses 30 clips from test recordings. Your entries stay in this browser and are not added to the app’s validation data or performance estimates.",
  "download": "View entered data",
  "back": "← Back to animals heard",
  "listTitle": "Animal names",
  "listHint": "Choose an animal, then drag across its call on the diagram.",
  "filter": "Filter the list",
  "unknown": "Species unknown",
  "addSpecies": "+ Add a species to the list",
  "keyboard": "Add a range using seconds",
  "startTime": "Start (seconds)",
  "endTime": "End (seconds)",
  "addRange": "Add this range",
  "present": "Heard",
  "absent": "Not heard",
  "uncertain": "Cannot tell",
  "previous": "← Previous clip",
  "close": "Close",
  "catalogHint": "Search common or scientific names, including species not detected by AI.",
  "speciesName": "Species name",
  "loading": "Preparing the clips…",
  "loadError": "The prototype audio could not be loaded. Generate the trial data with poc/multispecies_sampling.py, then reload.",
  "storageError": "This browser could not save the draft. Copy the trial annotations before closing.",
  "start": "Start",
  "resume": "Continue",
  "progress": "{done} of {total} clips completed",
  "position": "Clip {n} of {total}",
  "annotate": "Choose the animals you hear",
  "checkTitle": "Listen for “{name}”",
  "selectHint": "Choose an animal from the list, then drag across its call on the diagram.",
  "dragHint": "Drag across the “{name}” call.",
  "empty": "Selected calls appear here with the animal’s name and start and end times.",
  "saved": "Changes saved",
  "noneSelected": "Choose an animal from the list first.",
  "invalidRange": "Enter start and end times between 0 and 15 seconds, with the end after the start.",
  "remove": "Delete",
  "playRange": "Play range",
  "noMatch": "No matching species.",
  "added": "Added",
  "checkQuestion": "Can you hear “{name}” in this clip?",
  "checkHelp": "Listen and answer. Choose “Cannot tell” when you are unsure.",
  "answered": "Saved answer: {answer}",
  "next": "Save and continue →",
  "finish": "Save and return to the list",
  "footer": "Your entries are saved automatically. You can return at any time.",
  "checkFooter": "You can return to previous clips and correct any answer.",
  "spectrogram": "Spectrogram of this 15-second clip. Time runs from left to right.",
  "catalogCount": "Showing {count} species",
  "catalogSearch": "Enter a species name.",
  "filterPlaceholder": "Filter by name",
  "catalogPlaceholder": "e.g. Japanese Bush Warbler",
  "reviewTitle": "Animals you heard",
  "backHome": "← Back to the start",
  "reviewIntro": "Review the animals you selected and listen to the clips.",
  "qualityTitle": "Evaluate AI accuracy",
  "qualityWhy": "Evaluating missed or misidentified calls also needs clips where the species is not heard. Choose a species and listen to clips you have not answered yet.",
  "qualitySpecies": "Species to evaluate",
  "chooseSpecies": "Choose a species",
  "qualityCounts": "Heard {present} · Not heard {absent} · Uncertain {unknown} · Unchecked {missing}",
  "beginCheck": "Check this species in the clips",
  "reviewAnswers": "Review saved answers",
  "qualityPrototype": "This prototype saves your answers. It does not calculate AI accuracy yet.",
  "continueRecording": "Continue listening",
  "heardCount": "Clips with this call: {count}",
  "revisit": "Review the audio",
  "noNamedCalls": "Animal names appear here once you mark their calls.",
  "unknownCount": "Unidentified call ranges: {count}",
  "stageNote": "Answer whether you can hear this species to help evaluate AI accuracy. You do not need to answer again for clips where you already marked its call.",
  "alreadyMarked": "You have already marked this call in the clip. You do not need to answer again.",
  "zoomIn": "Zoom in",
  "zoomOut": "Zoom out",
  "zoomFit": "Show the whole clip",
  "zoomShort": "Scroll to zoom",
  "viewLabel": "{start}–{end} s / 15 s",
  "panHint": "Use the scrollbars to move around the enlarged diagram.",
  "playView": "Play visible range",
  "audioError": "Audio could not be played. Please try again.",
  "zoomHint": "Scroll to zoom. Scroll on the time axis for horizontal zoom, or the frequency axis for vertical zoom.",
  "plotLabel": "Spectrogram. Scroll to zoom both axes at the same rate.",
  "timeAxis": "Time (s)",
  "frequencyAxis": "Frequency (kHz)",
  "axisHelp": "Scroll, or press + or − to zoom this axis."
 }
};
const $ = id => document.getElementById(id);
let locale = navigator.language.startsWith('ja') ? 'ja' : 'en';
const t = (key, args={}) => Object.entries(args).reduce((s,[k,v])=>s.replaceAll(`{${k}}`,String(v)),messages[locale][key] ?? messages.en[key]);
const base = new URL('../../.local-data/annotation-design/', location.href);
const storageKey = 'echoroo-annotation-prototype-v1-' + (new URLSearchParams(location.search).get('trial') || 'default');
let data, state, active = null, dragging = null, playingUntil = null, stopTimer;
let mode = 'annotate', checkIndex = 0, checkSpecies = null, storageFailed = false;
let zoom = {x:1,y:1};
const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const name = species => species?.common_names?.[locale] || species?.common_names?.en || species?.scientific_name || t('unknown');
const taxon = id => data.catalog.find(s=>s.id===id);
const currentIndex = () => mode==='check'?checkIndex:state.index;
const entry = () => state.clips[currentIndex()] ||= {ranges:[],saved:false,checks:{}};
const currentClip = () => data.clips[currentIndex()];
const checkTarget = () => taxon(checkSpecies);
function annotationAnswer(clip, speciesId){
 return clip?.ranges.some(r=>r.species_id===speciesId)?'present':clip?.checks[speciesId] || null;
}
function timeAt(x, plot, viewport){
 const visibleX=Math.max(viewport.left,Math.min(viewport.right,x));
 return Math.max(0,Math.min(15,(visibleX-plot.left)/plot.width*15));
}
function visibleWindow(){
 const plot=$('plot').getBoundingClientRect(),viewport=viewportBounds();
 return [timeAt(viewport.left,plot,viewport),timeAt(viewport.right,plot,viewport)];
}
function viewportBounds(){
 const viewport=$('plotScroll'),rect=viewport.getBoundingClientRect();
 return {left:rect.left,top:rect.top,right:rect.left+viewport.clientWidth,bottom:rect.top+viewport.clientHeight,width:viewport.clientWidth,height:viewport.clientHeight};
}
function scaledZoom(current,axis,factor){
 // A normal zoom applies one factor to both axes, including at the limits.
 if(axis==='both')factor=Math.max(1/Math.min(current.x,current.y),Math.min(8/Math.max(current.x,current.y),factor));
 return {x:axis==='y'?current.x:Math.max(1,Math.min(8,current.x*factor)),y:axis==='x'?current.y:Math.max(1,Math.min(8,current.y*factor))};
}
function persist(){
 try { localStorage.setItem(storageKey, JSON.stringify(state)); storageFailed=false; $('saved').textContent=t('saved'); }
 catch { storageFailed=true; $('saved').textContent=t('storageError'); }
}
function stop(){ clearTimeout(stopTimer);$('player').pause(); playingUntil=null; }
function scheduleStop(){
 clearTimeout(stopTimer);
 if(playingUntil!==null&&!$('player').paused)stopTimer=setTimeout(stop,Math.max(0,(playingUntil-$('player').currentTime)/$('player').playbackRate*1000));
}
function playInterval(start,end){
 const [visibleStart,visibleEnd]=visibleWindow();
 if(start<visibleStart||end>visibleEnd){
  if(end-start>visibleEnd-visibleStart){zoom={x:1,y:1};applyDimensions();}
  $('plotScroll').scrollLeft=(start+end)/2/15*$('plotTrack').clientWidth-$('plotScroll').clientWidth/2;
  rememberView();
 }
 stop();$('player').currentTime=start;playingUntil=end;
 $('player').play().then(scheduleStop).catch(()=>{$('saved').textContent=t('audioError');});
}
function localize(){
 document.documentElement.lang=locale;
 document.title=`Echoroo · ${t('title')}`;
 document.querySelectorAll('[data-msg]').forEach(el=>el.textContent=t(el.dataset.msg));
 $('locale').value=locale; $('locale').ariaLabel=t('language');
 $('filter').placeholder=t('filterPlaceholder'); $('filter').ariaLabel=t('filter');
 $('catalogQuery').placeholder=t('catalogPlaceholder'); $('catalogQuery').ariaLabel=t('speciesName');
 $('spectrogram').alt=t('spectrogram');
 $('plotScroll').ariaLabel=t('plotLabel');
 $('timeAxis').ariaLabel=t('timeAxis')+' · '+t('axisHelp');
 $('frequencyAxis').ariaLabel=t('frequencyAxis')+' · '+t('axisHelp');
 $('timeAxis').title=t('axisHelp');$('frequencyAxis').title=t('axisHelp');
 $('zoomIn').ariaLabel=t('zoomIn');$('zoomOut').ariaLabel=t('zoomOut');
 if(data){if($('saved').textContent)$('saved').textContent=t(storageFailed?'storageError':'saved');renderHome();renderReview();renderPalette();renderRanges();renderTask();renderCatalog();renderZoom();}
}
function renderHome(){
 document.querySelectorAll('.site').forEach(el=>el.textContent=data.site);
 $('progress').textContent=t('progress',{done:Object.values(state.clips).filter(c=>c.saved).length,total:data.clips.length});
 $('start').textContent=Object.values(state.clips).some(c=>c.saved||c.ranges.length)?t('resume'):t('start');
 $('openReview').hidden=!Object.values(state.clips).some(c=>c.saved||c.ranges.length||Object.keys(c.checks).length);
}
function renderReview(){
 const ids=new Set();
 for(const clip of Object.values(state.clips)){
  clip.ranges.forEach(r=>{if(r.species_id)ids.add(r.species_id);});
  Object.entries(clip.checks).forEach(([id,answer])=>{if(answer==='present')ids.add(id);});
 }
 const recorded=[...ids].map(taxon).filter(Boolean).sort((a,b)=>name(a).localeCompare(name(b),locale));
 $('recordedSpecies').innerHTML=recorded.length?recorded.map(s=>{
  const count=Object.values(state.clips).filter(c=>annotationAnswer(c,s.id)==='present').length;
  return `<div class="recorded-species row between"><div><strong>${escape(name(s))}</strong><small>${escape(s.scientific_name)}</small></div><span>${escape(t('heardCount',{count}))}</span><button data-listen="${escape(s.id)}">${escape(t('revisit'))}</button></div>`;
 }).join(''):`<p class="muted">${escape(t('noNamedCalls'))}</p>`;
 const unknown=Object.values(state.clips).reduce((count,clip)=>count+clip.ranges.filter(r=>r.species_id===null).length,0);
 if(unknown)$('recordedSpecies').innerHTML+=`<div class="recorded-species row between"><span>${escape(t('unknownCount',{count:unknown}))}</span><button data-listen="unknown">${escape(t('revisit'))}</button></div>`;
 const selected=$('qualitySpecies').value||state.checkSpecies||recorded[0]?.id||'';
 $('qualitySpecies').innerHTML=`<option value="">${escape(t('chooseSpecies'))}</option>`+state.palette.map(taxon).filter(Boolean).sort((a,b)=>name(a).localeCompare(name(b),locale)).map(s=>`<option value="${escape(s.id)}">${escape(name(s))} · ${escape(s.scientific_name)}</option>`).join('');
 $('qualitySpecies').value=selected;renderQualityCounts();
}
function renderQualityCounts(){
 const id=$('qualitySpecies').value,counts={present:0,absent:0,unknown:0,missing:0};
 for(let i=0;i<data.clips.length;i++)counts[annotationAnswer(state.clips[i],id)||'missing']++;
 $('qualityCounts').textContent=id?t('qualityCounts',counts):'';
 $('beginCheck').disabled=!id;
 $('beginCheck').textContent=t(id&&counts.missing===0?'reviewAnswers':'beginCheck');
}
function showReview(){
 stop();persist();renderReview();$('home').hidden=true;$('editor').hidden=true;$('review').hidden=false;
 $('review').querySelector('h1').tabIndex=-1;$('review').querySelector('h1').focus({preventScroll:true});window.scrollTo(0,0);
}
function renderTask(){
 const checking=mode==='check';
 $('taskTitle').textContent=checking?t('checkTitle',{name:name(checkTarget())}):t('annotate');
 $('position').textContent=t('position',{n:currentIndex()+1,total:data.clips.length});
 $('timestamp').textContent=new Intl.DateTimeFormat(locale,{timeZone:data.timezone,dateStyle:'medium',timeStyle:'medium'}).format(new Date(currentClip().local_time));
 $('checkCard').hidden=!checking; $('speciesPanel').hidden=checking;
 $('ranges').hidden=checking&&entry().ranges.length===0;
 document.querySelector('.workspace').style.gridTemplateColumns=checking?'minmax(0,1fr)':'';
 $('keyboard').hidden=checking;
 $('instruction').hidden=checking;
 $('stageNote').hidden=!checking;$('stageNote').textContent=t('stageNote');
 $('targetScientific').hidden=!checking;$('targetScientific').textContent=checking?checkTarget().scientific_name:'';
 $('footerNote').textContent=t(checking?'checkFooter':'footer');
 $('previous').disabled=checking?checkIndex===0:state.index===0;
 $('next').textContent=t((checking?nextUnanswered(checkIndex+1)<0:state.index+1===data.clips.length)?'finish':'next');
 if(checking){
  const target=checkTarget();
  $('checkQuestion').textContent=t('checkQuestion',{name:name(target)});
  const marked=entry().ranges.some(r=>r.species_id===target.id);
  const answer=annotationAnswer(entry(),target.id);
  $('checkHelp').textContent=t(marked?'alreadyMarked':'checkHelp');$('answers').hidden=marked;
  $('checkAnswer').textContent=answer?t('answered',{answer:t(answer==='unknown'?'uncertain':answer)}):'';
  $('answers').querySelectorAll('button').forEach(b=>{b.ariaPressed=String(answer===b.dataset.answer);b.classList.toggle('primary',answer===b.dataset.answer);});
  $('next').disabled=!answer;
 } else { $('next').disabled=false; }
}
function nextUnanswered(first){
 return data.clips.findIndex((clip,i)=>i>=first&&!annotationAnswer(state.clips[i],checkSpecies));
}
function renderZoom(){
 if(!$('plotScroll').clientWidth)return;
 const [start,end]=visibleWindow();
 $('viewLabel').textContent=t('viewLabel',{start:start.toFixed(2),end:end.toFixed(2)});
 $('zoomOut').disabled=Math.min(zoom.x,zoom.y)<=1;$('zoomIn').disabled=Math.max(zoom.x,zoom.y)>=8;$('zoomFit').disabled=zoom.x===1&&zoom.y===1;
 $('panHint').hidden=zoom.x===1&&zoom.y===1;$('playView').hidden=zoom.x===1;
 $('ticks').innerHTML=[0,1/3,2/3,1].map(f=>`<span style="left:${f*100}%">${+(start+(end-start)*f).toFixed(2)}</span>`).join('');
 const plot=$('plot').getBoundingClientRect(),viewport=viewportBounds(),max=currentClip().frequency_max_hz;
 $('frequencyTicks').innerHTML=Number.isFinite(max)?[0,.5,1].map(f=>{
  const frequency=Math.max(0,Math.min(max,max*(1-(viewport.top+f*viewport.height-plot.top)/plot.height)));
  return `<span style="top:${f*100}%">${+(frequency/1000).toFixed(2)}</span>`;
 }).join(''):'';
 renderMarks();
}
function rememberView(){
 if(!data||!$('plotScroll').clientWidth)return;
 state.views[currentIndex()]={...zoom,left:$('plotScroll').scrollLeft/$('plotTrack').clientWidth,top:$('plotScroll').scrollTop/$('plotTrack').clientHeight};
 renderZoom();
}
function applyDimensions(){
 $('plotTrack').style.width=`${zoom.x*100}%`;$('plotTrack').style.height=`${zoom.y*100}%`;
}
function restoreView(){
 const view=state.views[currentIndex()]||{};
 // Earlier drafts only scaled time; keep annotations and start the new view fitted.
 zoom={x:view.x||1,y:view.y||1};applyDimensions();
 $('plotScroll').scrollLeft=(view.left||0)*$('plotTrack').clientWidth;
 $('plotScroll').scrollTop=(view.top||0)*$('plotTrack').clientHeight;renderZoom();
}
function setZoom(axis,factor,pointer){
 const viewport=viewportBounds(),plot=$('plot').getBoundingClientRect();
 const x=pointer?Math.max(viewport.left,Math.min(viewport.right,pointer.x)):viewport.left+viewport.width/2;
 const y=pointer?Math.max(viewport.top,Math.min(viewport.bottom,pointer.y)):viewport.top+viewport.height/2;
 const fractionX=(x-plot.left)/plot.width,fractionY=(y-plot.top)/plot.height;
 zoom=scaledZoom(zoom,axis,factor);applyDimensions();
 $('plotScroll').scrollLeft=fractionX*$('plotTrack').clientWidth-(x-viewport.left);
 $('plotScroll').scrollTop=fractionY*$('plotTrack').clientHeight-(y-viewport.top);
 rememberView();persist();
}
function wheelZoom(event,axis){
 if(axis==='both'&&Math.abs(event.deltaX)>Math.abs(event.deltaY)&&!event.ctrlKey)return;
 event.preventDefault();if(dragging!==null)return;
 const viewport=viewportBounds();
 const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?viewport.height:1);
 const factor=Math.exp(-Math.max(-120,Math.min(120,delta))*.004);
 setZoom(axis,factor,{x:axis==='y'?viewport.left+viewport.width/2:event.clientX,y:axis==='x'?viewport.top+viewport.height/2:event.clientY});
}
function renderPalette(){
 const scrollTop=$('palette').scrollTop;
 const query=$('filter').value.trim().toLocaleLowerCase(locale);
 // Sorting depends only on locale, never on the current clip or its annotations.
 const species=state.palette.map(taxon).filter(Boolean).sort((a,b)=>name(a).localeCompare(name(b),locale));
 const visible=species.filter(s=>!query||[name(s),s.scientific_name,...Object.values(s.common_names||{})].some(v=>v.toLocaleLowerCase(locale).includes(query)));
 $('palette').innerHTML=visible.length?visible.map(s=>`<button class="species" data-species="${escape(s.id)}" aria-pressed="${active===s.id}">${escape(name(s))}<small>${escape(s.scientific_name)}</small></button>`).join(''):`<p>${escape(t('noMatch'))}</p>`;
 $('palette').scrollTop=scrollTop;
 $('unknown').ariaPressed=String(active==='unknown');
 $('instruction').textContent=active===null?t('selectHint'):active==='unknown'?t('unknownHint'):t('dragHint',{name:name(taxon(active))});
}
function renderMarks(preview){
 const width=$('plot').clientWidth,height=$('plot').clientHeight;
 if(!width)return;
 $('marks').setAttribute('viewBox',`0 0 ${width} ${height}`);
 const ranges=entry().ranges;
 const lines=ranges.map((r,i)=>({start:r.start,end:r.end,index:i+1}));
 if(preview)lines.push({...preview,index:'+'});
 // Draw in screen pixels so zoom does not enlarge strokes or labels.
 $('marks').innerHTML=lines.map((r,i)=>{
  const x=r.start/15*width,w=(r.end-r.start)/15*width,y=$('plotScroll').scrollTop+12+(i%4)*16;
  return `<g stroke="#fff" stroke-width="2"><path d="M${x},${height} V${y} H${x+w} V${height}" fill="none" stroke="#30263d" stroke-width="5"/><path d="M${x},${height} V${y} H${x+w} V${height}" fill="none"/><text x="${x+5}" y="${y+15}" stroke="#30263d" stroke-width="3" paint-order="stroke" fill="white" font-size="14">${r.index}</text></g>`;
 }).join('');
}
function renderRanges(){
 const ranges=entry().ranges;
 $('ranges').innerHTML=ranges.length?ranges.map((r,i)=>{
  const s=taxon(r.species_id),n=r.species_id===null?t('unknown'):name(s);
  return `<div class="range-row" data-range="${i}"><div class="name"><strong>${i+1}. ${escape(n)}</strong>${s?`<small>${escape(s.scientific_name)}</small>`:''}</div><label><span hidden>${escape(t('startTime'))}</span><input data-bound="start" aria-label="${escape(n+' '+t('startTime'))}" type="number" min="0" max="15" step=".1" value="${r.start}"></label><span>–</span><label><span hidden>${escape(t('endTime'))}</span><input data-bound="end" aria-label="${escape(n+' '+t('endTime'))}" type="number" min="0" max="15" step=".1" value="${r.end}"></label><button data-action="play">${escape(t('playRange'))}</button><button data-action="remove">${escape(t('remove'))}</button></div>`;
 }).join(''):`<p class="muted">${escape(t('empty'))}</p>`;
 renderMarks();
}
function showClip(){
 stop();
 const clip=currentClip();
 $('player').src=new URL(clip.audio,base).href;
 $('spectrogram').src=new URL(clip.image,base).href;
 $('rangeError').textContent=''; $('saved').textContent=storageFailed?t('storageError'):'';
 renderRanges();renderTask();renderPalette();restoreView();
}
function openEditor(speciesId=null){
 mode=speciesId?'check':'annotate';
 if(speciesId){
  checkSpecies=speciesId;checkIndex=nextUnanswered(0);if(checkIndex<0)checkIndex=0;
  state.checkSpecies=speciesId;
 }
 $('home').hidden=true;$('review').hidden=true;$('editor').hidden=false;
 showClip();$('taskTitle').tabIndex=-1;$('taskTitle').focus({preventScroll:true});window.scrollTo(0,0);
}
function goHome(){
 stop();persist();renderHome();$('editor').hidden=true;$('review').hidden=true;$('home').hidden=false;
 $('start').focus({preventScroll:true});window.scrollTo(0,0);
}
function addRange(start,end){
 if(active===null){$('rangeError').textContent=t('noneSelected');return;}
 if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>15||end<=start){$('rangeError').textContent=t('invalidRange');return;}
 entry().ranges.push({species_id:active==='unknown'?null:active,start:Math.round(start*100)/100,end:Math.round(end*100)/100});
 entry().saved=false;$('rangeError').textContent='';persist();renderRanges();renderTask();
}
function renderCatalog(){
 if(!data)return;
 const q=$('catalogQuery').value.trim().toLocaleLowerCase(locale);
 const rank=s=>Math.min(...[name(s),s.scientific_name,...Object.values(s.common_names||{})].map(v=>{v=v.toLocaleLowerCase(locale);return v===q?0:v.startsWith(q)?1:v.includes(q)?2:3;}));
 const rows=q?data.catalog.map(s=>({s,r:rank(s)})).filter(x=>x.r<3).sort((a,b)=>a.r-b.r||name(a.s).length-name(b.s).length||name(a.s).localeCompare(name(b.s),locale)).slice(0,40):[];
 $('catalogResults').innerHTML=rows.map(({s})=>`<button class="species" data-add="${escape(s.id)}" ${state.palette.includes(s.id)?'disabled':''}>${escape(name(s))}${state.palette.includes(s.id)?' · '+escape(t('added')):''}<small>${escape(s.scientific_name)}</small></button>`).join('');
 $('catalogCount').textContent=!q?t('catalogSearch'):rows.length?t('catalogCount',{count:rows.length}):t('noMatch');
}
$('locale').onchange=()=>{locale=$('locale').value;localize();};
$('filter').oninput=renderPalette;
$('catalogQuery').oninput=renderCatalog;
$('palette').onclick=e=>{const button=e.target.closest('[data-species]');if(button){active=button.dataset.species;renderPalette();}};
$('unknown').onclick=()=>{active='unknown';renderPalette();};
$('openCatalog').onclick=()=>{$('catalogDialog').showModal();renderCatalog();$('catalogQuery').focus();};
$('closeCatalog').onclick=()=>$('catalogDialog').close();
$('catalogResults').onclick=e=>{const b=e.target.closest('[data-add]');if(b){state.palette.push(b.dataset.add);active=b.dataset.add;persist();$('filter').value='';renderPalette();$('catalogDialog').close();}};
$('start').onclick=()=>openEditor();$('back').onclick=showReview;
$('openReview').onclick=showReview;$('reviewHome').onclick=goHome;
$('continueRecording').onclick=()=>openEditor();
$('qualitySpecies').onchange=renderQualityCounts;
$('beginCheck').onclick=()=>openEditor($('qualitySpecies').value);
$('recordedSpecies').onclick=e=>{const button=e.target.closest('[data-listen]');if(button){active=button.dataset.listen;state.index=data.clips.findIndex((clip,i)=>active==='unknown'?state.clips[i]?.ranges.some(r=>r.species_id===null):annotationAnswer(state.clips[i],active)==='present');openEditor();}};
$('zoomIn').onclick=()=>setZoom('both',2);$('zoomOut').onclick=()=>setZoom('both',.5);
$('zoomFit').onclick=()=>{zoom={x:1,y:1};applyDimensions();$('plotScroll').scrollLeft=0;$('plotScroll').scrollTop=0;rememberView();persist();};
for(const [id,axis] of [['plotScroll','both'],['timeAxis','x'],['frequencyAxis','y']]){
 $(id).addEventListener('wheel',event=>wheelZoom(event,axis),{passive:false});
 $(id).addEventListener('keydown',event=>{if(!event.ctrlKey&&!event.metaKey&&!event.altKey&&['+','=','-'].includes(event.key)){event.preventDefault();setZoom(axis,event.key==='-'?.5:2);}});
}
$('plotScroll').onscroll=rememberView;
$('playView').onclick=()=>playInterval(...visibleWindow());
$('addRange').onclick=()=>addRange(Number($('from').value),Number($('to').value));
const point=e=>timeAt(e.clientX,$('plot').getBoundingClientRect(),viewportBounds());
$('plot').onpointerdown=e=>{if(mode==='check'||e.button!==0)return;if(active===null){$('instruction').textContent=t('noneSelected');return;}stop();dragging=point(e);$('plot').setPointerCapture(e.pointerId);};
$('plot').onpointermove=e=>{if(dragging!==null)renderMarks({start:Math.min(dragging,point(e)),end:Math.max(dragging,point(e))});};
$('plot').onpointerup=e=>{if(dragging===null)return;const end=point(e);if(Math.abs(end-dragging)>=.05)addRange(Math.min(dragging,end),Math.max(dragging,end));dragging=null;renderMarks();};
$('plot').onpointercancel=()=>{dragging=null;renderMarks();};
$('ranges').onclick=e=>{const row=e.target.closest('[data-range]'),button=e.target.closest('[data-action]');if(!row||!button)return;const index=Number(row.dataset.range),r=entry().ranges[index];if(button.dataset.action==='remove'){entry().ranges.splice(index,1);entry().saved=false;persist();renderRanges();renderTask();}else{playInterval(r.start,r.end);}};
$('ranges').oninput=e=>{const input=e.target.closest('[data-bound]');if(!input)return;const index=Number(input.closest('[data-range]').dataset.range),r=entry().ranges[index],edited={...r,[input.dataset.bound]:Number(input.value)};if(input.value!==''&&Number.isFinite(edited.start)&&Number.isFinite(edited.end)&&edited.start>=0&&edited.end<=15&&edited.start<edited.end){entry().ranges[index]=edited;entry().saved=false;persist();renderMarks();$('rangeError').textContent='';}else{$('rangeError').textContent=t('invalidRange');$('keyboard').open=true;}};
$('player').ontimeupdate=()=>{$('playhead').style.left=`${$('player').currentTime/15*100}%`;scheduleStop();};
$('player').onplay=scheduleStop;$('player').onpause=()=>clearTimeout(stopTimer);$('player').onratechange=scheduleStop;
$('answers').onclick=e=>{const b=e.target.closest('[data-answer]');if(b){entry().checks[checkTarget().id]=b.dataset.answer;persist();renderTask();}};
$('next').onclick=()=>{stop();if(mode==='check'){const next=nextUnanswered(checkIndex+1);if(next<0){showReview();return;}checkIndex=next;}else{entry().saved=true;if(state.index+1===data.clips.length){showReview();return;}state.index++;}persist();showClip();};
$('previous').onclick=()=>{stop();if(mode==='check'){checkIndex--;}else{state.index--;}persist();showClip();};
$('download').onclick=()=>{
 const annotations=Object.entries(state.clips).map(([index,answer])=>({...data.clips[Number(index)],...answer,ranges:answer.ranges.map(r=>({...r,source_start_s:data.clips[Number(index)].start_s+r.start,source_end_s:data.clips[Number(index)].start_s+r.end}))}));
 const payload={purpose:'ux_prototype_not_validated_ground_truth',timezone:data.timezone,palette_ids:state.palette,annotations};
 $('recordJSON').value=JSON.stringify(payload,null,2);$('copyStatus').textContent='';$('recordDialog').showModal();
};
$('closeRecord').onclick=()=>$('recordDialog').close();
$('copyRecord').onclick=async()=>{try{await navigator.clipboard.writeText($('recordJSON').value);$('copyStatus').textContent=t('copied');}catch{$('recordJSON').focus();$('recordJSON').select();$('copyStatus').textContent=t('copyFallback');}};
window.addEventListener('pagehide',()=>{stop();if(state)persist();});
window.addEventListener('resize',()=>{if(data)renderZoom();});
localize();$('loading').textContent=t('loading');
try {
 const response=await fetch(new URL('preview.json',base));if(!response.ok)throw new Error('Preview data unavailable');data=await response.json();
 state={index:0,palette:data.palette.map(s=>s.id),clips:{}};
 try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved)state=saved;}catch{storageFailed=true;}
 state.views ||= {};
 state.index=Math.max(0,Math.min(state.index,data.clips.length-1));
 $('loading').hidden=true;$('home').hidden=false;localize();
} catch { $('loading').textContent=t('loadError');$('loading').classList.add('error'); }

(() => {
  'use strict';
  const el = id => document.getElementById('video-' + id);
  const activeStates = new Set(['queued','preparing','exporting','cancelling']);
  const statusLabels = {queued:'等待中',preparing:'准备画面',exporting:'正在导出',cancelling:'正在停止',completed:'已完成',failed:'导出失败',cancelled:'已停止',interrupted:'已中断'};
  const clock = n => { n = Math.max(0, Number(n) || 0); return `${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`; };
  const fields = {title:'title',author:'author',text:'text',font_size:'font-size',line_spacing:'line-spacing',color:'color',shade:'shade',crop_x:'crop-x',crop_y:'crop-y',intro:'intro',outro:'outro',bgm_volume:'music-volume'};
  const numeric = new Set(['font_size','line_spacing','shade','crop_x','crop_y','intro','outro','bgm_volume']);
  const initial = (() => {try {return JSON.parse(localStorage.getItem('bensheng-video-draft')) || {};} catch {return {};}})();
  let selection = {image_id:null,job_id:null,audio_id:null,bgm_id:null,aspect:'portrait',...initial.selection};
  let library = {assets:[],sources:[]}, jobs = [], layout = null, background = null, sourceText = '', loaded = false;
  let previewTimer, saveTimer, previewSequence = 0, loading = false, uploading = 0, pollTimer, pollBusy = false;
  let historySignature = '';
  let playing = false, position = 0, anchor = 0, currentExport = null, frame, sourceSequence = 0, pendingConfig = false;
  const canvas = el('canvas'), ctx = canvas.getContext('2d');
  const narration = el('narration-player'), music = el('music-player');
  for (const [key, id] of Object.entries(fields)) if (initial.fields?.[key] !== undefined) el(id).value = initial.fields[key];
  function request() {
    const result = {...selection};
    for (const [key,id] of Object.entries(fields)) result[key] = numeric.has(key) ? Number(el(id).value) : el(id).value;
    return result;
  }
  function save() {
    const values = {};
    for (const [key,id] of Object.entries(fields)) values[key] = el(id).value;
    try {localStorage.setItem('bensheng-video-draft',JSON.stringify({selection,fields:values}));el('draft-status').textContent='草稿已保存';}
    catch {el('draft-status').textContent='草稿未能保存，请检查浏览器存储空间';}
  }
  function hasMaterials() {return selection.image_id && (selection.job_id || selection.audio_id) && el('text').value.trim();}
  function updateLabels() {
    el('text-count').textContent=el('text').value.length.toLocaleString()+' 字';
    el('shade-value').textContent=Math.round(Number(el('shade').value)*100)+'%';
    el('music-volume-value').textContent=Math.round(Number(el('music-volume').value)*100)+'%';
    el('text-match').textContent=sourceText && el('text').value.trim()!==sourceText.trim() ? '正文已修改，请确认仍与朗诵内容一致' : '保留换行，自动排版';
    el('music-settings').hidden=!selection.bgm_id;
    document.querySelectorAll('[data-video-aspect]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.videoAspect===selection.aspect)));
    canvas.classList.toggle('landscape',selection.aspect==='landscape');
    el('export').disabled=!layout || pendingConfig || uploading>0 || loading || jobs.filter(j=>activeStates.has(j.status)).length>=2;
    el('play').disabled=!layout || pendingConfig || uploading>0;
    el('seek').disabled=!layout || pendingConfig;
  }
  function pause() {
    if (playing) position = Math.min(layout?.duration || 0,(performance.now()-anchor)/1000);
    playing=false;cancelAnimationFrame(frame);narration.pause();music.pause();
    el('play').textContent='▶';el('play').setAttribute('aria-label','播放预览');
  }
  function invalidate() {
    pause();pendingConfig=true;previewSequence++;clearTimeout(previewTimer);clearTimeout(saveTimer);
    updateLabels();saveTimer=setTimeout(save,200);previewTimer=setTimeout(buildPreview,350);
  }
  async function buildPreview() {
    const seq=++previewSequence;
    if (!hasMaterials()) {layout=null;pendingConfig=false;el('preview-note').textContent='选好背景图、正文和朗诵音频，即可预览。';drawEmpty();updateLabels();return;}
    el('preview-note').textContent='正在更新画面…';
    try {
      const next=await api('/api/video/preview',{method:'POST',body:JSON.stringify(request())});
      const picture = new Image();picture.src=next.background_url;await picture.decode();
      if(seq!==previewSequence)return;
      layout=next;background=picture;pendingConfig=false;
      canvas.width=layout.width;canvas.height=layout.height;
      position=Math.min(position,layout.duration);el('seek').max=String(layout.duration);
      el('preview-note').textContent='正文全程匀速上滚；可拖动进度，检查任意位置的画面。';
      el('total-duration').textContent=`视频时长 ${clock(layout.duration)}`;
      draw();updateLabels();
    } catch(error) {if(seq!==previewSequence)return;layout=null;pendingConfig=false;el('preview-note').textContent=error.message;updateLabels();}
  }
  function drawEmpty() {
    canvas.width=selection.aspect==='portrait'?1080:1920;canvas.height=selection.aspect==='portrait'?1920:1080;
    const w=canvas.width,h=canvas.height;
    ctx.fillStyle='#344d40';ctx.fillRect(0,0,w,h);
    ctx.strokeStyle='#728173';ctx.lineWidth=1;ctx.strokeRect(w*.1,h*.12,w*.8,h*.76);
    ctx.textAlign='center';ctx.fillStyle='#edeadf';ctx.font='52px "Songti SC", serif';
    ctx.fillText(el('title').value || '让文字，有声有画。',w/2,h*.45,w*.75);
    ctx.font='26px "PingFang SC", sans-serif';ctx.fillStyle='#c7cfbf';
    ctx.fillText('选择素材，开始预览',w/2,h*.53);
    ctx.font='18px "PingFang SC", sans-serif';ctx.fillText('本 声  /  VIDEO STUDIO',w/2,h*.82);
    el('clock').textContent='0:00 / 0:00';
  }
  function draw() {
    if(!layout || !background){drawEmpty();return;}
    const l=layout,t=position;
    ctx.globalAlpha=1;ctx.drawImage(background,0,0,l.width,l.height);ctx.fillStyle=l.color;
    if(t<l.intro){
      ctx.globalAlpha=Math.min(1,t/.35,(l.intro-t)/.35);
      ctx.textAlign='center';ctx.textBaseline='middle';ctx.font=`${l.title_font_size}px "Songti SC", serif`;
      l.title_lines.forEach((line,i)=>ctx.fillText(line,l.width/2,l.height*.45+(i-(l.title_lines.length-1)/2)*l.title_font_size*1.4));
      if(l.author){ctx.font='34px "Songti SC", serif';ctx.fillText('作者：'+l.author,l.width/2,l.height*.45+(l.title_lines.length+1)/2*l.title_font_size*1.4);}
    } else {
      ctx.save();ctx.beginPath();ctx.rect(l.left-4,l.top,l.width-l.left*2+8,l.bottom-l.top);ctx.clip();
      ctx.font=`${l.font_size}px "Songti SC", serif`;ctx.textAlign='left';ctx.textBaseline='top';
      const y=l.start_y-l.speed*Math.min(l.audio_duration,Math.max(0,t-l.intro));
      for(const line of l.lines){
        if(t<line.start || t>=line.end)continue;
        const ly=y+line.offset;if(ly<l.top-l.line_height || ly>l.bottom)continue;
        ctx.globalAlpha=Math.max(0,Math.min(1,(t-line.start)/line.fade_in,(line.end-t)/line.fade_out));
        ctx.fillText(line.text,l.left,ly);
      }
      ctx.restore();
    }
    ctx.globalAlpha=1;el('seek').value=String(position);el('clock').textContent=`${clock(position)} / ${clock(l.duration)}`;
  }
  function musicFade(t){const f=Math.min(2,layout.duration/3);return Math.max(0,Math.min(1,t/f,(layout.duration-t)/f));}
  function setAudioTime(audio,time){if(Number.isFinite(audio.duration) && Math.abs(audio.currentTime-time)>.18)audio.currentTime=Math.max(0,Math.min(time,audio.duration));}
  function syncAudio(force=false){
    if(!layout)return;
    const body=position-layout.intro;
    if(body>=0 && body<layout.audio_duration){
      if(force || Math.abs(narration.currentTime-body)>.3)setAudioTime(narration,body);
      if(playing && narration.paused)narration.play().catch(()=>{pause();toast('无法播放朗诵音频，请先确认素材可正常试听。',true);});
    }else narration.pause();
    if(selection.bgm_id && Number.isFinite(music.duration) && music.duration>0){
      music.loop=true;music.volume=Number(el('music-volume').value)*musicFade(position);
      if(force)setAudioTime(music,position%music.duration);
      if(playing && music.paused)music.play().catch(()=>{pause();toast('无法播放背景音乐，请检查文件。',true);});
    }else music.pause();
  }
  function tick(){
    if(!playing || !layout)return;
    position=Math.min(layout.duration,(performance.now()-anchor)/1000);draw();syncAudio();
    if(position>=layout.duration){pause();return;}frame=requestAnimationFrame(tick);
  }
  function seek(time){pause();position=Math.max(0,Math.min(time,layout?.duration||0));syncAudio(true);returnToPreview();}
  el('play').onclick=async()=>{
    if(!layout)return;if(playing){pause();return;}
    returnToPreview();
    if(position>=layout.duration)position=0;
    el('result-player').pause();
    document.querySelectorAll('audio').forEach(audio=>{if(audio!==narration && audio!==music)audio.pause();});
    // Unlock both audio elements in this user gesture, including a delayed narration start.
    try {const tracks=[narration,...(selection.bgm_id?[music]:[])];await Promise.all(tracks.map(async audio=>{audio.muted=true;try {await audio.play();audio.pause();} finally {audio.muted=false;}}));}
    catch(error){toast('无法播放音频，请先在素材区试听确认。',true);return;}
    playing=true;anchor=performance.now()-position*1000;el('play').textContent='Ⅱ';el('play').setAttribute('aria-label','暂停预览');syncAudio(true);tick();
  };
  el('seek').oninput=()=>seek(Number(el('seek').value));
  el('jump-intro').onclick=()=>seek(Math.min(.7,(layout?.intro||0)/2));
  el('jump-body').onclick=()=>seek((layout?.intro||0)+(layout?.audio_duration||0)*.45);
  el('jump-end').onclick=()=>seek(Math.max(0,(layout?.duration||0)-1));
  for(const id of Object.values(fields))el(id).addEventListener('input',invalidate);
  document.querySelectorAll('[data-video-aspect]').forEach(button=>button.onclick=()=>{selection.aspect=button.dataset.videoAspect;invalidate();if(!layout)drawEmpty();});
  function setNarration(url,name,seconds){
    pause();narration.src=url;narration.hidden=false;el('audio-name').textContent=name;el('audio-duration').textContent=clock(seconds);
  }
  async function chooseSource(id,carry=true){
    const seq=++sourceSequence;pause();pendingConfig=true;updateLabels();
    if(!id){selection.job_id=null;selection.audio_id=null;sourceText='';narration.removeAttribute('src');narration.hidden=true;el('audio-name').textContent='尚未选择朗诵音频';el('audio-duration').textContent='—';invalidate();return;}
    const job=await api('/api/jobs/'+id);if(seq!==sourceSequence)return;
    selection.job_id=id;selection.audio_id=null;sourceText=job.request.text;
    if(carry){el('title').value=job.title;el('text').value=sourceText;position=0;}
    el('source').value=id;setNarration(`/api/jobs/${id}/files/narration.mp3`,job.title,job.duration);invalidate();
  }
  function setImage(id){
    selection.image_id=id;const asset=library.assets.find(a=>a.id===id);
    el('image-thumb').hidden=!asset;
    if(asset){el('image-thumb').src=`/api/video/assets/${id}/file`;el('image-name').textContent=asset.name;}
    else el('image-name').textContent='选择你的背景图片';
    el('image-saved').value=id||'';
  }
  function setMusic(id){
    music.pause();selection.bgm_id=id||null;
    if(id){music.src=`/api/video/assets/${id}/file`;music.volume=Number(el('music-volume').value);}
    else music.removeAttribute('src');
    el('music-saved').value=id||'';updateLabels();
  }
  function renderLibrary(){
    const audioAsset=library.assets.find(a=>a.id===selection.audio_id);
    const options=[new Option('选择一份已完成的作品',''),...library.sources.map(j=>new Option(`${j.title} · ${clock(j.duration)}`,j.id)),...library.assets.filter(a=>a.kind==='narration').map(a=>new Option(`已上传：${a.name} · ${clock(a.duration)}`,'audio:'+a.id))];
    el('source').replaceChildren(...options);el('source').value=selection.job_id||(audioAsset?'audio:'+audioAsset.id:'');
    const images=library.assets.filter(a=>a.kind==='image');
    el('image-saved').replaceChildren(new Option('选择已保存的图片',''),...images.map(a=>new Option(a.name,a.id)));el('image-saved').hidden=images.length<2;
    el('music-saved').replaceChildren(new Option('不使用背景音乐',''),...library.assets.filter(a=>a.kind==='music').map(a=>new Option(a.name,a.id)));
    setImage(selection.image_id);setMusic(selection.bgm_id);
  }
  el('source').onchange=guarded(()=>{const value=el('source').value;if(value.startsWith('audio:')){const a=library.assets.find(a=>a.id===value.slice(6));selection.audio_id=a.id;selection.job_id=null;sourceText='';sourceSequence++;setNarration(`/api/video/assets/${a.id}/file`,a.name,a.duration);invalidate();return;}return chooseSource(value);});
  el('image-saved').onchange=()=>{setImage(el('image-saved').value||null);invalidate();};
  el('music-saved').onchange=()=>{setMusic(el('music-saved').value);invalidate();};
  async function upload(file,kind){
    if(!file)return;
    pause();uploading++;updateLabels();el('draft-status').textContent='正在保存素材…';
    const data=new FormData();data.append('file',file);
    try {
      const asset=await api('/api/video/assets/'+kind,{method:'POST',body:data});library.assets.unshift(asset);
      if(kind==='image')selection.image_id=asset.id;
      if(kind==='music')selection.bgm_id=asset.id;
      if(kind==='narration'){selection.audio_id=asset.id;selection.job_id=null;sourceText='';sourceSequence++;setNarration(`/api/video/assets/${asset.id}/file`,asset.name,asset.duration);}
      renderLibrary();invalidate();toast('素材已保存，可在后续视频中复用。');
    } finally {uploading--;updateLabels();save();}
  }
  for(const [id,kind] of [['image-file','image'],['audio-file','narration'],['music-file','music']])el(id).onchange=guarded(async()=>{const file=el(id).files[0];try{await upload(file,kind);}finally{el(id).value='';}});
  el('import-text').onclick=()=>el('text-file').click();
  el('text-file').onchange=guarded(async()=>{const file=el('text-file').files[0];if(!file)return;if(file.size>1024*1024)throw new Error('文稿不能超过 1 MB。');const text=await file.text();if(text.length>50000)throw new Error('正文不能超过 50,000 字。');el('text').value=text;el('text-file').value='';invalidate();});
  function showExport(job){currentExport=job.id;renderExport(job);}
  function renderExport(job){
    const running=activeStates.has(job.status);
    el('export-status').hidden=false;el('export-message').textContent=job.message;
    el('cancel').hidden=!running;el('cancel').disabled=job.status==='cancelling';
    el('export-progress').value=job.progress;el('export-percent').textContent=statusLabels[job.status]+(running?` · ${Math.floor(job.progress*100)}%`:'');
    el('export-time').textContent=job.elapsed?`已用 ${clock(job.elapsed)}`:'';
  }
  function renderHistory(){
    const signature=JSON.stringify(jobs.map(j=>[j.id,j.status,j.duration]));if(signature===historySignature)return;historySignature=signature;
    el('history').hidden=!jobs.length;
    const rows=jobs.map(job=>{
      const row=document.createElement('div');row.className='video-history-row';
      const copy=document.createElement('div');copy.className='video-history-copy';
      const title=document.createElement('strong');title.textContent=job.title;
      const small=document.createElement('small');small.textContent=`${job.request.aspect==='portrait'?'竖屏':'横屏'} · ${job.duration?clock(job.duration)+' · ':''}${statusLabels[job.status]} · ${new Date(job.created_at*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}`;
      copy.append(title,small);const actions=document.createElement('div');actions.className='video-history-actions';
      const button=(text,fn)=>{const b=document.createElement('button');b.className='text-button';b.textContent=text;b.onclick=guarded(fn);actions.append(b);};
      if(job.status==='completed'){
        button('播放',()=>{pause();el('result-player').src=`/api/video/exports/${job.id}/video.mp4`;el('result-player').hidden=false;canvas.hidden=true;el('return-preview').hidden=false;el('result-player').scrollIntoView({block:'center',behavior:'smooth'});});
        const a=document.createElement('a');a.href=`/api/video/exports/${job.id}/video.mp4?download=true`;a.textContent='下载 MP4 ↓';actions.append(a);
      } else if(activeStates.has(job.status))button('查看进度',()=>showExport(job));
      else button('重新导出',async()=>{const next=await api(`/api/video/exports/${job.id}/retry`,{method:'POST'});showExport(next);await poll();});
      button('载入设置',()=>loadProject(job));row.append(copy,actions);return row;
    });
    el('history-list').replaceChildren(...rows);
  }
  async function loadProject(job){
    pause();const req=job.request;
    for(const key of ['image_id','audio_id','job_id','bgm_id','aspect'])selection[key]=req[key];
    for(const [key,id] of Object.entries(fields))el(id).value=req[key];
    renderLibrary();
    if(req.job_id)await chooseSource(req.job_id,false);
    else {const a=library.assets.find(a=>a.id===req.audio_id);if(a)setNarration(`/api/video/assets/${a.id}/file`,a.name,a.duration);sourceText='';}
    position=0;returnToPreview();invalidate();el('title').focus();toast('已载入这份视频的素材与设置。');
  }
  function returnToPreview(){el('result-player').pause();el('result-player').hidden=true;canvas.hidden=false;el('return-preview').hidden=true;draw();}
  el('return-preview').onclick=returnToPreview;
  el('export').onclick=guarded(async()=>{
    if(!layout || pendingConfig)return;
    pause();loading=true;updateLabels();save();
    try{const job=await api('/api/video/exports',{method:'POST',body:JSON.stringify(request())});showExport(job);await poll();}
    finally{loading=false;updateLabels();}
  });
  el('cancel').onclick=guarded(async()=>{if(currentExport)showExport(await api(`/api/video/exports/${currentExport}/cancel`,{method:'POST'}));});
  async function poll(){
    if(pollBusy)return;pollBusy=true;clearTimeout(pollTimer);
    try {jobs=await api('/api/video/exports');const current=jobs.find(j=>j.id===currentExport)||jobs.find(j=>activeStates.has(j.status));if(current){currentExport=current.id;renderExport(current);}renderHistory();updateLabels();}
    catch(error){el('preview-note').textContent=error.message;}
    finally{pollBusy=false;pollTimer=setTimeout(poll,jobs.some(j=>activeStates.has(j.status))?1200:6000);}
  }
  async function init(){
    library=await api('/api/video/library');
    if(!library.assets.some(a=>a.id===selection.image_id))selection.image_id=library.assets.find(a=>a.kind==='image')?.id||null;
    if(!library.assets.some(a=>a.id===selection.bgm_id))selection.bgm_id=null;
    if(!library.sources.some(j=>j.id===selection.job_id))selection.job_id=null;
    if(!library.assets.some(a=>a.id===selection.audio_id))selection.audio_id=null;
    renderLibrary();
    if(selection.job_id)await chooseSource(selection.job_id,false);
    else if(selection.audio_id){const a=library.assets.find(a=>a.id===selection.audio_id);setNarration(`/api/video/assets/${a.id}/file`,a.name,a.duration);}
    loaded=true;updateLabels();await buildPreview();await poll();
  }
  let initPromise;
  async function switchTab(name){
    const video=name==='video';document.getElementById('studio-panel').hidden=video;document.getElementById('video-panel').hidden=!video;
    for(const tab of ['studio','video']){const b=document.getElementById('tab-'+tab);b.setAttribute('aria-selected',String(tab===name));b.tabIndex=tab===name?0:-1;}
    document.querySelector('.skip-link').href=video?'#video-text':'#script';document.querySelector('.skip-link').textContent=video?'跳到视频文稿':'跳到文稿编辑';document.getElementById('guide-button').hidden=video;
    history.replaceState(null,'',video?'#video':'#studio');
    if(video){
      document.querySelectorAll('#studio-panel audio').forEach(a=>a.pause());
      if(!loaded){if(!initPromise)initPromise=init().catch(error=>{initPromise=null;throw error;});await initPromise;}
      else {library=await api('/api/video/library');renderLibrary();}
    }else {pause();el('result-player').pause();}
  }
  for(const name of ['studio','video']){
    const button=document.getElementById('tab-'+name);button.onclick=guarded(()=>switchTab(name));
    button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const other=event.key==='Home'?'studio':event.key==='End'?'video':name==='studio'?'video':'studio';document.getElementById('tab-'+other).focus();guarded(()=>switchTab(other))();}};
  }
  window.addEventListener('studio:make-video',guarded(async event=>{await switchTab('video');await chooseSource(event.detail.id);returnToPreview();}));
  window.addEventListener('pagehide',()=>{save();pause();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  updateLabels();drawEmpty();if(location.hash==='#video')guarded(()=>switchTab('video'))();
})();

const $ = id => document.getElementById(id);
const state = { voices: [], voiceId: '', mode: 'short', file: null, jobId: null, jobs: [], status: null, busy: false, saving: false, restarting:false, recorder: null, recordingPending: false, recordingStream: null, recordingTimer: null, uploadUrl: null, planId:null, planSignature:null, renderedPlan:null, planSegments:null, overrides:{} };
const labels = { queued: '等待中', planning:'分析语气', planned:'分析完成', loading: '准备模型', generating: '正在生成', exporting: '正在导出', completed: '已完成', failed: '生成失败', cancelled: '已停止', interrupted: '已中断', cancelling: '正在停止' };
const active = new Set(['queued','planning','loading','generating','exporting','cancelling']);
const emotions = {reference:'参考语气',calm:'平静',happy:'高兴',angry:'愤怒',sad:'悲伤',afraid:'恐惧',disgusted:'反感',melancholic:'低落',surprised:'惊讶'};
const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const duration = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2,'0')}`;
const setText = (id, text) => { if ($(id).textContent !== text) $(id).textContent = text; };
const scrollTo = id => $(id).scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
if (matchMedia('(max-width: 799px)').matches) $('delivery-settings').open = false;
$('import-text-button').onclick = () => $('text-file').click();
function updateSettingSummaries() {
  const mode = $('emotion-mode').value;
  const delivery = state.status?.supports_emotion===false ? '自然语气' : mode === 'auto' ? '自动语气' : mode === 'manual' ? emotions[$('emotion').value] : '参考语气';
  $('delivery-summary').textContent = `${delivery} · ${Number($('speed').value).toFixed(2)}×`;
  $('pacing-summary').textContent = `${$('gap').selectedOptions[0]?.textContent.split(' · ')[0] || ''} · ${$('language').selectedOptions[0]?.textContent || ''}`;
}
let toastTimer;
function toast(message, error = false) { $('toast-text').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, error ? 14000 : 6500); }
$('toast-close').onclick = () => $('toast').hidden = true;
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, { ...options, headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers } }); }
  catch { throw new Error('无法连接本地服务，请检查「启动录音棚.command」的终端窗口是否仍在运行。'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    let message = body.detail;
    if (Array.isArray(message)) message = path.startsWith('/api/video/') ? '请检查视频设置：正文为 1–50,000 字，片头为 0–15 秒，收尾为 0–10 秒。' : '请检查输入：文稿最多 50,000 字，分段长度为 24–160 字，随机种子需为有效整数。';
    throw new Error(message || `操作没有完成（${response.status}），请稍后重试。`);
  }
  return body;
}
const guarded = fn => async (...args) => { try { await fn(...args); } catch (error) { toast(error.message, true); } };
$('unload-engine').onclick=guarded(async()=>{await api('/api/engine/unload',{method:'POST'});$('unload-engine').hidden=true;toast('模型内存已释放，下次使用时会自动加载。');});
function selectedModel() { return state.status?.models?.find(model => model.id === $('model-select').value); }
function renderRuntime(status, jobs=state.jobs) {
  const running=jobs.some(job=>active.has(job.status));
  const modelState={ready:'已就绪',loading:'加载中',analyzing:'分析语气中',analysis_ready:'分析就绪',not_loaded:'待加载',error:'运行异常'}[status.model_state] || '待加载';
  $('model-status').textContent=`${status.model} · ${modelState}${status.device?' · '+status.device:''}`;
  $('runtime-summary').textContent=status.model;
  const previousChoice=$('model-select').value;
  $('model-select').replaceChildren(...status.models.map(model=>{const option=new Option(model.name,model.id);option.disabled=!model.model_downloaded||!model.runtime_ready;return option;}));
  $('model-select').value=status.models.some(model=>model.id===previousChoice)&&previousChoice!==status.engine?previousChoice:status.engine;
  $('model-select').disabled=running||state.restarting;
  $('switch-model').disabled=running||state.restarting||$('model-select').value===status.engine;
  $('restart-service').disabled=running||state.restarting||status.video_active;
  $('unload-engine').hidden=!['ready','analysis_ready'].includes(status.model_state);
  $('unload-engine').disabled=running||state.restarting;
  const model=status.models.find(item=>item.id===$('model-select').value);
  $('model-description').textContent=model?.supports_emotion?'支持自动分析语气、逐段情绪控制和原生语速。':'速度更快，适合自然朗读；不提供自动语气和逐段情绪控制。';
}
$('model-select').onchange=()=>{const model=selectedModel();$('model-description').textContent=model?.supports_emotion?'支持自动分析语气、逐段情绪控制和原生语速。':'速度更快，适合自然朗读；不提供自动语气和逐段情绪控制。';$('switch-model').disabled=!state.status||$('model-select').value===state.status.engine||state.jobs.some(job=>active.has(job.status));};
$('switch-model').onclick=guarded(async()=>{
  const identifier=$('model-select').value;
  if(identifier===state.status?.engine)return;
  $('switch-model').disabled=true;$('switch-model').textContent='正在切换…';
  try {state.status=await api('/api/engine/select',{method:'POST',body:JSON.stringify({engine:identifier})});invalidatePlan();renderRuntime(state.status);updateEmotionFields();updateButton();toast(`已切换到 ${state.status.model}，之后的新任务会使用这个模型。`);}
  finally {$('switch-model').textContent='切换模型';if(state.status)renderRuntime(state.status);}
});
$('restart-service').onclick=guarded(async()=>{
  if(!window.confirm('确定重启整个录音棚服务吗？文稿草稿和已有作品都会保留。'))return;
  draft();state.restarting=true;renderRuntime(state.status);$('restart-screen').hidden=false;
  const previous=state.status.service_instance;
  try {
    await api('/api/service/restart',{method:'POST'});
    const deadline=Date.now()+45000;
    while(Date.now()<deadline){
      await new Promise(resolve=>setTimeout(resolve,650));
      try {const response=await fetch(`/api/status?restart=${Date.now()}`,{cache:'no-store'});if(response.ok){const status=await response.json();if(status.service_instance!==previous){location.reload();return;}}} catch { /* Expected while the local process is restarting. */ }
    }
    throw new Error('服务尚未恢复。请双击「启动录音棚.command」，文稿草稿仍保存在本机。');
  } catch(error){state.restarting=false;$('restart-screen').hidden=true;if(state.status)renderRuntime(state.status);throw error;}
});
function updateButton() {
  const text = $('script').value.trim();
  $('char-count').textContent = $('script').value.length.toLocaleString();
  const ready = state.status?.model_downloaded && state.status?.runtime_ready;
  $('generate-button').disabled = !state.voiceId || !text || !ready || state.busy;
  const emotionCapable=state.status?.supports_emotion!==false;
  $('analyze-button').disabled = !text || !ready || state.busy || !emotionCapable;
  $('analyze-button').title=emotionCapable?'':'Qwen 不支持语气分析，请切换到 IndexTTS-2.5。';
  $('preview-button').disabled = !text;
  $('preview-button').textContent = state.planId ? '语气安排' : '预览分段';
  $('generate-hint').textContent = !state.voiceId ? '先保存声音，再开始配音。' : !state.status ? '正在连接本地服务…' : !state.status.model_downloaded ? '当前模型尚未准备好。' : !state.status.runtime_ready ? '运行环境准备完成后即可生成。' : !text ? '写入文稿，或导入 TXT / Markdown。' : !emotionCapable ? 'Qwen 将按参考录音的自然语气生成。' : state.mode === 'video' ? '按短段落生成，方便对齐画面。' : state.mode === 'long' ? '长文自动分段，中断后可继续。' : '准备好了。也可以先分析语气。';
  updateSettingSummaries();
}
function draft() {
  try { localStorage.setItem('bensheng-draft', JSON.stringify({ text: $('script').value, title: $('project-title').value, mode: state.mode, speed: $('speed').value, gap: $('gap').value, language: $('language').value, segmentLength: $('segment-length').value, seed: $('seed').value, voiceId: state.voiceId, emotionMode:$('emotion-mode').value,emotion:$('emotion').value,emotionStrength:$('emotion-strength').value,direction:$('direction').value,contextual:$('contextual').checked })); setText('draft-status', '草稿已保存'); } catch { setText('draft-status', '草稿未能保存'); }
}
function invalidatePlan() { state.planId=null; state.planSignature=null; state.renderedPlan=null; state.planSegments=null; state.overrides={}; $('segment-preview').hidden=true; $('preview-button').textContent='预览分段'; }
function updateEmotionFields() {
  const mode=$('emotion-mode').value;
  const capable=state.status?.supports_emotion!==false;
  $('emotion-mode').disabled=!capable;
  $('emotion-choice').hidden=!capable||mode!=='manual'; $('auto-emotion-options').hidden=!capable||mode!=='auto';
  $('emotion-strength-field').hidden=!capable||mode==='reference'; $('reference-emotion-note').hidden=capable?mode!=='reference':false;
  $('reference-emotion-note').textContent=capable?'沿用参考录音的表达习惯，适合自然平稳的叙述。':'当前 Qwen 模型不支持逐段情绪控制，将按参考录音自然朗读。';
  $('emotion-strength-value').textContent=Math.round(Number($('emotion-strength').value)*100)+'%';
  updateSettingSummaries();
}
function setMode(mode, reset = true) {
  state.mode = mode;
  document.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
  if (reset) { $('segment-length').value = { short: 80, long: 80, video: 42 }[mode]; $('gap').value = mode === 'video' ? '0.15' : '0.3'; }
  invalidatePlan(); updateButton(); draft();
}
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => setMode(b.dataset.mode));
for (const id of ['script','project-title','speed','gap','language','segment-length','seed']) {
  $(id).addEventListener('input', () => { $('speed-value').textContent = Number($('speed').value).toFixed(2) + '×'; updateButton(); draft(); if (id === 'script' || id === 'segment-length') invalidatePlan(); });
}
for(const id of ['emotion-mode','emotion','emotion-strength','direction','contextual']) $(id).addEventListener('input',()=>{updateEmotionFields();invalidatePlan();draft();});
const saved = (() => { try { return JSON.parse(localStorage.getItem('bensheng-draft')) || {}; } catch { return {}; } })();
$('script').value = saved.text || ''; $('project-title').value = saved.title || '';
for (const [key,id] of [['speed','speed'],['gap','gap'],['language','language'],['segmentLength','segment-length'],['seed','seed']]) if (saved[key] !== undefined) $(id).value = saved[key];
for(const [key,id] of [['emotionMode','emotion-mode'],['emotion','emotion'],['emotionStrength','emotion-strength'],['direction','direction']]) if(saved[key]!==undefined) $(id).value=saved[key];
if(saved.contextual!==undefined)$('contextual').checked=saved.contextual;
updateEmotionFields();
state.voiceId = saved.voiceId || ''; setMode(['short','long','video'].includes(saved.mode) ? saved.mode : 'short', false); $('speed-value').textContent = Number($('speed').value).toFixed(2) + '×';
function renderVoices() {
  const hasVoice = state.voices.length > 0;
  $('voice-empty').hidden = hasVoice; $('voice-selected').hidden = !hasVoice; $('add-voice').hidden = !hasVoice;
  $('voice-form').hidden = hasVoice;
  $('add-voice').textContent = '添加声音';
  $('add-voice').setAttribute('aria-expanded', 'false');
  if (!state.voices.some(v => v.id === state.voiceId)) state.voiceId = state.voices[0]?.id || '';
  $('voice-select').replaceChildren(...state.voices.map(v => new Option(v.name, v.id)));
  $('voice-select').value = state.voiceId; selectVoice();
}
function selectVoice() {
  const voice = state.voices.find(v => v.id === state.voiceId);
  if (voice) {
    $('reference-player').src = `/api/voices/${voice.id}/audio`;
    $('voice-duration').textContent = `${voice.duration.toFixed(1)} 秒`;
    $('saved-transcript').textContent = voice.transcript;
    $('voice-warnings').textContent = voice.warnings.join(' ');
  }
  updateButton(); draft();
}
$('voice-select').onchange = () => { state.voiceId = $('voice-select').value; selectVoice(); };
function showVoiceForm(show, focus = false) {
  if (!show && (state.recordingPending || state.recorder?.state === 'recording' || state.saving)) return;
  $('voice-form').hidden = !show;
  $('add-voice').textContent = show ? '收起' : '添加声音';
  $('add-voice').setAttribute('aria-expanded', String(show));
  if (show && focus) $('voice-file').focus();
}
$('add-voice').onclick = () => showVoiceForm($('voice-form').hidden, true);
function setUpload(file) {
  if (!file) return;
  if (file.size > 40 * 1024 * 1024) throw new Error('录音文件超过 40 MB，请选择较短的片段。');
  state.file = file; $('upload-label').textContent = file.name;
  if (state.uploadUrl) URL.revokeObjectURL(state.uploadUrl);
  state.uploadUrl = URL.createObjectURL(file); $('upload-player').src = state.uploadUrl; $('upload-player').hidden = false;
}
$('voice-file').onchange = guarded(() => setUpload($('voice-file').files[0]));
$('voice-form').onsubmit = guarded(async event => {
  event.preventDefault();
  if (!state.file) throw new Error('先选择一段录音，或点击「直接录一段」。');
  if (state.saving || state.recordingPending || state.recorder?.state === 'recording') return;
  state.saving = true; $('save-voice').disabled = true; $('add-voice').disabled = true; $('record-button').disabled = true; $('save-voice').textContent = '正在保存声音…';
  try {
    const data = new FormData(); data.append('name', $('voice-name').value); data.append('transcript', $('reference-text').value); data.append('audio', state.file);
    const voice = await api('/api/voices', { method: 'POST', body: data });
    state.voices.unshift(voice); state.voiceId = voice.id; renderVoices(); toast('声音已保存。现在可以写入文稿，试听克隆效果。');
  } finally {
    state.saving = false;
    const recording = state.recordingPending || state.recorder?.state === 'recording';
    $('save-voice').disabled = recording;
    $('add-voice').disabled = recording;
    $('record-button').disabled = state.recordingPending;
    $('save-voice').textContent = '保存我的声音';
  }
});
$('guide-button').onclick = () => { $('recording-guide').open = true; $('recording-guide').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block:'center' }); };
$('use-reference-script').onclick = () => { $('reference-text').value = $('recording-guide').querySelector('blockquote').textContent; showVoiceForm(true); $('reference-text').focus(); toast('已填入录音备注。'); };
$('record-button').onclick = guarded(async () => {
  if (state.saving || state.recordingPending) return;
  if (state.recorder?.state === 'recording') { state.recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('这个浏览器不支持录音，请用系统「语音备忘录」录制后选择文件。');
  state.recordingPending = true;
  $('record-button').disabled = true;
  $('add-voice').disabled = true;
  $('save-voice').disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation:true, noiseSuppression:false, autoGainControl:true }, video:false });
    state.recordingStream = stream;
    const mimeType = ['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t => MediaRecorder.isTypeSupported(t));
    const recorder = new MediaRecorder(stream, mimeType ? {mimeType} : {}), pieces = []; state.recorder = recorder;
    const started = Date.now();
    function cleanup() { clearInterval(state.recordingTimer); stream.getTracks().forEach(t => t.stop()); state.recordingStream = null; $('record-label').textContent = '或直接录一段'; $('record-button').classList.remove('recording'); $('save-voice').disabled = false; $('add-voice').disabled = false; }
    recorder.ondataavailable = e => { if (e.data.size) pieces.push(e.data); };
    recorder.onstop = () => { cleanup(); const type = recorder.mimeType; try { setUpload(new File(pieces, `我的参考录音.${type.includes('mp4') ? 'm4a' : 'webm'}`, {type})); } catch(error) { toast(error.message,true); } };
    recorder.onerror = () => { cleanup(); toast('录音中断，请重新录制或选择文件。', true); };
    recorder.start(500); $('save-voice').disabled = true; $('add-voice').disabled = true; $('record-button').classList.add('recording'); $('record-label').textContent = '停止录音 · 0:00';
    state.recordingTimer = setInterval(() => { const seconds = (Date.now()-started)/1000; $('record-label').textContent = `停止录音 · ${duration(seconds)}`; if (seconds >= 59 && recorder.state === 'recording') recorder.stop(); }, 250);
  } catch (error) {
    state.recordingStream?.getTracks().forEach(t => t.stop());
    throw new Error(error.name === 'NotAllowedError' ? '未获得麦克风权限。可以在浏览器里允许录音，或直接选择已有录音文件。' : '麦克风暂时不可用，请检查设备或选择已有录音。');
  } finally {
    state.recordingPending = false;
    $('record-button').disabled = false;
    const recording = state.recorder?.state === 'recording';
    $('add-voice').disabled = recording;
    $('save-voice').disabled = recording || state.saving;
  }
});
window.addEventListener('pagehide', () => { state.recordingStream?.getTracks().forEach(t => t.stop()); clearInterval(state.recordingTimer); });
$('text-file').onchange = guarded(async () => {
  const file = $('text-file').files[0]; if (!file) return;
  if (file.size > 1024 * 1024) throw new Error('文稿过大，请先按章节拆分为 50,000 字以内。');
  const text = await file.text(); if (text.length > 50000) throw new Error('文稿超过 50,000 字，请按章节拆分。');
  if (text.includes('\uFFFD')) throw new Error('文稿编码无法正确识别，请保存为 UTF-8 后重新导入。');
  if ($('script').value.trim() && !window.confirm('导入会替换编辑区中的当前文稿，是否继续？')) return;
  $('script').value = text; $('project-title').value = file.name.replace(/\.[^.]+$/, ''); invalidatePlan(); updateButton(); draft(); $('text-file').value = '';
});
$('example-button').onclick = () => {
  if ($('script').value.trim() && !window.confirm('将用示例替换当前文稿，是否继续？')) return;
  $('project-title').value = '留一点时间，听听自己';
  $('script').value = '有时候，我们需要的，只是慢下来。\n\n把窗户打开，让风走进房间。听一首喜欢的歌，读一段想了很久的文字。那些看似平常的片刻，也可以成为值得记录的故事。\n\n今天，就从自己的声音开始。'; invalidatePlan(); updateButton(); draft();
};
function textRequest() { const limit = Number($('segment-length').value); if (!Number.isInteger(limit) || limit < 24 || limit > 160) throw new Error('每段长度应为 24–160 之间的整数。'); return {text:$('script').value, segment_length:limit}; }
function planRequest() { return {...textRequest(),emotion_mode:state.status?.supports_emotion===false?'reference':$('emotion-mode').value,emotion:$('emotion').value,emotion_strength:Number($('emotion-strength').value),direction:$('direction').value,contextual:$('contextual').checked}; }
function signature(request) { const r=request;return JSON.stringify({text:r.text,segment_length:r.segment_length,emotion_mode:r.emotion_mode,emotion:r.emotion,emotion_strength:r.emotion_strength,direction:r.direction,contextual:r.contextual}); }
function renderPlan(segments, editable=false) {
  $('preview-title').textContent = `${editable?'语气安排':'分段预览'} · 共 ${segments.length} 段`;
  $('plan-note').textContent = editable?'每段可单独修改情绪和强度；修改后点击「生成配音」。':'点击「分析语气」可查看模型判断，再逐段调整。';
  $('preview-list').innerHTML=segments.map((s,i)=>{const d=state.overrides[i]||s.delivery; return `<li><div class="segment-text">${escapeHtml(s.text)}</div>${d?`<div class="segment-controls"><select aria-label="第 ${i+1} 段情绪" data-emotion-index="${i}" ${editable?'':'disabled'}>${Object.entries(emotions).map(([k,v])=>`<option value="${k}" ${d.emotion===k?'selected':''}>${v}</option>`).join('')}</select><label>强度 <input type="range" aria-label="第 ${i+1} 段强度" min="0" max="1" step="0.05" value="${d.strength}" data-strength-index="${i}" ${editable?'':'disabled'}><output>${Math.round(d.strength*100)}%</output></label><span class="segment-source">${d.source==='auto'?'模型判断':d.source==='override'?'已调整':'指定语气'}</span></div>`:''}</li>`;}).join('');
  $('segment-preview').hidden=false;
}
$('preview-list').oninput=event=>{
  const el=event.target, raw=el.dataset.emotionIndex??el.dataset.strengthIndex;
  if(raw===undefined || !state.planId)return;
  const i=Number(raw), row=el.closest('li'), emotion=row.querySelector('select').value, strength=Number(row.querySelector('input').value);
  row.querySelector('output').textContent=Math.round(strength*100)+'%'; row.querySelector('.segment-source').textContent='已调整';
  state.overrides[i]={index:i,emotion,strength,source:'override'};
};
$('preview-button').onclick = guarded(async () => {
  if (state.planId && state.planSegments) {
    // Keep the existing controls and overrides when returning to an analyzed plan.
    $('segment-preview').hidden = false;
  } else {
    $('preview-button').disabled = true;
    try { const result = await api('/api/preview', {method:'POST',body:JSON.stringify(textRequest())}); renderPlan(result.segments); }
    finally { updateButton(); }
  }
  scrollTo('segment-preview');
});
$('analyze-button').onclick=guarded(async()=>{
  state.busy=true; updateButton(); $('analyze-button').textContent='正在提交…';
  try { const request=planRequest(); invalidatePlan(); state.planSignature=signature(request); const job=await api('/api/plans',{method:'POST',body:JSON.stringify(request)});state.jobId=job.id;state.jobs.unshift(job);renderJob(job);renderHistory();scrollTo('output-heading');toast('开始分析语气，完成后可逐段调整。'); }
  finally {state.busy=false;$('analyze-button').textContent='分析语气';updateButton();}
});
$('close-preview').onclick = () => $('segment-preview').hidden = true;
$('generate-button').onclick = guarded(async () => {
  if (state.busy) return; state.busy = true; updateButton();
  try {
    const seed = Number($('seed').value); if (!Number.isInteger(seed) || seed < 0 || seed > 2147480000) throw new Error('随机种子应为 0–2147480000 之间的整数。');
    const request = {...planRequest(), title:$('project-title').value, voice_id:state.voiceId, speed:Number($('speed').value), gap:Number($('gap').value), language:$('language').value, seed, mode:state.mode};
    if(state.planId && state.planSignature===signature(request)) {request.plan_id=state.planId;request.segment_overrides=Object.values(state.overrides).map(({index,emotion,strength})=>({index,emotion,strength}));}
    const job = await api('/api/jobs', {method:'POST',body:JSON.stringify(request)}); state.jobId = job.id; state.jobs.unshift(job); renderJob(job); renderHistory(); scrollTo('output-heading');
  } finally { state.busy = false; updateButton(); }
});
$('script').addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); if (!$('generate-button').disabled) $('generate-button').click(); } });
function renderJob(job) {
  $('output-empty').hidden = true; $('job-output').hidden = false; setText('job-title', job.title); setText('job-message', job.message);
  $('job-badge').textContent = job.kind==='analysis' && job.status==='failed' ? '分析待重试' : labels[job.status] || job.status; $('job-badge').classList.toggle('error',['failed','interrupted'].includes(job.status));
  const analyzing=job.kind==='analysis'||job.status==='planning';
  $('job-progress').max = job.total; $('job-progress').value = analyzing?(job.analyzed||0):job.completed;
  $('job-count').textContent = analyzing?`${job.analyzed||0} / ${job.total} 段已分析`:`${job.completed} / ${job.total} 段已完成`;
  $('job-time').textContent = job.duration ? `音频 ${duration(job.duration)} · 生成用时 ${duration(job.elapsed)}` : job.elapsed ? `已处理 ${duration(job.elapsed)}` : '首次加载稍需等待';
  $('cancel-job').hidden = !active.has(job.status); $('cancel-job').disabled = job.status === 'cancelling';
  $('resume-job').hidden = !['failed','interrupted','cancelled'].includes(job.status);
  $('resume-job').textContent = job.kind==='analysis' ? (job.analyzed ? '继续分析' : '重新分析') : '继续生成';
  const completed = job.status === 'completed'; $('finished-output').hidden = !completed;
  let planMatches=false;
  try {planMatches=signature(job.request)===signature(planRequest());} catch { /* Editing an incomplete numeric field. */ }
  const validPlan=job.status==='planned' && job.analyzed===job.total && job.segments.length===job.total && job.segments.every(s=>s.delivery);
  if(state.planId===job.id && !validPlan) invalidatePlan();
  if(validPlan && state.renderedPlan!==job.id && planMatches) {
    state.planId=job.id;state.planSignature=signature(job.request);state.renderedPlan=job.id;state.planSegments=job.segments;state.overrides={};renderPlan(job.segments,true);updateButton();
  }
  if (completed && $('output-player').dataset.job !== job.id) {
    $('output-player').src = `/api/jobs/${job.id}/files/narration.mp3`; $('output-player').dataset.job = job.id;
    for (const [type,filename] of [['wav','narration.wav'],['mp3','narration.mp3'],['srt','captions.srt'],['zip','voice-studio.zip']]) $('download-'+type).href = `/api/jobs/${job.id}/files/${filename}`;
  } else if (!completed && $('output-player').dataset.job) { $('output-player').pause(); $('output-player').removeAttribute('src'); delete $('output-player').dataset.job; }
}
function renderHistory() {
  $('history-section').hidden = !state.jobs.length;
  const visibleJobs = state.jobs.slice(0, 12);
  setText('history-count', `最近 ${visibleJobs.length} 项`);
  const list = $('history-list');
  const existing = new Map([...list.children].map(row => [row.dataset.job, row]));
  visibleJobs.forEach((job, index) => {
    let row = existing.get(job.id);
    if (!row) {
      row = document.createElement('button');
      row.className = 'history-item'; row.dataset.job = job.id;
      row.innerHTML = '<div><strong></strong><small></small></div><span></span>';
    }
    const model=job.engine==='index-tts-2.5'?'IndexTTS-2.5':(!job.engine||job.engine==='qwen3-tts')?'旧 Qwen':'模型待确认';
    const status=job.kind==='analysis' && job.status==='failed'?'分析待重试':labels[job.status] || job.status;
    const values = [job.title, `${job.voice.name} · ${model} · ${new Date(job.created_at).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})} · ${job.total} 段`, `${status} ↗`];
    ['strong','small','span'].forEach((selector, i) => {
      const node = row.querySelector(selector);
      if (node.textContent !== values[i]) node.textContent = values[i];
    });
    row.setAttribute('aria-current', String(job.id === state.jobId));
    if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    existing.delete(job.id);
  });
  existing.forEach(row => row.remove());
}
$('history-list').onclick = event => { const id = event.target.closest('[data-job]')?.dataset.job; if (!id) return; state.jobId = id; renderJob(state.jobs.find(j => j.id === id)); renderHistory(); scrollTo('output-heading'); };
$('cancel-job').onclick = guarded(async () => renderJob(await api(`/api/jobs/${state.jobId}/cancel`,{method:'POST'})));
$('resume-job').onclick = guarded(async () => renderJob(await api(`/api/jobs/${state.jobId}/resume`,{method:'POST'})));
async function refresh() {
  try {
    const [status,jobs] = await Promise.all([api('/api/status'),api('/api/jobs')]);
    state.status = status; state.jobs = jobs; renderRuntime(status,jobs); updateEmotionFields();
    $('connection-warning').hidden = status.model_downloaded && status.runtime_ready;
    if (!status.model_downloaded) $('connection-warning').textContent = `${status.model} 模型文件尚未完整，请先准备模型。`;
    else if(!status.runtime_ready) $('connection-warning').textContent = '模型已下载，独立运行环境尚未准备好。请运行「安装环境.command」。';
    if (!state.jobId && jobs.length) state.jobId = jobs[0].id;
    const job = jobs.find(j => j.id === state.jobId); if (job) renderJob(job); renderHistory(); updateButton();
  } catch(error) { $('connection-warning').hidden = false; $('connection-warning').textContent = error.message; state.status = null; updateButton(); }
  finally { setTimeout(refresh, state.jobs.some(j => active.has(j.status)) ? 1500 : 7000); }
}
async function init() { try { state.voices = await api('/api/voices'); renderVoices(); } catch(error) { toast(error.message,true); } await refresh(); }
init();

$('make-video').onclick = () => { const job = state.jobs.find(j => j.id === state.jobId); if (job?.status === 'completed') window.dispatchEvent(new CustomEvent('studio:make-video', {detail: job})); };

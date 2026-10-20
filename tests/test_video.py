import io
import json
import re
import subprocess
import threading
import time

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from PIL import Image, ImageFont

from studio.server import create_app
from studio.video import FONT, VideoManager, VideoRequest, make_layout, wrap_text, write_ass


class IdleEngine:
    engine_id = 'index-tts-2.5'
    state = 'not_loaded'
    error = None


def audio_bytes(seconds=2.4, frequency=220):
    buf = io.BytesIO()
    samples = .22 * np.sin(2*np.pi*frequency*np.arange(round(seconds*24000))/24000)
    sf.write(buf, samples, 24000, format='WAV')
    return buf.getvalue()


def prepare(client):
    buf = io.BytesIO()
    Image.new('RGB', (600, 400), '#344d40').save(buf, format='PNG')
    image = client.post('/api/video/assets/image', files={'file': ('背景.png', buf.getvalue(), 'image/png')})
    assert image.status_code == 201, image.text
    audio = client.post('/api/video/assets/narration', files={'file': ('朗诵.wav', audio_bytes(), 'audio/wav')})
    assert audio.status_code == 201, audio.text
    return {'image_id': image.json()['id'], 'audio_id': audio.json()['id'], 'title': '清晨的一封信', 'author': '公明',
            'text': '清晨的光，轻轻落在窗边。\n我们慢慢读过一段文字，听见生活温柔的回声。\n愿每一个平凡的日子，都有新的发现。',
            'intro': .8, 'outro': .6, 'aspect': 'landscape'}


def wait_video(client, identifier):
    for _ in range(400):
        job = client.get('/api/video/exports/' + identifier).json()
        if job['status'] not in {'queued','preparing','exporting','cancelling'}:
            return job
        time.sleep(.05)
    pytest.fail('Short video export did not finish')


def test_long_article_layout_keeps_every_character_and_constant_speed(tmp_path):
    text = ('中文 mixed 2026，标点“都要保留”。\n' * 1700)[:50000]
    font = ImageFont.truetype(str(FONT), 46, index=6)
    lines = wrap_text(text, font, 864)
    compact = lambda s: re.sub(r'\s', '', s)
    assert compact(''.join(line['text'] for line in lines)) == compact(text)
    assert all(font.getlength(line['text']) <= 864 for line in lines)
    request = VideoRequest(image_id='a'*32, audio_id='b'*32, text=text)
    layout = make_layout(request, 600)
    assert layout['end_y'] + layout['lines'][-1]['offset'] == layout['top']
    assert layout['start_y'] - layout['end_y'] == pytest.approx(layout['speed']*600)
    assert max(sum(line['start'] <= t <= line['end'] for line in layout['lines']) for t in range(4,600,20)) < 24
    write_ass(tmp_path / 'motion.ass', layout)
    assert (tmp_path / 'motion.ass').stat().st_size < 2_000_000


def test_material_validation_and_preview(tmp_path):
    app = create_app(tmp_path, engine=IdleEngine())
    with TestClient(app) as client:
        request = prepare(client)
        layout = client.post('/api/video/preview', json=request)
        assert layout.status_code == 200, layout.text
        layout = layout.json()
        assert (layout['width'],layout['height']) == (1920,1080)
        assert layout['duration'] == pytest.approx(layout['audio_duration']+1.4)
        image = client.get(layout['background_url'])
        assert image.status_code == 200
        assert Image.open(io.BytesIO(image.content)).size == (1920,1080)
        portrait = client.post('/api/video/preview',json={**request,'aspect':'portrait'}).json()
        assert (portrait['width'],portrait['height']) == (1080,1920)
        assert client.post('/api/video/preview',json={**request,'job_id':'c'*32}).status_code == 422
        assert client.post('/api/video/preview',json={**request,'shade':float('1.2')}).status_code == 422
        assert client.post('/api/video/preview',json={**request,'image_id':request['audio_id']}).status_code == 400
        assert client.post('/api/video/preview',json={**request,'text':'  '}).status_code == 422
        assert client.post('/api/video/assets/image',files={'file':('bad.png',b'not an image','image/png')}).status_code == 400
        assert client.get('/api/video/assets/invalid/file').status_code == 400
        assert client.post('/api/video/exports',json=request,headers={'Origin':'https://example.com'}).status_code == 403
        assert len(client.get('/api/video/library').json()['assets']) == 2


def test_short_real_export_title_motion_audio_mix_and_download(tmp_path):
    app = create_app(tmp_path, engine=IdleEngine())
    with TestClient(app) as client:
        request = prepare(client)
        bgm = client.post('/api/video/assets/music',files={'file':('music.wav',audio_bytes(.6,440),'audio/wav')}).json()
        request['bgm_id'] = bgm['id']
        created = client.post('/api/video/exports',json=request)
        assert created.status_code == 201, created.text
        identifier = created.json()['id']
        job = wait_video(client,identifier)
        assert job['status'] == 'completed', job
        assert job['progress'] == 1
        folder = tmp_path / 'videos/projects' / identifier
        movie = folder / 'video.mp4'
        assert movie.is_file() and not (folder/'video.pending.mp4').exists()
        response = client.get(f'/api/video/exports/{identifier}/video.mp4?download=true')
        assert response.status_code == 200 and response.headers['content-type'] == 'video/mp4'
        assert 'attachment' in response.headers['content-disposition']
        assert client.get(f'/api/video/exports/{identifier}/video.mp4',headers={'Range':'bytes=0-99'}).status_code == 206
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        decoded = subprocess.run([ffmpeg,'-v','error','-i',str(movie),'-vn','-f','f32le','-ac','1','-ar','24000','pipe:1'],capture_output=True)
        assert decoded.returncode == 0, decoded.stderr
        samples = np.frombuffer(decoded.stdout,dtype=np.float32)
        assert len(samples)/24000 == pytest.approx(job['duration'],abs=.08)
        rms = lambda a,b: np.sqrt(np.mean(samples[int(a*24000):int(b*24000)]**2))
        assert 0 < rms(.3,.6) < rms(1.2,1.6) / 3
        background = np.asarray(Image.open(folder/'background.jpg')).astype(int)
        frames = []
        for index,t in enumerate([.4,1.4,2.2]):
            path = folder/f'frame{index}.png'
            result = subprocess.run([ffmpeg,'-v','error','-ss',str(t),'-i',str(movie),'-frames:v','1','-y',str(path)],capture_output=True)
            assert result.returncode == 0
            frame = np.asarray(Image.open(path)).astype(int)
            assert (frame.mean(axis=2) > background.mean(axis=2)+60).sum() > 100
            frames.append(frame)
        assert np.mean(np.abs(frames[1]-frames[2])) > .2
        assert client.post(f'/api/video/exports/{identifier}/retry').status_code == 409


def test_cancel_blocks_restart_and_recovery_preserves_request(tmp_path, monkeypatch):
    app = create_app(tmp_path,engine=IdleEngine(),restart_callback=lambda: None)
    started = threading.Event()
    def blocked_run(self, identifier):
        started.set()
        self.events[identifier].wait(5)
        self.update(identifier,status='cancelled')
    monkeypatch.setattr(VideoManager,'run',blocked_run)
    with TestClient(app) as client:
        request = prepare(client)
        job = client.post('/api/video/exports',json=request).json()
        assert started.wait(1)
        assert client.get('/api/status').json()['video_active']
        assert client.post('/api/service/restart').status_code == 409
        assert client.get(f"/api/video/exports/{job['id']}/video.mp4").status_code == 404
        client.post(f"/api/video/exports/{job['id']}/cancel")
        assert wait_video(client,job['id'])['status'] == 'cancelled'
        assert not client.get('/api/status').json()['video_active']
    path = tmp_path/'videos/projects'/job['id']/'project.json'
    saved = json.loads(path.read_text());saved['status']='exporting';path.write_text(json.dumps(saved))
    with TestClient(create_app(tmp_path,engine=IdleEngine())) as client:
        recovered = client.get('/api/video/exports/'+job['id']).json()
        assert recovered['status']=='interrupted'
        assert recovered['request']['text']==request['text']


def test_completed_studio_work_can_be_used_without_reupload(tmp_path):
    app = create_app(tmp_path,engine=IdleEngine())
    with TestClient(app) as client:
        request = prepare(client)
        speech = app.state.manager
        identifier = 'd'*32
        (speech.directory/identifier).mkdir()
        (speech.directory/identifier/'narration.wav').write_bytes(audio_bytes(2))
        speech.jobs[identifier]={'id':identifier,'title':'原配音','status':'completed','duration':2,'request':{'text':request['text']}}
        request.pop('audio_id');request['job_id']=identifier
        assert client.get('/api/video/library').json()['sources'][0]['id']==identifier
        assert client.post('/api/video/preview',json=request).json()['audio_duration']==2

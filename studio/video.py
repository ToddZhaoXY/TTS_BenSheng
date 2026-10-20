"""Local still-image films: one layout, linear motion, native FFmpeg export."""
import copy
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import imageio_ffmpeg
from PIL import Image, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from .jobs import save_json

FONT = Path('/System/Library/Fonts/Supplemental/Songti.ttc')
ACTIVE = {'queued', 'preparing', 'exporting', 'cancelling'}
IDENTIFIER = r'^[a-f0-9]{32}$'


class VideoRequest(BaseModel):
    image_id: str = Field(pattern=IDENTIFIER)
    job_id: str | None = Field(default=None, pattern=IDENTIFIER)
    audio_id: str | None = Field(default=None, pattern=IDENTIFIER)
    bgm_id: str | None = Field(default=None, pattern=IDENTIFIER)
    title: str = Field(default='', max_length=100)
    author: str = Field(default='公明', max_length=40)
    text: str = Field(min_length=1, max_length=50000)
    aspect: Literal['portrait', 'landscape'] = 'portrait'
    font_size: int = Field(default=46, ge=32, le=72)
    line_spacing: float = Field(default=1.7, ge=1.2, le=2.4)
    color: Literal['#f7f3e8', '#ffffff', '#26342b'] = '#f7f3e8'
    shade: float = Field(default=.25, ge=0, le=.8)
    crop_x: float = Field(default=.5, ge=0, le=1)
    crop_y: float = Field(default=.5, ge=0, le=1)
    intro: float = Field(default=4, ge=0, le=15)
    outro: float = Field(default=2, ge=0, le=10)
    bgm_volume: float = Field(default=.12, ge=0, le=.5)

    @model_validator(mode='after')
    def valid_source(self):
        if bool(self.job_id) == bool(self.audio_id):
            raise ValueError('请选择一份配音作品，或上传一份朗诵音频。')
        if not self.text.strip():
            raise ValueError('请填写要滚动的正文。')
        return self


def run_media(args, timeout=180):
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                                 '-nostdin', '-y', *map(str, args)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('音频处理超时，请尝试较短的文件。') from exc
    if result.returncode:
        raise ValueError('素材无法读取，请换用可正常播放的图片或音频文件。')
    return result.stdout


def wrap_text(text, font, width):
    """Measure actual glyphs, preserve paragraphs, keep closing punctuation attached."""
    lines = []
    closing = set('，。！？；：、）》」』】…,.!?;:)]}')
    for paragraph in re.split(r'\n+', text.replace('\r\n', '\n').replace('\r', '\n').strip()):
        paragraph = re.sub(r'[\t \u3000]+', ' ', paragraph).strip()
        if not paragraph:
            continue
        current = ''
        for char in paragraph:
            if current and font.getlength(current + char) > width:
                if char in closing and len(current) > 1:
                    carry = current[-1]
                    lines.append({'text': current[:-1], 'paragraph_end': False})
                    current = carry
                else:
                    lines.append({'text': current, 'paragraph_end': False})
                    current = ''
            current += char
        if current:
            lines.append({'text': current.rstrip(), 'paragraph_end': True})
    return lines


def make_layout(request, audio_duration):
    w, h = (1080, 1920) if request.aspect == 'portrait' else (1920, 1080)
    font = ImageFont.truetype(str(FONT), request.font_size, index=6)
    left, top, bottom = round(w * .10), round(h * .17), round(h * .84)
    line_height = round(request.font_size * request.line_spacing)
    lines = wrap_text(request.text, font, w - left * 2)
    offset = 0
    for line in lines:
        line['offset'] = offset
        offset += line_height * (1.5 if line['paragraph_end'] else 1)
    text_height = lines[-1]['offset'] + line_height
    start_y = bottom - line_height
    end_y = top - lines[-1]['offset']
    speed = (start_y - end_y) / audio_duration
    total = request.intro + audio_duration + request.outro
    for line in lines:
        # Only visible lines are active in libass; long articles do not create a giant bitmap.
        enter = request.intro + (start_y + line['offset'] - bottom) / speed
        leave = request.intro + (start_y + line['offset'] - top + line_height) / speed
        line['start'] = max(request.intro, enter)
        line['end'] = total if leave > request.intro + audio_duration else leave
        line['fade_in'] = min(.35, max(0, (line['end'] - line['start']) / 4))
        line['fade_out'] = min(.35, max(0, (line['end'] - line['start']) / 4))
    title_font_size = 76 if w == 1080 else 82
    title_font = ImageFont.truetype(str(FONT), title_font_size, index=6)
    title_lines = [line['text'] for line in wrap_text(request.title or '未命名作品', title_font, w * .8)]
    while len(title_lines) > 4 and title_font_size > 40:
        title_font_size -= 4
        title_font = ImageFont.truetype(str(FONT), title_font_size, index=6)
        title_lines = [line['text'] for line in wrap_text(request.title or '未命名作品', title_font, w * .8)]
    return {'width': w, 'height': h, 'left': left, 'top': top, 'bottom': bottom,
            'font_size': request.font_size, 'line_height': line_height, 'text_height': text_height,
            'start_y': start_y, 'end_y': end_y, 'speed': speed, 'lines': lines,
            'intro': request.intro, 'outro': request.outro, 'audio_duration': audio_duration,
            'duration': total, 'title_lines': title_lines, 'title_font_size': title_font_size,
            'author': request.author, 'color': request.color}


def ass_time(seconds):
    value = max(0, round(seconds * 100))
    return f'{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}'


def ass_text(text):
    # Escape literal braces and break user backslash commands without changing visible text.
    return text.replace('\\', '\\\u2060').replace('{', '\\{').replace('}', '\\}').replace('\n', ' ')


def write_ass(path, layout):
    l = layout
    color = l['color'].lstrip('#')
    color = '&H00' + color[4:6] + color[2:4] + color[:2]
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {l['width']}
PlayResY: {l['height']}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Body,Songti SC,{l['font_size']},{color},{color},&H80202020,&H80202020,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    def event(start, end, tags, text):
        if end > start:
            events.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Body,,0,0,0,,{{{tags}}}{ass_text(text)}')
    if l['intro']:
        size = l['title_font_size']
        center = l['height'] * .45
        for i, title in enumerate(l['title_lines']):
            y = center + (i - (len(l['title_lines']) - 1) / 2) * size * 1.4
            event(0, l['intro'], f"\\an5\\pos({l['width']/2},{y})\\fs{size}\\fad(350,350)", title)
        if l['author']:
            y = center + (len(l['title_lines']) + 1) / 2 * size * 1.4
            event(0, l['intro'], f"\\an5\\pos({l['width']/2},{y})\\fs34\\fad(350,350)", '作者：' + l['author'])
    for line in l['lines']:
        start, end = line['start'], line['end']
        y0 = l['start_y'] + line['offset'] - l['speed'] * max(0, start - l['intro'])
        moving_end = min(end, l['intro'] + l['audio_duration'])
        y1 = l['start_y'] + line['offset'] - l['speed'] * (moving_end - l['intro'])
        tags = (f"\\an7\\clip({l['left']-4},{l['top']},{l['width']-l['left']+4},{l['bottom']})"
                f"\\move({l['left']},{y0:.3f},{l['left']},{y1:.3f},0,{max(1,round((moving_end-start)*1000))})"
                f"\\fad({round(line['fade_in']*1000)},{round(line['fade_out']*1000)})")
        event(start, end, tags, line['text'])
    path.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')


class VideoManager:
    def __init__(self, data, speech):
        self.root = Path(data) / 'videos'
        self.assets = self.root / 'assets'
        self.directory = self.root / 'projects'
        self.previews = self.root / 'previews'
        for path in (self.assets, self.directory, self.previews):
            path.mkdir(parents=True, exist_ok=True)
        self.speech = speech
        self.lock = speech.lock
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='video-export')
        self.jobs, self.events, self.processes = {}, {}, {}
        for path in self.directory.glob('*/project.json'):
            try:
                job = json.loads(path.read_text())
                if job['status'] in ACTIVE:
                    job.update(status='interrupted', message='上次导出已中断，素材保留，可重新导出。')
                    save_json(path, job)
                self.jobs[job['id']] = job
            except (ValueError, KeyError):
                continue

    def asset(self, identifier, kind=None):
        if not re.fullmatch(IDENTIFIER, identifier):
            raise ValueError('素材不存在。')
        path = self.assets / identifier / 'asset.json'
        if not path.is_file():
            raise ValueError('素材不存在，请重新选择。')
        asset = json.loads(path.read_text())
        if kind and asset['kind'] != kind:
            raise ValueError('素材类型不匹配。')
        return asset, path.parent / asset['filename']

    def upload(self, upload, kind):
        identifier = uuid.uuid4().hex
        folder = self.assets / identifier
        folder.mkdir()
        suffix = Path(upload.filename or '').suffix.lower()
        allowed = {'.jpg', '.jpeg', '.png', '.webp'} if kind == 'image' else {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.aiff', '.webm'}
        try:
            if suffix not in allowed:
                raise ValueError('图片支持 JPG、PNG、WebP；音频支持 WAV、MP3、M4A、FLAC 等常用格式。')
            source = folder / ('upload' + suffix)
            size = 0
            with source.open('wb') as out:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > (30 if kind == 'image' else 256) * 1024 * 1024:
                        raise ValueError('图片不能超过 30 MB，音频不能超过 256 MB。')
                    out.write(chunk)
            asset = {'id': identifier, 'kind': kind, 'name': Path(upload.filename or '素材').name[:120], 'created_at': time.time()}
            if kind == 'image':
                with Image.open(source) as im:
                    if im.width * im.height > 40_000_000:
                        raise ValueError('图片尺寸过大，请使用 4000 万像素以内的图片。')
                    im = ImageOps.exif_transpose(im)
                    if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
                        canvas = Image.new('RGBA', im.size, '#f5f4ef')
                        im = Image.alpha_composite(canvas, im.convert('RGBA'))
                    im = im.convert('RGB')
                    im.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                    im.save(folder / 'image.jpg', quality=95)
                    asset.update(filename='image.jpg', width=im.width, height=im.height)
            else:
                progress = run_media(['-protocol_whitelist', 'file,pipe', '-i', source, '-t', '7201', '-vn', '-ac', '2', '-ar', '48000',
                                      '-c:a', 'libmp3lame', '-b:a', '192k', '-progress', 'pipe:1', folder / 'audio.mp3'])
                times = re.findall(r'out_time_us=(\d+)', progress)
                duration = int(times[-1]) / 1e6 if times else 0
                if not 0.5 <= duration <= 7200:
                    raise ValueError('音频需要在 0.5 秒到 2 小时之间。')
                asset.update(filename='audio.mp3', duration=duration)
            source.unlink(missing_ok=True)
            save_json(folder / 'asset.json', asset)
            return asset
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError('素材无法读取，请检查文件是否损坏。') from exc
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def library(self):
        assets = []
        for path in self.assets.glob('*/asset.json'):
            try:
                assets.append(json.loads(path.read_text()))
            except ValueError:
                continue
        with self.lock:
            sources = [{'id': j['id'], 'title': j['title'], 'duration': j['duration']} for j in sorted(self.speech.jobs.values(), key=lambda j: j.get('created_at', ''), reverse=True)
                       if j['status'] == 'completed' and j.get('duration') and (self.speech.directory / j['id'] / 'narration.wav').is_file()]
        return {'assets': sorted(assets, key=lambda a: a['created_at'], reverse=True), 'sources': sources}

    def resolve(self, request):
        _, image = self.asset(request.image_id, 'image')
        if request.job_id:
            try:
                job = self.speech.get(request.job_id)
            except KeyError as exc:
                raise ValueError('配音作品不存在。') from exc
            audio = self.speech.directory / job['id'] / 'narration.wav'
            if job['status'] != 'completed' or not job.get('duration') or not audio.is_file():
                raise ValueError('请先完成配音，再制作视频。')
            duration = job['duration']
        else:
            asset, audio = self.asset(request.audio_id, 'narration')
            duration = asset['duration']
        if not .5 <= duration <= 7200:
            raise ValueError('视频支持 0.5 秒到 2 小时的配音。')
        bgm = self.asset(request.bgm_id, 'music')[1] if request.bgm_id else None
        return image, audio, bgm, duration

    def preview(self, request):
        image, _, _, duration = self.resolve(request)
        layout = make_layout(request, duration)
        key = hashlib.sha256(json.dumps([request.image_id, request.aspect, request.crop_x, request.crop_y, request.shade]).encode()).hexdigest()
        target = self.previews / (key + '.jpg')
        if not target.is_file():
            with Image.open(image) as im:
                im = ImageOps.fit(im, (layout['width'], layout['height']), Image.Resampling.LANCZOS, centering=(request.crop_x, request.crop_y))
                im = Image.blend(im, Image.new('RGB', im.size, '#000000'), request.shade)
                temporary = target.with_suffix('.' + uuid.uuid4().hex + '.jpg')
                im.save(temporary, quality=95)
                temporary.replace(target)
        layout['background_url'] = '/api/video/backgrounds/' + target.name
        return layout, target

    def has_active_jobs(self):
        with self.lock:
            return any(j['status'] in ACTIVE for j in self.jobs.values())

    def get(self, identifier):
        with self.lock:
            return copy.deepcopy(self.jobs[identifier])

    def recent(self):
        with self.lock:
            return [copy.deepcopy(j) for j in sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:30]]

    def update(self, identifier, **changes):
        with self.lock:
            self.jobs[identifier].update(changes)
            save_json(self.directory / identifier / 'project.json', self.jobs[identifier])

    def create(self, request):
        self.resolve(request)
        with self.lock:
            if sum(j['status'] in ACTIVE for j in self.jobs.values()) >= 2:
                raise ValueError('已有两条视频在导出或等待，请完成后再添加。')
            identifier = uuid.uuid4().hex
            (self.directory / identifier).mkdir()
            job = {'id': identifier, 'title': request.title or '未命名作品', 'request': request.model_dump(),
                   'status': 'queued', 'progress': 0, 'created_at': time.time(), 'elapsed': 0,
                   'message': '等待导出。', 'duration': None}
            self.jobs[identifier] = job
            self.events[identifier] = threading.Event()
            self.update(identifier)
            self.executor.submit(self.run, identifier)
            return copy.deepcopy(job)

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            if job['status'] in ACTIVE:
                self.events[identifier].set()
                self.update(identifier, status='cancelling', message='正在停止导出…')
                process = self.processes.get(identifier)
                if process and process.poll() is None:
                    process.terminate()
        return self.get(identifier)

    def run(self, identifier):
        began = time.monotonic()
        folder = self.directory / identifier
        output = folder / 'video.pending.mp4'
        try:
            request = VideoRequest(**self.get(identifier)['request'])
            cancelled = self.events[identifier]
            if cancelled.is_set():
                return
            self.update(identifier, status='preparing', message='正在排版文字与准备画面。')
            layout, background = self.preview(request)
            _, audio, bgm, _ = self.resolve(request)
            shutil.copyfile(background, folder / 'background.jpg')
            save_json(folder / 'layout.json', layout)
            write_ass(folder / 'motion.ass', layout)
            total = layout['duration']
            self.update(identifier, duration=total)
            for encoder in ['h264_videotoolbox', 'libx264']:
                if cancelled.is_set():
                    return
                self.update(identifier, status='exporting', encoder=encoder, progress=0,
                            message='正在合成视频。' if encoder == 'h264_videotoolbox' else '使用兼容模式导出。')
                cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
                       '-loop', '1', '-framerate', '30', '-i', 'background.jpg', '-i', str(audio.resolve())]
                if bgm:
                    cmd += ['-stream_loop', '-1', '-i', str(bgm.resolve())]
                filters = ["[0:v]ass=motion.ass,format=yuv420p[v]",
                           f"[1:a]aresample=48000,adelay={round(request.intro*1000)}:all=1,apad,atrim=duration={total}[voice]"]
                if bgm:
                    fade = min(2, total / 3)
                    filters += [f"[2:a]aresample=48000,volume={request.bgm_volume},atrim=duration={total},afade=t=in:d={fade},afade=t=out:st={total-fade}:d={fade}[music]",
                                '[voice][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[a]']
                else:
                    filters += ['[voice]anull[a]']
                cmd += ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]', '-t', str(total), '-c:v', encoder]
                cmd += ['-b:v', '5M'] if encoder == 'h264_videotoolbox' else ['-preset', 'veryfast', '-crf', '21']
                cmd += ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-progress', 'pipe:1', '-stats_period', '0.5', output.name]
                with (folder / 'export.log').open('a') as log:
                    log.write('\nEncoder: ' + encoder + '\n')
                    log.flush()
                    process = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=log, text=True)
                    with self.lock:
                        self.processes[identifier] = process
                        if cancelled.is_set():
                            process.terminate()
                    for line in process.stdout:
                        if line.startswith('out_time_us='):
                            value = line.partition('=')[2].strip()
                            if value.lstrip('-').isdigit():
                                self.update(identifier, progress=min(.99, max(0, int(value) / 1e6 / total)), elapsed=round(time.monotonic()-began, 1))
                    code = process.wait()
                if cancelled.is_set():
                    return
                if code == 0 and output.is_file() and output.stat().st_size > 1000:
                    output.replace(folder / 'video.mp4')
                    self.update(identifier, status='completed', progress=1, elapsed=round(time.monotonic()-began, 1), message='视频已完成，可以播放与下载。')
                    return
                output.unlink(missing_ok=True)
            raise ValueError('视频导出失败，素材已保留。可重新导出；详情见此作品的 export.log。')
        except Exception as exc:
            self.update(identifier, status='failed', message=str(exc), elapsed=round(time.monotonic()-began, 1))
        finally:
            with self.lock:
                self.processes.pop(identifier, None)
                if self.events[identifier].is_set() and self.jobs[identifier]['status'] != 'completed':
                    self.update(identifier, status='cancelled', message='已停止导出，素材与设置已保留。', elapsed=round(time.monotonic()-began, 1))
            output.unlink(missing_ok=True)

    def shutdown(self):
        for identifier in list(self.jobs):
            if self.jobs[identifier]['status'] in ACTIVE:
                self.cancel(identifier)
        self.executor.shutdown(wait=True, cancel_futures=False)

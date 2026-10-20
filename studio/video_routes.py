import re
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .video import VideoRequest


def video_router(manager):
    router = APIRouter(prefix='/api/video')

    def get_job(identifier):
        try:
            return manager.get(identifier)
        except KeyError:
            raise HTTPException(404, '视频作品不存在。')

    @router.get('/library')
    def library():
        return manager.library()

    @router.post('/assets/{kind}', status_code=201)
    def upload(kind: Literal['image', 'narration', 'music'], file: UploadFile = File(...)):
        return manager.upload(file, kind)

    @router.get('/assets/{identifier}/file')
    def asset_file(identifier: str):
        asset, path = manager.asset(identifier)
        return FileResponse(path, media_type='image/jpeg' if asset['kind'] == 'image' else 'audio/mpeg')

    @router.get('/backgrounds/{filename}')
    def background(filename: str):
        if not re.fullmatch(r'[a-f0-9]{64}\.jpg', filename) or not (manager.previews / filename).is_file():
            raise HTTPException(404, '画面不存在。')
        return FileResponse(manager.previews / filename, media_type='image/jpeg')

    @router.post('/preview')
    def preview(request: VideoRequest):
        return manager.preview(request)[0]

    @router.post('/exports', status_code=201)
    def create(request: VideoRequest):
        return manager.create(request)

    @router.get('/exports')
    def recent():
        return manager.recent()

    @router.get('/exports/{identifier}')
    def get(identifier: str):
        return get_job(identifier)

    @router.post('/exports/{identifier}/cancel')
    def cancel(identifier: str):
        get_job(identifier)
        return manager.cancel(identifier)

    @router.post('/exports/{identifier}/retry', status_code=201)
    def retry(identifier: str):
        job = get_job(identifier)
        if job['status'] not in {'failed', 'cancelled', 'interrupted'}:
            raise HTTPException(409, '这份视频无需重新导出。')
        return manager.create(VideoRequest(**job['request']))

    @router.get('/exports/{identifier}/video.mp4')
    def download(identifier: str, download: bool = False):
        job = get_job(identifier)
        path = manager.directory / identifier / 'video.mp4'
        if job['status'] != 'completed' or not path.is_file():
            raise HTTPException(404, '视频尚未生成。')
        title = re.sub(r'[\\/\x00-\x1f]', '_', job['title'])[:80]
        return FileResponse(path, media_type='video/mp4', filename=title + '.mp4',
                            content_disposition_type='attachment' if download else 'inline')

    return router

from fastapi import APIRouter
from backend.worker.service import status

router = APIRouter(prefix='/api/worker', tags=['worker'])


@router.get('/status')
async def get_worker_status():
    return status()

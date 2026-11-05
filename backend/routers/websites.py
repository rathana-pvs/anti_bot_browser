from fastapi import APIRouter, HTTPException
from backend.services import website_service as service

router = APIRouter(prefix='/api/websites', tags=['websites'])


def checked(action, *args):
    try:
        return action(*args)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.get('')
def connections():
    return service.connections()


@router.post('')
def connect(request: service.Connector):
    return checked(service.save_connection, request)


@router.delete('/{connection_id}')
def disconnect(connection_id: str):
    service.remove_connection(connection_id)
    return {'success': True}


@router.get('/{connection_id}/catalog')
def catalog(connection_id: str):
    return checked(service.catalog, connection_id)


@router.post('/prepare')
def prepare(request: service.Prepare):
    return checked(service.prepare, request)


@router.post('/captions')
def prepare_captions(request: service.PrepareCaptions):
    return checked(service.prepare_captions, request)


@router.post('/regenerate-hook')
def regenerate_hook(request: service.RegenerateHook):
    return checked(service.regenerate_hook, request)


@router.post('/{connection_id}/test')
def test_connection(connection_id: str):
    return checked(service.test_connection, connection_id)

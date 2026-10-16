"""Security boundaries, recovery and network failures for the mobile upgrade."""
import time
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth
from app.main import app
from app.db import engine, init_db
from app.models import Controller, Light, LightModel, User, LookCheckpoint
from app.artnet import ArtNetManager, UniverseBuffer
from app.schemas import ControllerIn, ControllerOut


@pytest.fixture()
def clients():
    init_db()
    auth._attempts.clear()
    accounts = {}
    for role in ['admin', 'operator', 'viewer']:
        username = f'{role}-{uuid.uuid4().hex[:8]}'
        auth.create_user(username, role, role, 'Test-password-123!')
        client = TestClient(app, base_url='https://testserver')
        response = client.post('/api/auth/login', json={'username': username, 'password':'Test-password-123!'})
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
        accounts[role] = client
    yield accounts
    for c in accounts.values(): c.close()


def test_named_logins_and_server_enforced_roles(clients):
    assert clients['admin'].get('/api/auth/users').status_code == 200
    assert clients['operator'].get('/api/auth/users').status_code == 403
    assert clients['viewer'].post('/api/layers/clear').status_code == 403
    data = {'name':'Unauthorized', 'ip':'127.0.0.1'}
    assert clients['operator'].post('/api/controllers',json=data).status_code == 403
    assert clients['admin'].post('/api/controllers',json={**data,'ip':'192.168.099.239'}).status_code == 422
    assert clients['admin'].post('/api/controllers',json=data,headers={'Origin':'https://wrong.example'}).status_code == 403
    assert TestClient(app,base_url='https://testserver').post('/api/auth/login',json={'password':'secretsauce'}).status_code == 401


def test_disabled_accounts_revoke_existing_sessions(clients):
    user = clients['operator'].get('/api/auth/status').json()['user']
    assert clients['admin'].patch(f'/api/auth/users/{user["id"]}',json={'enabled':False}).status_code == 200
    assert clients['operator'].get('/api/controllers').status_code == 401


def test_login_throttling():
    auth._attempts.clear()
    c = TestClient(app,base_url='https://testserver')
    for _ in range(8):
        assert c.post('/api/auth/login',json={'username':'missing-account','password':'wrong'}).status_code == 401
    response=c.post('/api/auth/login',json={'username':'missing-account','password':'wrong'})
    assert response.status_code == 429 and response.headers['retry-after']=='300'
    auth._attempts.clear()


def test_address_validation_and_legacy_read():
    assert ControllerIn(name='c',ip=' 192.168.99.239 ').ip=='192.168.99.239'
    for bad in ['192.168.099.239','192.168.1.999','hello','::1']:
        with pytest.raises(ValueError):ControllerIn(name='c',ip=bad)
    assert ControllerOut(id=1,name='legacy',ip='192.168.099.239',port=6454,net=0,subnet=0,universe=0,enabled=True).ip.endswith('239')


def test_send_failure_health_backoff_and_recovery():
    mgr=ArtNetManager();mgr.output_enabled=True
    c=Controller(id=1,name='Stage',ip='127.0.0.1');buf=UniverseBuffer(0,0,0)
    mgr._controllers[c.id]=(c,buf)
    mgr._sock.close();mgr._sock=Mock()
    mgr._sock.sendto.side_effect=OSError('network unavailable')
    mgr._send_buffer(c,buf);mgr._send_buffer(c,buf)
    assert mgr._sock.sendto.call_count==1
    assert mgr.controller_health(1)['status']=='error'
    assert mgr.controller_health(1)['error_count']==1
    mgr._retry_after[1]=0;mgr._sock.sendto.side_effect=None;mgr._send_buffer(c,buf)
    assert mgr.controller_health(1)['status']=='sending'
    assert mgr.controller_health(1)['last_error'] is None
    assert mgr.controller_health(1)['sent_count']==1
    mgr.close()


def test_thinking_blocks_and_new_model_request_shape():
    from app.ai_runtime import request_options,serialize_content
    from app.routers.effect_chat import _build_messages_for_api
    thinking={'type':'thinking','thinking':'','signature':'signed-value'}
    tool={'type':'tool_use','id':'t1','name':'propose_effect','input':{}}
    blocks=serialize_content(SimpleNamespace(content=[thinking,tool]))
    history=_build_messages_for_api([{'role':'assistant','content':blocks}],'make it slower')
    assert history[0]['content'][0]==thinking
    assert history[1]['content'][0]['tool_use_id']=='t1'
    options=request_options()
    assert options['tool_choice']=={'type':'auto'}
    assert options['thinking']['type']=='adaptive'


def test_scene_capture_and_restore_preserve_running_layers(clients):
    from app.engine import engine as effects
    with Session(engine) as s:
        c=Controller(name='Recovery test',ip='127.0.0.1');s.add(c);s.flush()
        m=LightModel(name='Recovery RGB',channel_count=3,channels=['r','g','b']);s.add(m);s.flush()
        l=Light(name='Recovery fixture',controller_id=c.id,model_id=m.id,start_address=1,r=100,g=40,b=0)
        s.add(l);s.commit();s.refresh(l);lid=l.id;cid=c.id
    from app.artnet import rebuild_manager_sync
    rebuild_manager_sync()
    client=clients['admin']
    effect=client.post('/api/effects',json={'name':'Recovery wave','source':"function render(ctx) return { r=10,g=20,b=30,brightness=1 } end"}).json()
    layer=client.post('/api/layers',json={'effect_id':effect['id'],'light_ids':[lid],'mask_light_ids':[lid],'opacity':0.35}).json()
    scene=client.post('/api/scenes',json={'name':'Saved layer','controller_id':cid,'light_ids':[lid],'include_layers':True,'favorite':True}).json()
    assert scene['favorite'] and scene['layers'][0]['opacity']==0.35
    assert scene['layers'][0]['mask_light_ids']==[lid]
    response=client.post('/api/states/blackout/apply')
    assert response.status_code==200
    assert not effects.layer_snapshot()
    assert client.get('/api/looks/history').json()['available']
    assert client.post('/api/looks/restore',json={}).status_code==200
    assert any(l['opacity']==pytest.approx(0.35) for l in effects.layer_snapshot())
    with Session(engine) as s:assert s.get(Light,lid).r==100
    assert client.post(f'/api/scenes/{scene["id"]}/apply?fade_s=-1').status_code==422
    assert client.post(f'/api/scenes/{scene["id"]}/apply?fade_s=0.05').status_code==200
    time.sleep(.1)
    effects.stop_all(immediate=True)

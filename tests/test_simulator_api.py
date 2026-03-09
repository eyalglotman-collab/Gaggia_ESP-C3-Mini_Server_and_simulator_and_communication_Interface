from fastapi.testclient import TestClient

from server.app import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_link_snapshot_shape() -> None:
    client = TestClient(app)
    response = client.get('/api/link')
    assert response.status_code == 200
    payload = response.json()
    assert payload['current_state'] == 'reset'
    assert payload['serial_port'] == 'COM4'
    assert 'HostLiveInteger' in payload['important_data']
    assert 'transport' in payload
    assert isinstance(payload['logs'], list)
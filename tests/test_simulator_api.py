from fastapi.testclient import TestClient

from server.app import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_connect_and_brew_flow() -> None:
    client = TestClient(app)

    connect = client.post('/api/connect', json={'client_ip_address': '192.168.1.50'})
    assert connect.status_code == 200
    snapshot = connect.json()
    assert snapshot['current_state'] == 'Idle'
    assert snapshot['connected_to_client'] is True
    assert snapshot['machine_initialized'] is True

    brew = client.post('/api/brew/start')
    assert brew.status_code == 200
    assert brew.json()['current_state'] == 'Brew'

    idle = client.post('/api/brew/stop')
    assert idle.status_code == 200
    assert idle.json()['current_state'] == 'Idle'

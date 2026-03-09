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
    assert payload['config']['wifi_ssid'] == 'EyalSimulatorAP'
    assert 'HostLiveInteger' in payload['important_data']
    assert 'transport' in payload
    assert isinstance(payload['logs'], list)


def test_transport_config_update_round_trip() -> None:
    client = TestClient(app)
    response = client.post(
        '/api/config',
        json={
            'serial_port': 'COM7',
            'wifi_ssid': 'LabBridge',
            'wifi_password': 'pw123456',
            'server_ip': '10.10.0.1',
            'server_port': 4444,
            'wifi_connect_timeout_ms': 12000,
            'tcp_connect_timeout_ms': 4000,
            'keepalive_period_ms': 150,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['serial_port'] == 'COM7'
    assert payload['config']['wifi_ssid'] == 'LabBridge'
    assert payload['config']['server_ip'] == '10.10.0.1'
    assert payload['config']['server_port'] == 4444
    assert payload['config']['keepalive_period_ms'] == 150

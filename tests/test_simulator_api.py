from fastapi.testclient import TestClient

from server.app import app
from server.sim.link_state_machine import link_runtime
from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager


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
    assert 'Last Monitor Event' in payload['important_data']
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


def test_open_port_reports_already_open(monkeypatch) -> None:
    client = TestClient(app)

    state = {'open': False}

    def fake_snapshot() -> SerialLinkSnapshot:
        return SerialLinkSnapshot(
            port_name='COM4',
            baud_rate=115200,
            port_open=state['open'],
            protocol='ESP32-C3 Framed Serial Link',
            last_event_at='2026-03-09 00:00:00Z',
            last_event='Serial transport manager ready.',
            last_error='',
            last_tx_at='Never',
            last_rx_at='Never',
            tx_frames=0,
            rx_frames=0,
            tx_bytes=0,
            rx_bytes=0,
        )

    def fake_open_port() -> SerialLinkSnapshot:
        if state['open']:
            return SerialLinkSnapshot(
                port_name='COM4',
                baud_rate=115200,
                port_open=True,
                protocol='ESP32-C3 Framed Serial Link',
                last_event_at='2026-03-09 00:00:01Z',
                last_event='Serial port COM4 already open.',
                last_error='',
                last_tx_at='Never',
                last_rx_at='Never',
                tx_frames=0,
                rx_frames=0,
                tx_bytes=0,
                rx_bytes=0,
            )
        state['open'] = True
        return SerialLinkSnapshot(
            port_name='COM4',
            baud_rate=115200,
            port_open=True,
            protocol='ESP32-C3 Framed Serial Link',
            last_event_at='2026-03-09 00:00:01Z',
            last_event='Opened serial port COM4 @ 115200.',
            last_error='',
            last_tx_at='Never',
            last_rx_at='Never',
            tx_frames=0,
            rx_frames=0,
            tx_bytes=0,
            rx_bytes=0,
        )

    monkeypatch.setattr(serial_link_manager, 'get_snapshot', fake_snapshot)
    monkeypatch.setattr(serial_link_manager, 'open_port', fake_open_port)

    first = client.post('/api/transport/open', json={'port_name': 'COM4'})
    assert first.status_code == 200
    second = client.post('/api/transport/open', json={'port_name': 'COM4'})
    assert second.status_code == 200
    payload = second.json()
    assert payload['transport']['port_open'] is True
    assert any('already open' in line.lower() for line in payload['logs'])


def test_link_snapshot_stays_available_if_open_fails(monkeypatch) -> None:
    client = TestClient(app)

    def fake_open_port() -> None:
        raise RuntimeError('busy')

    monkeypatch.setattr(serial_link_manager, 'open_port', fake_open_port)

    open_response = client.post('/api/transport/open', json={'port_name': 'COM4'})
    assert open_response.status_code == 200
    snapshot_response = client.get('/api/link')
    assert snapshot_response.status_code == 200
    payload = snapshot_response.json()
    assert payload['last_error'] == 'COM port not found'


def test_all_api_routes_return_snapshots(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(serial_link_manager, 'configure_port', lambda port_name: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'open_port', lambda: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'close_port', lambda: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'send_frame', lambda frame: serial_link_manager.get_snapshot())

    endpoints = (
        ('get', '/api/link', None),
        ('post', '/api/config', {
            'serial_port': 'COM4',
            'wifi_ssid': 'EyalSimulatorAP',
            'wifi_password': 'espresso1234',
            'server_ip': '192.168.4.1',
            'server_port': 3333,
            'wifi_connect_timeout_ms': 10000,
            'tcp_connect_timeout_ms': 3000,
            'keepalive_period_ms': 100,
        }),
        ('post', '/api/transport/open', {'port_name': 'COM4'}),
        ('post', '/api/transport/close', None),
        ('post', '/api/command/reset', None),
        ('post', '/api/command/initialize', None),
        ('post', '/api/command/connect', None),
        ('post', '/api/command/disconnect', None),
        ('post', '/api/command/keepalive', None),
    )

    for method, url, payload in endpoints:
        if method == 'get':
            response = client.get(url)
        elif payload is None:
            response = client.post(url)
        else:
            response = client.post(url, json=payload)
        assert response.status_code == 200, url
        body = response.json()
        assert 'current_state' in body, url
        assert 'transport' in body, url


def test_route_fail_safe_returns_snapshot_on_unexpected_exception(monkeypatch) -> None:
    client = TestClient(app)

    def boom() -> None:
        raise ValueError('unexpected')

    monkeypatch.setattr(link_runtime, 'get_snapshot', boom)
    response = client.get('/api/link')
    assert response.status_code == 200
    payload = response.json()
    assert payload['current_state'] == 'error'
    assert 'unexpected' in payload['last_error']


def test_monitor_events_are_written_to_logs() -> None:
    client = TestClient(app)
    response = client.post(
        '/api/config',
        json={
            'serial_port': 'COM4',
            'wifi_ssid': 'EyalSimulatorAP',
            'wifi_password': 'espresso1234',
            'server_ip': '192.168.4.1',
            'server_port': 3333,
            'wifi_connect_timeout_ms': 10000,
            'tcp_connect_timeout_ms': 3000,
            'keepalive_period_ms': 100,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['important_data']['Last Monitor Event'].startswith('api:')
    assert any('[monitor]' in line for line in payload['logs'])


def test_logs_are_newest_first_and_capped_to_2000() -> None:
    client = TestClient(app)

    with link_runtime._lock:  # noqa: SLF001 - regression check for rolling logger behavior
        link_runtime._logs.clear()  # noqa: SLF001
        serial_link_manager._logs.clear()  # noqa: SLF001
        for index in range(2105):
            link_runtime._logs.appendleft(f"[2026-03-10 12:{index // 60:02d}:{index % 60:02d}Z] runtime {index}")  # noqa: SLF001

    response = client.get('/api/link')
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['logs']) == 2000
    assert payload['logs'][0].endswith('runtime 2104')
    assert payload['logs'][-1].endswith('runtime 105')

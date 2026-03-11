import pytest
from fastapi.testclient import TestClient

from ServerInterface.frame_codec import Frame
from ServerInterface.frame_codec import FrameDecodeError
from ServerInterface.frame_codec import MessageType
from ServerInterface.frame_codec import decode_frames
from ServerInterface.frame_codec import encode_frame
from server.app import app
from server.sim.link_state_machine import LinkState, link_runtime
from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager


def _reset_runtime_for_test() -> None:
    with link_runtime._lock:  # noqa: SLF001 - controlled test fixture reset
        link_runtime._current_state = LinkState.RESET  # noqa: SLF001
        link_runtime._host_live_integer = 0  # noqa: SLF001
        link_runtime._device_live_integer = 0  # noqa: SLF001
        link_runtime._wifi_ready = False  # noqa: SLF001
        link_runtime._wifi_connected = False  # noqa: SLF001
        link_runtime._tcp_connected = False  # noqa: SLF001
        link_runtime._bridge_ready = False  # noqa: SLF001
        link_runtime._last_error = ''  # noqa: SLF001
        link_runtime._clear_runtime_flow_locked()  # noqa: SLF001


def _open_transport_snapshot() -> SerialLinkSnapshot:
    return SerialLinkSnapshot(
        port_name='COM4',
        baud_rate=115200,
        port_open=True,
        protocol='ESP32-C3 Framed Serial Link',
        last_event_at='2026-03-11 00:00:00Z',
        last_event='Opened serial port COM4 @ 115200.',
        last_error='',
        last_tx_at='Never',
        last_rx_at='Never',
        tx_frames=0,
        rx_frames=0,
        tx_bytes=0,
        rx_bytes=0,
    )


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
    monkeypatch.setattr(serial_link_manager, 'clear_buffers', lambda: None)

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
        ('post', '/api/command/keepalive', None),
        ('post', '/api/command/send-data', None),
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


def test_initialize_automatically_enters_connect_state(monkeypatch) -> None:
    client = TestClient(app)

    _reset_runtime_for_test()

    monkeypatch.setattr(serial_link_manager, 'send_frame', lambda frame: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'clear_buffers', lambda: None)
    monkeypatch.setattr(serial_link_manager, 'get_snapshot', _open_transport_snapshot)
    monkeypatch.setattr(serial_link_manager, 'pop_received_frames', lambda: [])

    response = client.post('/api/command/initialize')

    assert response.status_code == 200
    payload = response.json()
    assert payload['current_state'] == 'initialize'
    assert payload['important_data']['Send Data Enabled'] == 'No'

    snapshot_response = client.get('/api/link')
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()['current_state'] == 'connect'


def test_reset_automatically_progresses_into_initialize_and_connect(monkeypatch) -> None:
    client = TestClient(app)

    _reset_runtime_for_test()

    monkeypatch.setattr(serial_link_manager, 'send_frame', lambda frame: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'clear_buffers', lambda: None)
    monkeypatch.setattr(serial_link_manager, 'get_snapshot', _open_transport_snapshot)
    monkeypatch.setattr(serial_link_manager, 'pop_received_frames', lambda: [])

    reset_response = client.post('/api/command/reset')
    assert reset_response.status_code == 200
    assert reset_response.json()['current_state'] == 'reset'

    initialize_snapshot = client.get('/api/link')
    assert initialize_snapshot.status_code == 200
    assert initialize_snapshot.json()['current_state'] == 'initialize'

    connect_snapshot = client.get('/api/link')
    assert connect_snapshot.status_code == 200
    assert connect_snapshot.json()['current_state'] == 'connect'


def test_connect_ack_automatically_promotes_runtime_to_keepalive(monkeypatch) -> None:
    client = TestClient(app)

    _reset_runtime_for_test()

    rx_batches = [
        [],
        [],
        [
            Frame(
                message_type=MessageType.ACK,
                host_live_integer=0,
                device_live_integer=1,
                sequence=2,
                payload=b'connect_ack',
            )
        ],
    ]

    monkeypatch.setattr(serial_link_manager, 'send_frame', lambda frame: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'clear_buffers', lambda: None)
    monkeypatch.setattr(serial_link_manager, 'get_snapshot', _open_transport_snapshot)
    monkeypatch.setattr(
        serial_link_manager,
        'pop_received_frames',
        lambda: rx_batches.pop(0) if rx_batches else [],
    )

    client.post('/api/command/reset')
    client.get('/api/link')
    client.get('/api/link')
    keepalive_snapshot = client.get('/api/link')

    assert keepalive_snapshot.status_code == 200
    payload = keepalive_snapshot.json()
    assert payload['current_state'] == 'keepalive'
    assert payload['important_data']['Send Data Enabled'] == 'Yes'


def test_send_data_stays_blocked_until_keepalive_ready(monkeypatch) -> None:
    client = TestClient(app)

    _reset_runtime_for_test()

    monkeypatch.setattr(serial_link_manager, 'send_frame', lambda frame: serial_link_manager.get_snapshot())
    monkeypatch.setattr(serial_link_manager, 'clear_buffers', lambda: None)
    monkeypatch.setattr(serial_link_manager, 'pop_received_frames', lambda: [])

    response = client.post('/api/command/send-data')

    assert response.status_code == 200
    payload = response.json()
    assert payload['current_state'] == 'error'
    assert payload['last_error'] == 'send data requires keepalive-ready connection'


def test_server_interface_frame_codec_round_trip() -> None:
    frame = Frame(
        message_type=MessageType.KEEPALIVE,
        host_live_integer=7,
        device_live_integer=11,
        sequence=19,
        payload=b"alive",
    )

    encoded = encode_frame(frame)
    buffer = bytearray(encoded)
    decoded = decode_frames(buffer)

    assert len(decoded) == 1
    assert decoded[0] == frame
    assert buffer == bytearray()


def test_server_interface_frame_codec_rejects_bad_crc() -> None:
    frame = Frame(
        message_type=MessageType.CONNECT,
        host_live_integer=1,
        device_live_integer=2,
        sequence=3,
        payload=b"endpoint",
    )
    corrupted = bytearray(encode_frame(frame))
    corrupted[-1] ^= 0xFF

    with pytest.raises(FrameDecodeError):
        decode_frames(corrupted)

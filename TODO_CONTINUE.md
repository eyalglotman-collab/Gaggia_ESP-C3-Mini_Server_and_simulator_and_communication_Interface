# Continue From Here

Current proven state:
- The simulator no longer sends duplicate host-driven `CONNECT` commands during normal connect progression.
- The ESP32-C3 bridge owns the real low-level TCP listener and keepalive path.
- The client reaches `Keepalive`, so startup, Wi-Fi discovery, and TCP open are not the main blocker now.

Current recurring faults:
- mirrored `keepalive_counter_mismatch`
- mirrored `keepalive_supervision_lost`
- eventual reset-required fault state after repeated failures

What to continue from next time:
1. Inspect the bridge keepalive truth first:
   - `firmware/esp32c3_bridge/main/bridge_main.c`
   - Add temporary logs for every server keepalive send and every client keepalive reply.
   - Record:
     - `s_server_live_integer`
     - `s_client_live_integer`
     - received `host_live_integer`
     - received `device_live_integer`
     - exact reason for `keepalive_counter_mismatch`
2. Inspect the Python mirror second:
   - `server/sim/link_state_machine.py`
   - Verify it only mirrors bridge-owned traffic and does not re-latch bridge-originated noise as a new host-side fault.
3. Compare against the client:
   - `../Eyal_espresso_client/main/CommunicationFunctions.c`
   - Match each bridge keepalive TX/RX pair to the client-side keepalive validation path.

Do not restart from:
- COM-port ownership
- Wi-Fi enable/disable UI
- early simulator UI naming/layout work

COM-port workflow reminder:
- After every `flash` or `monitor`, release all exact COM-holder PIDs.
- Keep COM cleanup narrow and targeted to the real port-holder processes only.

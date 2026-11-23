import ssl
import time

import pytest

from noc_pulse.probes import ProbeResult, cert_days_left, parse_ping_output

WINDOWS_OK = """
Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=4ms TTL=60
Reply from 1.1.1.1: bytes=32 time<1ms TTL=60
Request timed out.
Reply from 1.1.1.1: bytes=32 time=6ms TTL=60

Ping statistics for 1.1.1.1:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
"""

WINDOWS_UNREACHABLE = """
Pinging 10.9.9.9 with 32 bytes of data:
Reply from 192.168.1.20: Destination host unreachable.
Request timed out.

Ping statistics for 10.9.9.9:
    Packets: Sent = 2, Received = 1, Lost = 1 (50% loss),
"""

LINUX_OK = """
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=5.12 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=4.98 ms

--- 8.8.8.8 ping statistics ---
3 packets transmitted, 2 received, 33.3333% packet loss, time 2003ms
"""


def test_parse_windows_ping():
    sent, received, rtts = parse_ping_output(WINDOWS_OK, 4)
    assert (sent, received) == (4, 3)
    assert rtts == [4.0, 0.5, 6.0]  # "<1ms" is recorded as 0.5 ms


def test_parse_windows_unreachable_counts_as_lost():
    # Windows counts "Destination host unreachable" as *received*; we do not
    sent, received, rtts = parse_ping_output(WINDOWS_UNREACHABLE, 2)
    assert (sent, received, rtts) == (2, 0, [])


def test_parse_linux_ping():
    assert parse_ping_output(LINUX_OK, 3) == (3, 2, [5.12, 4.98])


def test_cert_days_left():
    not_after = time.strftime("%b %d %H:%M:%S %Y GMT", time.gmtime(1_800_000_000))
    assert ssl.cert_time_to_seconds(not_after) == 1_800_000_000
    assert cert_days_left(not_after, now=1_800_000_000 - 10 * 86400) == pytest.approx(10.0)


def test_result_row_roundtrip_and_derived_metrics():
    r = ProbeResult(
        ts=1.0,
        target="t",
        probe="ping",
        ok=True,
        latency_ms=11.0,
        sent=4,
        received=3,
        rtts=[10.0, 12.0, 11.0],
        detail={"backend": "x"},
    )
    back = ProbeResult.from_row(r.to_row())
    assert back == r
    assert r.loss_pct == 25.0
    assert r.jitter_ms == pytest.approx(1.5)

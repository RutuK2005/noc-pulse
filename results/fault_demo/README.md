# Fault-injection demo output

Run on 2026-11-23 23:52:47 (local time) with `noc-pulse demo`. Probe interval 2 s, raise after 3 bad / clear after 2 good samples. All targets are local listeners on 127.0.0.1.

## Timeline (seconds since start)

```
t+   0.1s  lab started: edge TCP 18081, app HTTP 18080, api TCP 18082 (all healthy)
t+  10.1s  FAULT INJECTED #1: edge router down (all three listeners stopped)
t+  18.1s  RAISED alarm #3 lab-edge/tcp/unreachable [critical]: lab-edge/tcp: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  18.1s  CORRELATED alarm #1 lab-api/tcp/unreachable [critical] under root cause alarm #3 (lab-edge)
t+  18.1s  CORRELATED alarm #2 lab-app/http/unreachable [critical] under root cause alarm #3 (lab-edge)
t+  18.1s  RAISED alarm #2 lab-app/http/unreachable [critical]: lab-app/http: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  18.1s  RAISED alarm #1 lab-api/tcp/unreachable [critical]: lab-api/tcp: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  22.1s  TICKET INC000001 opened P1 for alarm #3; impacted services: branch07-access, branch07-web, branch07-api
t+  27.1s  OPERATOR ack INC000001: L1: acknowledged, edge unreachable, checking
t+  27.2s  DIAGNOSTICS attached to INC000001: L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
t+  30.1s  OPERATOR start INC000001: L1: field/carrier check requested
t+  34.1s  FIX APPLIED #1: edge router restored (listeners started again)
t+  36.1s  CLEARED alarm #3 lab-edge/tcp/unreachable [critical] after 18s
t+  36.1s  TICKET INC000001 resolved after 14s (auto-resolved: alarm cleared, service restored)
t+  36.1s  CLEARED alarm #2 lab-app/http/unreachable [critical] after 18s
t+  36.1s  CLEARED alarm #1 lab-api/tcp/unreachable [critical] after 18s
t+  40.2s  OPERATOR close INC000001: customer confirmed service restored
t+  46.2s  FAULT INJECTED #2: web app down only (edge healthy)
t+  56.1s  RAISED alarm #4 lab-app/http/unreachable [critical]: lab-app/http: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  60.1s  TICKET INC000002 opened P2 for alarm #4; impacted services: branch07-web
t+  64.1s  DIAGNOSTICS attached to INC000002: L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
t+  67.2s  FIX APPLIED #2: web app restarted
t+  70.1s  CLEARED alarm #4 lab-app/http/unreachable [critical] after 14s
t+  70.1s  TICKET INC000002 resolved after 10s (auto-resolved: alarm cleared, service restored)
```

## Summary

```json
{
  "phase1": {
    "detect_s": 8.0,
    "ticket": "INC000001",
    "priority": "P1",
    "suppressed_child_alarms": [
      "#1 lab-api",
      "#2 lab-app"
    ],
    "tickets_opened_in_phase": 1,
    "time_to_ack_s": 5.1,
    "mttr_s": 14.0,
    "restore_after_fix_s": 1.9
  },
  "phase2": {
    "detect_s": 9.8,
    "ticket": "INC000002",
    "priority": "P2",
    "suppressed": false,
    "mttr_s": 10.0
  },
  "tickets_total": 2,
  "alarms_total": 4,
  "alarms_suppressed": 2,
  "samples": 88
}
```

## Tickets

### INC000001 P1 Closed: [CRITICAL] Branch-07 edge router (lab listener): unreachable (tcp)

- services: branch07-access, branch07-web, branch07-api
- resolution: auto-resolved: alarm cleared, service restored

Worklog:

```
t+  22.1s [opened] P1 (impact=high, urgency=high): [CRITICAL] Branch-07 edge router (lab listener): unreachable (tcp)
t+  22.1s [alarm] lab-edge/tcp: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  27.1s [state] New -> Acknowledged: L1: acknowledged, edge unreachable, checking
t+  27.2s [diagnostics] L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
t+  30.1s [state] Acknowledged -> In Progress: L1: field/carrier check requested
t+  36.1s [state] In Progress -> Resolved: auto-resolved: alarm cleared, service restored
t+  36.1s [correlation] child alarm #1 cleared
t+  36.1s [correlation] child alarm #2 cleared
t+  40.2s [state] Resolved -> Closed: customer confirmed service restored
```

Auto-diagnostics attached on open:

```
== auto-diagnostics for lab-edge (Branch-07 edge router (lab listener)) at 2026-11-23 23:53:11 ==

$ ping -n 4 -w 1000 127.0.0.1
Pinging 127.0.0.1 with 32 bytes of data:
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Ping statistics for 127.0.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 0ms, Average = 0ms

$ tracert -d -h 12 -w 500 127.0.0.1
Tracing route to 127.0.0.1 over a maximum of 12 hops
  1    <1 ms    <1 ms    <1 ms  127.0.0.1
Trace complete.

TCP connect 127.0.0.1:18081: FAILED (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)

L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
```

### INC000002 P2 Resolved: [CRITICAL] Customer web app (lab HTTP server): unreachable (http)

- services: branch07-web
- resolution: auto-resolved: alarm cleared, service restored

Worklog:

```
t+  60.1s [opened] P2 (impact=medium, urgency=high): [CRITICAL] Customer web app (lab HTTP server): unreachable (http)
t+  60.1s [alarm] lab-app/http: Probe failed (timeout, refused, DNS error, HTTP >= 400) (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)
t+  64.1s [diagnostics] L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
t+  70.1s [state] New -> Resolved: auto-resolved: alarm cleared, service restored
```

Auto-diagnostics attached on open:

```
== auto-diagnostics for lab-app (Customer web app (lab HTTP server)) at 2026-11-23 23:53:47 ==

$ ping -n 4 -w 1000 127.0.0.1
Pinging 127.0.0.1 with 32 bytes of data:
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
Ping statistics for 127.0.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 0ms, Average = 0ms

$ tracert -d -h 12 -w 500 127.0.0.1
Tracing route to 127.0.0.1 over a maximum of 12 hops
  1    <1 ms    <1 ms    <1 ms  127.0.0.1
Trace complete.

TCP connect 127.0.0.1:18080: FAILED (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)

HTTP GET http://127.0.0.1:18080/health: FAILED (ConnectionRefusedError: [WinError 1225] The remote computer refused the network connection)

L1 triage summary: host answers ping but port refused/filtered -> application or firewall issue
```

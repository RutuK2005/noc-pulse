# NOC shift handover

Period: 2026-11-23 23:36:31 -> 2026-11-24 01:35:31 (last 8 h of data, local time).

## Service health in this shift

| Service | Class | Avail % | p95 ms | Loss % | MOS | SLA |
|---|---|---:|---:|---:|---:|---|
| acme-dia | silver | 97.500 | 6.48 | 1.744 | - | BREACH: availability_pct, loss_pct |
| acme-voice | gold | 98.333 | 6.36 | 1.681 | 4.25 | BREACH: availability_pct, loss_pct |
| acme-dns | silver | 97.500 | 41.87 | - | - | BREACH: availability_pct |
| portal-tatacomm | bronze | 97.500 | 368.93 | - | - | BREACH: availability_pct |
| saas-web | bronze | 97.500 | 461.34 | - | - | BREACH: availability_pct |

## Alarms

- Raised this shift: 10 (4 suppressed as symptoms of a root cause)
- Still active at handover: 0

## Tickets

- Open: 0
- Resolved this shift: 6
  - INC000001 P2: [CRITICAL] Quad9 9.9.9.9: unreachable (ping) (time to resolve 1.0 min)
  - INC000002 P1: [CRITICAL] Google Public DNS 8.8.8.8: unreachable (ping) (time to resolve 0.5 min)
  - INC000003 P1: [CRITICAL] Cloudflare anycast 1.1.1.1: unreachable (ping) (time to resolve 0.5 min)
  - INC000004 P2: [CRITICAL] tatacommunications.com (HTTPS): unreachable (http) (time to resolve 1.0 min)
  - INC000005 P3: [CRITICAL] cloudflare.com (HTTPS): unreachable (tcp) (time to resolve 1.0 min)
  - INC000006 P3: [CRITICAL] wikipedia.org (HTTPS): unreachable (http) (time to resolve 0.9 min)

## Watch list

- dns-site/dns: 2 failed samples (availability 98.319 %)
- site-gw/ping: 0.17 % ICMP loss (4 of 2380 echoes)
- wan-cloudflare/dns: 2 failed samples (availability 98.319 %)
- wan-cloudflare/ping: 1.68 % ICMP loss (40 of 2380 echoes)
- wan-cloudflare/ping: 4 failed samples (availability 98.319 %)
- wan-cloudflare/tcp: 2 failed samples (availability 98.319 %)
- wan-google/dns: 3 failed samples (availability 97.479 %)
- wan-google/ping: 1.81 % ICMP loss (43 of 2380 echoes)
- wan-google/ping: 4 failed samples (availability 98.319 %)
- wan-google/tcp: 2 failed samples (availability 98.319 %)
- wan-quad9/dns: 2 failed samples (availability 98.319 %)
- wan-quad9/ping: 2.27 % ICMP loss (54 of 2380 echoes)
- wan-quad9/ping: 5 failed samples (availability 97.899 %)
- wan-quad9/tcp: 2 failed samples (availability 98.319 %)
- web-cloudflare/http: 2 failed samples (availability 98.319 %)
- web-cloudflare/tcp: 3 failed samples (availability 97.479 %)
- web-google/http: 2 failed samples (availability 98.319 %)
- web-google/tcp: 2 failed samples (availability 98.319 %)
- web-tatacomm/http: 3 failed samples (availability 97.479 %)
- web-tatacomm/tcp: 2 failed samples (availability 98.319 %)
- web-wikipedia/http: 3 failed samples (availability 97.500 %)
- web-wikipedia/tcp: 2 failed samples (availability 98.319 %)

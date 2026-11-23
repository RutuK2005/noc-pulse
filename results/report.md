# noc-pulse report

Generated 2026-11-24 01:35:44 (local time) by `noc-pulse report --markdown`.

## Data coverage

| Item | Value |
|---|---|
| First sample | 2026-11-23 23:36:31 |
| Last sample | 2026-11-24 01:35:31 |
| Duration | 1.98 h |
| Samples (all probes) | 2738 |
| Samples by probe | dns: 476, http: 477, ping: 952, tcp: 833 |
| ICMP echoes sent / received | 9520 / 9379 |
| Collector gaps > 120 s | 0 (total 0 s) |

## Per-target KPIs

Latency for ping = every echo RTT; for tcp = connect time; dns = query time; http = time to first byte incl. DNS+TCP+TLS. Jitter = mean |dRTT| inside a ping burst.

| Target | Probe | Samples | Avail % | p50 ms | p95 ms | Jitter ms | Loss % | MOS | Min cert days |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dns-site | dns | 119 | 98.319 | 4.49 | 24.08 | - | - | - | - |
| site-gw | ping | 238 | 100.000 | 1.75 | 2.73 | 1.08 | 0.168 | 4.39 | - |
| wan-cloudflare | dns | 119 | 98.319 | 12.88 | 35.79 | - | - | - | - |
| wan-cloudflare | ping | 238 | 98.319 | 5.07 | 6.36 | 1.45 | 1.681 | 4.25 | - |
| wan-cloudflare | tcp | 119 | 98.319 | 6.12 | 8.95 | - | - | - | - |
| wan-google | dns | 119 | 97.479 | 14.98 | 29.87 | - | - | - | - |
| wan-google | ping | 238 | 98.319 | 5.40 | 6.55 | 1.07 | 1.807 | 4.24 | - |
| wan-google | tcp | 119 | 98.319 | 6.39 | 14.65 | - | - | - | - |
| wan-quad9 | dns | 119 | 98.319 | 14.94 | 74.40 | - | - | - | - |
| wan-quad9 | ping | 238 | 97.899 | 5.44 | 7.55 | 1.94 | 2.269 | 4.19 | - |
| wan-quad9 | tcp | 119 | 98.319 | 6.58 | 16.14 | - | - | - | - |
| web-cloudflare | http | 119 | 98.319 | 298.80 | 358.00 | - | - | - | 82.9 |
| web-cloudflare | tcp | 119 | 97.479 | 5.99 | 11.22 | - | - | - | - |
| web-google | http | 119 | 98.319 | 169.24 | 679.23 | - | - | - | 63.5 |
| web-google | tcp | 119 | 98.319 | 6.74 | 17.18 | - | - | - | - |
| web-tatacomm | http | 119 | 97.479 | 165.08 | 368.93 | - | - | - | 63.5 |
| web-tatacomm | tcp | 119 | 98.319 | 5.65 | 8.20 | - | - | - | - |
| web-wikipedia | http | 120 | 97.500 | 257.76 | 317.10 | - | - | - | 39.9 |
| web-wikipedia | tcp | 119 | 98.319 | 58.52 | 72.42 | - | - | - | - |

## Customer services vs SLA

Availability is per minute: a minute counts as up only if every probe of every member target succeeded in it.

| Service | Class | KPI source | Minutes | Avail % | p95 ms | Jitter ms | Loss % | MOS | SLA |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| acme-dia | silver | ping: wan-cloudflare, wan-google | 120 | 97.500 | 6.48 | 1.26 | 1.744 | - | BREACH: availability_pct, loss_pct |
| acme-voice | gold | ping: wan-cloudflare | 120 | 98.333 | 6.36 | 1.45 | 1.681 | 4.25 | BREACH: availability_pct, loss_pct |
| acme-dns | silver | dns: dns-site, wan-quad9 | 120 | 97.500 | 41.87 | - | - | - | BREACH: availability_pct |
| portal-tatacomm | bronze | http: web-tatacomm | 120 | 97.500 | 368.93 | - | - | - | BREACH: availability_pct |
| saas-web | bronze | http: web-google, web-cloudflare, web-wikipedia | 120 | 97.500 | 461.34 | - | - | - | BREACH: availability_pct |

## Ping p95 RTT (ms) / loss (%) per hour (local time)

| Hour | site-gw | wan-cloudflare | wan-google | wan-quad9 |
|---|---:|---:|---:|---:|
| 2026-11-23 23:00 | 2.9 / 0.64 | 7.2 / 0.00 | 6.6 / 0.00 | 7.8 / 0.64 |
| 2026-11-24 00:00 | 2.5 / 0.08 | 6.1 / 3.33 | 6.4 / 3.58 | 6.8 / 4.17 |
| 2026-11-24 01:00 | 3.0 / 0.00 | 6.2 / 0.00 | 7.0 / 0.00 | 10.2 / 0.14 |

## Alarms and tickets (from `noc-pulse replay` over the samples above)

- Alarms raised: 10 (suppressed by correlation: 4, flap re-raises: 0)
- By rule: packet_loss=3, unreachable=7
- Tickets opened: 6 (P1=2, P2=2, P3=2)
- Ticket states: Closed=3, Resolved=3
- MTTR (open -> Resolved): 49 s; MTTA: - (replay has no human acknowledgements, so MTTA is normally '-')

| # | Raised | Cleared | Target/probe | Rule | Severity | Suppressed (root #) | Ticket |
|---:|---|---|---|---|---|---|---|
| 1 | 2026-11-24 00:33:06 | 2026-11-24 00:35:06 | wan-quad9/ping | unreachable | critical | no | INC000001 |
| 2 | 2026-11-24 00:33:06 | 2026-11-24 00:35:36 | wan-quad9/ping | packet_loss | major | yes (#1) | - |
| 3 | 2026-11-24 00:33:17 | 2026-11-24 00:34:47 | wan-google/ping | unreachable | critical | no | INC000002 |
| 4 | 2026-11-24 00:33:17 | 2026-11-24 00:35:47 | wan-google/ping | packet_loss | major | yes (#3) | - |
| 5 | 2026-11-24 00:33:24 | 2026-11-24 00:34:54 | wan-cloudflare/ping | unreachable | critical | no | INC000003 |
| 6 | 2026-11-24 00:33:24 | 2026-11-24 00:35:24 | wan-cloudflare/ping | packet_loss | major | yes (#5) | - |
| 7 | 2026-11-24 00:34:06 | 2026-11-24 00:36:06 | wan-google/dns | unreachable | critical | yes (#3) | - |
| 8 | 2026-11-24 00:34:09 | 2026-11-24 00:36:09 | web-tatacomm/http | unreachable | critical | no | INC000004 |
| 9 | 2026-11-24 00:34:17 | 2026-11-24 00:36:17 | web-cloudflare/tcp | unreachable | critical | no | INC000005 |
| 10 | 2026-11-24 00:34:31 | 2026-11-24 00:36:31 | web-wikipedia/http | unreachable | critical | no | INC000006 |

## SD-WAN path selection (simulation over the measured ping series)

Paths: path-a = wan-cloudflare, path-b = wan-google, path-c = wan-quad9; primary = path-a; 119 decision intervals of 60 s; hold-down = 3 intervals.

| App class | SLA (RTT / jitter / loss) | Static primary | SLA-aware dynamic | Oracle (upper bound) | Switches static / dynamic | Per-path compliance |
|---|---|---:|---:|---:|---|---|
| voice | <=150 ms / <=30 ms / <=1 % | 98.32% (117/119) | 98.32% (117/119) | 98.32% | 0 / 0 | path-a 98.32%, path-b 97.48%, path-c 95.80% |
| video | <=200 ms / <=50 ms / <=2 % | 98.32% (117/119) | 98.32% (117/119) | 98.32% | 0 / 0 | path-a 98.32%, path-b 97.48%, path-c 95.80% |
| bulk | <=400 ms / any / <=5 % | 98.32% (117/119) | 98.32% (117/119) | 98.32% | 0 / 0 | path-a 98.32%, path-b 97.48%, path-c 96.64% |

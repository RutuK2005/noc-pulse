# noc-pulse learning guide

This guide is for someone who knows Python but not much networking, and who has to explain
noc-pulse - and the networking and service-management ideas behind it - in an interview
for a NOC / service-assurance role. It is long on purpose. Use the 7-day plan near the end
to work through it.

## How to use this guide

1. Read **The 60-second pitch** below and say it out loud.
2. Work through **Part A** (networking from zero) and **Part B/C** (ITIL, NOC shift work)
   at your own pace; Part D is a command reference you will keep coming back to.
3. Do the **Formulas** section with a calculator. Every number there is also checked by
   a unit test, so you can trust it.
4. Read the **file-by-file tour** with the code open next to it.
5. Read **What the real numbers mean** and learn `docs/FACTS.md`.
6. Follow the **7-day plan** and finish with the interview questions in **Part E** and
   **Project-specific questions** (75 questions in total).

Contents

* The 60-second pitch
* Part A - Networking from zero
* Part B - ITIL and service management for a NOC
* Part C - How a NOC shift works
* Part D - Troubleshooting toolkit
* Formulas with worked examples
* How noc-pulse works: a file-by-file tour
* Design decisions
* What the real numbers mean
* 7-day learn-it plan (with answers)
* Part E - Generic interview questions with model answers (Q1-Q55)
* Project-specific interview questions (P1-P20)

## The 60-second pitch

"I built noc-pulse, a small service-assurance toolkit in Python, to learn what a NOC
engineer actually does. It probes a set of customer-style services - my site gateway, DNS
resolvers, three public anycast addresses and four HTTPS sites including
tatacommunications.com - with ICMP, TCP, DNS and HTTPS checks, at a polite rate. From the
raw samples it computes availability, p50/p95 latency, jitter, loss and an ITU-T E-model
voice MOS, and checks each service against its SLA class. An alarm engine with hysteresis
and dependency-based correlation turns failures into alarms, so one root-cause fault opens
one ticket instead of an alarm storm. Tickets follow an ITIL lifecycle with P1-P4 priority
from an impact x urgency matrix, SLA timers, and L1 diagnostics - ping, traceroute, DNS,
TCP, HTTP - attached automatically. I ran it for 2.0 hours and wrote a
fault-injection demo that shows alarm, correlation, ticket, diagnostics and resolution end
to end. It also has an SD-WAN simulation comparing static routing with SLA-aware path
steering on the measured data, a Typer CLI, a Streamlit dashboard and 90 unit tests
in CI."

Things to be ready to say straight after the pitch: what the numbers were (see FACTS.md),
what you would do differently in production (P18), and what it does *not* do (README,
"Honest limitations").

**A note on probes and privileges (Part A, A4).** Raw ICMP sockets normally need
administrator/root rights. noc-pulse uses the `icmplib` library, which on Windows works for
a normal user (it goes through the operating system's ICMP support), and falls back to
running the system `ping` command and parsing its output if the socket cannot be opened.

**A note on MOS ceilings (Part A, A9).** The MOS scale itself goes up to 5, the E-model's
mapping caps at 4.5, and with the default G.107 parameters used in noc-pulse the best
possible estimate is 4.41 (R = 93.2). So "MOS 4.40" in the results means "as good as this
model can say".


## Part A - Networking from zero

This part builds the networking knowledge a NOC engineer uses every day, starting from nothing. Read it in order; each section assumes the earlier ones.

### A1. Layer models: OSI and TCP/IP

Networks are built in layers. Each layer does one job and relies on the layer below. When something breaks, the first question is "which layer?", because that tells you which team, tool and device to look at. The OSI model (7 layers) is mostly shared vocabulary; the TCP/IP model (4 layers, described in RFC 1122) is what is actually implemented.

```
 OSI layer          TCP/IP layer   What lives here                  Typical faults found here
 ---------------    ------------   ------------------------------   ----------------------------------------
 7 Application  \                  HTTP, DNS, SMTP, SIP, SNMP       DNS failure, HTTP 5xx, application crash,
 6 Presentation  >  Application    encoding, TLS encryption (*)     expired or mismatched certificate
 5 Session      /                  session setup and teardown
 4 Transport        Transport      TCP, UDP, port numbers           port blocked by a firewall, service not
                                                                    listening, retransmissions
 3 Network          Internet       IP addresses, ICMP, routing,     wrong IP/mask/gateway, missing route,
                                   routers, BGP, OSPF               BGP session down, ACL blocking
 2 Data link    \   Link           Ethernet frames, MAC addresses,  VLAN mismatch, switching loop, duplex
                 >                 switches, VLANs, ARP             mismatch, ARP problem, duplicate IP
 1 Physical     /                  fibre, copper, optics, radio,    cable cut, dirty or failed optic, low
                                   power, signal levels             light levels, CRC errors, power outage
```

(*) TLS runs on top of TCP and below HTTP. Books place it at layer 6 or call it "layer 4.5"; in an interview, say exactly that.

**Encapsulation.** Going down the stack, each layer wraps the data in its own header; the receiver unwraps in reverse.

```
 [ HTTP request ]                                        data      (L7)
 [ TCP header | HTTP request ]                           segment   (L4)  port numbers
 [ IP header | TCP header | HTTP request ]               packet    (L3)  IP addresses
 [ Eth header | IP | TCP | HTTP request | FCS ]          frame     (L2)  MAC addresses
 0110100101110...                                        bits      (L1)
```

**Why it matters.** If layer 1 is broken, nothing above works. If ping works, layers 1-3 work on that path; if ping works but the web page does not, the fault is at layer 4 or above. Engineers usually work bottom-up (link up? gateway reachable? destination reachable? port open? DNS resolves? application responds?), or start with a ping and move up or down from there.

In noc-pulse: the probe types map onto layers. ICMP ping tests layer 3 reachability, TCP connect tests layer 4, DNS tests name resolution, and HTTPS tests TLS and the application, so running several probes against one service shows which layer failed.

### A2. IP addressing, subnets, gateway, NAT and ARP

**IPv4 addresses** (RFC 791) are 32 bits, written as four octets (0-255), for example 192.168.10.37. Each has a network part (which network) and a host part (which device).

**Subnet mask and CIDR.** The mask says how many leading bits are the network part; CIDR notation (RFC 4632) writes that count after a slash.

```
 Address   192.168.10.37    = 11000000.10101000.00001010.00|100101
 Mask /26  255.255.255.192  = 11111111.11111111.11111111.11|000000
                              <------ network part: 26 bits ->|<- host part: 6 bits
```

Worked example, 192.168.10.37/26: 6 host bits give 2^6 = 64 addresses per subnet (blocks start at .0, .64, .128, .192). .37 is in the .0-.63 block, so the network is 192.168.10.0, the broadcast is 192.168.10.63, and the usable hosts are .1 to .62 (62 hosts).

| Prefix | Mask | Addresses | Usable hosts | Typical use |
|---|---|---|---|---|
| /32 | 255.255.255.255 | 1 | 1 | single host, router loopback |
| /31 | 255.255.255.254 | 2 | 2 | point-to-point link (RFC 3021) |
| /30 | 255.255.255.252 | 4 | 2 | classic point-to-point WAN link |
| /29 | 255.255.255.248 | 8 | 6 | small public block for a customer |
| /24 | 255.255.255.0 | 256 | 254 | typical LAN |

Usable hosts = 2^(32 - prefix) - 2, because the network and broadcast addresses are reserved (/31 and /32 are exceptions).

**Private and special addresses.**

| Range | Meaning |
|---|---|
| 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 | Private (RFC 1918); used inside organisations, not routed on the Internet |
| 100.64.0.0/10 | Shared space for carrier-grade NAT (RFC 6598); seen on some broadband and mobile links |
| 127.0.0.0/8 | Loopback (the machine itself) |
| 169.254.0.0/16 | Link-local; on Windows a 169.254.x.x address usually means DHCP failed (APIPA) |
| 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 | Documentation (RFC 5737); used in this guide's examples |

**Default gateway.** A host talks directly only to hosts in its own subnet; everything else goes to its default gateway, the router on its subnet.

```
 Host 192.168.10.37/26 wants to send to X
   Is X inside 192.168.10.0 - 192.168.10.63 ?
     yes -> find X's MAC address (ARP) and send directly
     no  -> find the gateway's MAC (e.g. 192.168.10.1) and send the packet to the gateway
```

A wrong mask or gateway gives a classic symptom: local devices work, but nothing outside (or only some remote networks).

**NAT.** Private addresses are not routed on the Internet, so the edge router rewrites them. With port address translation (PAT, the common form), many inside hosts share one public IP, told apart by port.

```
 Inside host              NAT router (public IP 203.0.113.5)           Server
 192.168.1.20:51000  -->  rewrites source to 203.0.113.5:40001  -->  198.51.100.80:443
 192.168.1.21:51000  -->  rewrites source to 203.0.113.5:40002  -->  198.51.100.80:443
                          translation table: 40001 -> .20:51000,  40002 -> .21:51000
```

Consequences: inbound connections need a port forward or static NAT; idle translations time out (UDP especially), silently dropping long-idle sessions; protocols that carry IP addresses inside the payload, such as SIP, can break and cause one-way audio; with carrier-grade NAT the customer's "public" address is shared.

**ARP (brief).** On a LAN, frames are delivered by MAC address. ARP (RFC 826) maps IP to MAC: the host broadcasts "Who has 192.168.10.1? Tell 192.168.10.37", the owner replies, and the answer is cached (`arp -a`). A missing or incomplete ARP entry for the gateway points to layer 2 (cable, VLAN, gateway down). Two MACs answering for one IP means a duplicate IP, which causes intermittent connectivity.

**IPv6 in brief.** IPv6 (RFC 8200) uses 128-bit hexadecimal addresses such as 2001:db8::10, normally without NAT. DNS returns them in AAAA records, ARP is replaced by Neighbor Discovery over ICMPv6, and ICMPv6 must not be blocked wholesale.

In noc-pulse: targets are hostnames or IPs. A private (RFC 1918) target can only be probed from inside the same private network or VPN; from the Internet the probe just times out.

### A3. Routing, BGP and anycast

**Routing table.** Every router and host has one. For each packet it uses the longest matching prefix (the most specific route).

```
 Destination        Next hop        Interface   Learned from
 0.0.0.0/0          203.0.113.1     wan0        static  (default route: "everything else")
 10.0.0.0/8         10.255.0.1      tun0        BGP
 10.20.30.0/24      -               lan0        directly connected
 10.20.30.128/25    10.20.30.2      lan0        static
```

A packet to 10.20.30.150 matches /0, /8, /24 and /25; the /25 wins. Each router decides independently, so the return path can differ from the forward path (asymmetric routing). Routes are static (configured) or dynamic. Inside one organisation, OSPF or IS-IS find shortest paths and converge quickly; between organisations, the Internet uses BGP.

**Autonomous system (AS).** A network, or group of networks, run by one organisation under one routing policy (an ISP, a cloud provider, a large enterprise), identified by an AS number. ASNs began as 16-bit and are now 32-bit; the private ranges 64512-65534 and 4200000000-4294967294 (RFC 6996) are used inside private networks, for example between a customer site and an MPLS provider.

**BGP in one page.** BGP-4 (RFC 4271) is a path-vector protocol. Configured neighbours (peers) talk over TCP port 179 and advertise the prefixes they can reach, with attributes; the key one is the AS_PATH, the list of ASes the route has crossed.

```
  AS 64496 (customer)        AS 64497 (ISP A)            AS 64498 (ISP B)
  +-----------+    eBGP    +-------------+    eBGP    +-------------+
  | CE router |------------| ISP A edge  |------------| ISP B edge  |---- 198.51.100.0/24
  +-----------+            +-------------+            +-------------+
  The customer learns 198.51.100.0/24 with AS_PATH "64497 64498"
  (ASNs 64496-64511 are reserved for documentation, RFC 5398)
```

BGP picks the best path by policy (local preference first, then AS_PATH length, then other tie-breakers), not raw speed. That is why ISPs use it: they need control over which neighbour to prefer and which routes to announce to whom, it scales to the whole Internet table, and it lets customers multihome to two providers. eBGP runs between ASes; iBGP carries the routes inside one AS.

What a NOC sees: a BGP session going down (on a Cisco-style router the neighbour shows Idle or Active instead of Established), which withdraws its routes so traffic fails or moves to a backup; sessions flapping because of an unstable link underneath; a prefix not advertised after a change, so one subnet is unreachable while everything else works; and, more rarely, route leaks or hijacks.

**Anycast.** Normally one IP means one machine. With anycast the same prefix is announced from many locations and BGP delivers each user to the nearest instance in routing terms. 1.1.1.1 (Cloudflare), 8.8.8.8 (Google) and the DNS root servers are anycast.

```
 user in city A  --->  instance at site A  \
 user in city B  --->  instance at site B   >  every instance answers as the same IP, e.g. 8.8.8.8
 user in city C  --->  instance at site C  /
```

It gives low latency and resilience (if a site fails, BGP sends users elsewhere). For monitoring: pings to 8.8.8.8 from two cities reach different machines, so their latencies are not comparable, and a sudden lasting step in latency often means routing moved you to another site, not that the service got slow.

In noc-pulse: public anycast resolvers make good "is the Internet reachable from here?" reference targets; when their latency jumps and stays at a new level, suspect a routing change before a fault.

### A4. ICMP, ping and traceroute

ICMP (RFC 792; ICMPv6 is RFC 4443) is IP's control and error protocol. It has no ports; messages have a type and a code.

| Type | Name | When it is sent |
|---|---|---|
| 8 | Echo Request | sent by ping |
| 0 | Echo Reply | the answer to ping |
| 3 | Destination Unreachable | code 0 network, 1 host, 3 port unreachable; 4 fragmentation needed but DF set; 13 administratively prohibited (filtered) |
| 11 | Time Exceeded | code 0: TTL reached zero in transit (used by traceroute) |

**Ping** sends Echo Requests and times the Echo Replies, giving round-trip time (RTT), loss and variation (jitter).

**TTL.** Every IP packet has an 8-bit Time To Live, set by the sender (commonly 64 on Linux/macOS, 128 on Windows, 255 on many network devices). Each router decrements it; at 0 the router drops the packet and returns ICMP Time Exceeded, so packets cannot loop forever. A ping reply's TTL is what was left on arrival, so TTL=57 probably started at 64 and crossed 7 routers. On Windows, "TTL expired in transit" usually means a routing loop.

**Why some hosts do not answer ping.** Firewalls drop ICMP Echo to hide hosts from scanners (Windows Defender Firewall blocks inbound echo by default on many machines), and routers rate-limit the ICMP they generate to protect their CPUs. So a failed ping does not prove a host is down (test the real service), and a working ping does not prove the service works.

**How traceroute works.** It sends probes with TTL = 1, 2, 3 and so on. The router where each probe's TTL hits zero returns Time Exceeded, revealing its address; the destination answers differently, which ends the trace.

```
 You ---- R1 ---- R2 ---- R3 ---- Server
 TTL=1  R1 decrements it to 0, drops it, returns Time Exceeded   -> hop 1 = R1
 TTL=2  R1 forwards it (TTL now 1); R2 drops it, Time Exceeded  -> hop 2 = R2
 TTL=3  R3 drops it, returns Time Exceeded                       -> hop 3 = R3
 TTL=4  reaches the Server, which answers (Echo Reply, or Port Unreachable for UDP probes)
```

Windows `tracert` sends ICMP Echo. Linux `traceroute` sends UDP to high ports (from 33434) by default; `-I` uses ICMP and `-T` uses TCP SYN (needs root), useful when UDP and ICMP are filtered. Each hop gets 3 probes by default; `*` means no reply to that probe. Part D covers reading the output.

In noc-pulse: the ping probe sends ICMP Echo Requests and records RTT and loss. Because ICMP can be filtered or rate-limited, a failed ping alone is weak evidence, which is why TCP, DNS and HTTPS probes exist too. Raw ICMP normally needs administrator/root rights; be ready to explain how the probe handles that.

### A5. TCP and UDP

**Ports.** An IP address identifies a host; a port (0-65535) identifies an application on it. A connection is identified by its 5-tuple (source IP, source port, destination IP, destination port, protocol). Servers listen on well-known ports; clients use random high "ephemeral" ports. Worth memorising: 22 SSH, 25 SMTP, 53 DNS (UDP and TCP), 80 HTTP, 123 NTP (UDP), 161/162 SNMP (UDP), 179 BGP, 443 HTTPS (UDP 443 carries HTTP/3), 3389 RDP, 5060/5061 SIP (5061 is SIP over TLS).

**TCP (RFC 9293)** provides a reliable, ordered byte stream, starting with a three-way handshake:

```
 Client                                   Server (listening on port 443)
   | ---- SYN (seq=x) ------------------->  |
   | <--- SYN-ACK (seq=y, ack=x+1) -------  |   <- the client's connect() completes here
   | ---- ACK (ack=y+1) ----------------->  |
   | ===== data flows in both directions == |
```

**Why TCP connect time is about 1 RTT.** The client's timer runs from sending the SYN to receiving the SYN-ACK: one round trip plus a tiny server kernel delay. So a TCP connect measures RTT even where ICMP is blocked, and also proves the port is open.

**Refused vs timed out.**

| What comes back | Client sees | Meaning |
|---|---|---|
| SYN-ACK | connected | port open, service listening |
| RST | "connection refused", fast (about 1 RTT) | host reachable, nothing listening on that port (or a device actively rejecting) |
| nothing | "timed out" after several retries | SYN dropped: silent firewall, host down, or broken routing ("filtered") |
| ICMP Destination Unreachable | "no route to host", fast | a router or firewall says it cannot or will not deliver |

"Refused" means the path works and the service is the problem; "timed out" points to a firewall or the network.

**Reliability.** Sequence numbers and acknowledgements; lost segments are resent after a retransmission timeout (RFC 6298 sets the initial RTO to 1 second and doubles it per retry, so one lost SYN adds about a second to setup); three duplicate ACKs trigger fast retransmit; the receiver's window provides flow control; congestion control treats loss as congestion and slows down. Two NOC consequences: loss makes TCP much slower, not just "missing a bit"; and one TCP stream cannot exceed window / RTT. A 64 KB window over 100 ms RTT gives 65,535 x 8 / 0.1 = about 5.2 Mbit/s regardless of link size. Window scaling (RFC 7323) lifts that limit, but high RTT still slows single downloads, so "my 1 Gbit/s link gives 50 Mbit/s to a distant server" is often not a fault. Connections close with FIN/ACK; TIME_WAIT afterwards is normal.

**UDP (RFC 768)** has an 8-byte header and no handshake, acknowledgements, retransmission or ordering; the application handles loss if it cares. It carries DNS, NTP, SNMP, syslog, voice and video (RTP) and QUIC (HTTP/3). For voice, a retransmitted packet would arrive too late to play, so UDP is right. UDP is harder to test: no handshake, open ports usually stay silent, closed ports may return ICMP Port Unreachable.

In noc-pulse: the TCP probe times the connect (about one RTT) and separates refused (RST) from timeout (filtered or unreachable). DNS queries normally use UDP port 53.

### A6. DNS

DNS (concepts RFC 1034, protocol RFC 1035) turns names into addresses and other data. It is a distributed, heavily cached database.

- **Stub resolver:** the DNS client in your OS; asks one configured server and trusts the answer.
- **Recursive resolver:** does the work (the ISP's, the corporate one, or public ones such as 1.1.1.1 and 8.8.8.8); walks the hierarchy and caches results.
- **Root servers:** 13 named identities (a.root-servers.net to m.root-servers.net), each served by many anycast instances; they know where each top-level domain is served.
- **TLD servers** (.com, .in, .org): know which servers are authoritative for each domain.
- **Authoritative servers:** hold the actual records of a zone such as example.com.

```
 PC (stub) --(1) "www.example.com A?"--> Recursive resolver (empty cache)
     --(2)--> Root server:      "Not me. .com is served by these servers."   (referral)
     --(3)--> .com TLD server:  "example.com is served by ns1.example.com."  (referral)
     --(4)--> ns1.example.com:  "www.example.com A 203.0.113.10, TTL 300"    (authoritative answer)
 PC <--(5) 203.0.113.10 -- the resolver returns it and caches it for 300 seconds
```

**Caching and TTL.** Each record has a TTL in seconds; resolvers and OSes cache answers that long. After a change, users keep getting the old answer until their cached copy expires; "DNS propagation" is really cache expiry. Negative answers are cached too (RFC 2308). `ipconfig /flushdns` clears only the local PC's cache, not the ISP resolver's.

| Type | Holds | Example |
|---|---|---|
| A | IPv4 address | www.example.com -> 203.0.113.10 |
| AAAA | IPv6 address | www.example.com -> 2001:db8::10 |
| CNAME | alias to another name (cannot coexist with other records, so not at the zone apex) | shop.example.com -> example.cdn-provider.example |
| MX | mail servers, with a preference (lower preferred) | example.com MX 10 mail1.example.com |
| NS | authoritative servers for a zone (delegation) | example.com NS ns1.example.com |
| PTR | reverse lookup, IP to name, under in-addr.arpa | 10.113.0.203.in-addr.arpa -> www.example.com |
| SOA, TXT, SRV | zone metadata; free text (SPF, verification); service location | |

| Result | Meaning | Where to look |
|---|---|---|
| NOERROR + answer | success | - |
| NOERROR, empty ("NODATA") | name exists, but no record of that type (e.g. no AAAA) | usually normal; check the type asked |
| NXDOMAIN | the name does not exist (authoritative "no") | typo, deleted record, wrong or expired domain |
| SERVFAIL | resolver could not get a valid answer | authoritative servers down or broken, bad delegation, DNSSEC failure; try other resolvers, `dig +trace` |
| REFUSED | server will not answer you | resolver does not serve your IP |
| timeout | no reply at all | resolver down, UDP/53 blocked, path broken |

DNS uses UDP 53, falling back to TCP 53 for large responses and zone transfers; encrypted variants are DNS over TLS (853) and DNS over HTTPS (443).

In noc-pulse: the DNS probe records resolution time and outcome. NXDOMAIN (wrong or removed name), SERVFAIL (broken resolution chain) and timeout (resolver unreachable) point to different fixes, so never lump them together as "DNS failed".

### A7. HTTP and TLS

**HTTP** is request/response (semantics RFC 9110; HTTP/1.1 RFC 9112, HTTP/2 RFC 9113, HTTP/3 RFC 9114). The client sends a method (GET, POST, HEAD), a path and headers (including `Host:`); the server returns a status code, headers and a body.

| Class | Meaning | Common codes |
|---|---|---|
| 1xx | informational | 101 Switching Protocols |
| 2xx | success | 200 OK, 204 No Content |
| 3xx | redirection | 301 Moved Permanently, 302 Found, 304 Not Modified |
| 4xx | client error | 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 429 Too Many Requests |
| 5xx | server error | 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout |

Any HTTP status at all means DNS, TCP and TLS worked. A 502 or 504 from a load balancer means the front end is reachable but its back end is not answering properly (502: invalid response; 504: no response in time); 503 usually means overload or maintenance; a 403 may come from a web application firewall or geo-block rather than the application.

**TLS** provides encryption, integrity and server authentication. A full TLS 1.3 handshake (RFC 8446) takes 1 RTT; TLS 1.2 (RFC 5246) takes 2. A brand-new HTTPS request:

```
 Client                                          Server
  |--- DNS lookup (0 ms if cached) ...                      DNS time
  |--- SYN -------------------------------------->|  \
  |<-- SYN-ACK -----------------------------------|  /  1 RTT   TCP connect
  |--- ACK + ClientHello (SNI, key share) ------->|  \
  |<-- ServerHello, Certificate, ..., Finished ---|  /  1 RTT   TLS 1.3 handshake (TLS 1.2 needs 2)
  |--- Finished + GET / ------------------------->|  \
  |<-- HTTP/1.1 200 OK  (first byte) -------------|  /  1 RTT + server processing time
```

On a 100 ms RTT path the first byte cannot arrive in under about 300 ms with TLS 1.3, or 400 ms with TLS 1.2, plus DNS and server time. That is why latency hurts web apps. Keep-alive, TLS session resumption and HTTP/3 (over QUIC, RFC 9000, which merges the transport and TLS handshakes into one RTT) reduce the cost.

**TTFB (time to first byte)**: definitions vary; curl measures from the very start, so it includes DNS, TCP and TLS. Say what yours includes. A large TTFB with a fast TCP connect points to a slow server or back end, not the network.

**Certificates.** An X.509 certificate (RFC 5280) binds hostnames (the Subject Alternative Names) to a public key, with an issuer, a validity period (notBefore / notAfter) and the issuer's signature.

```
 [ Root CA ]            self-signed; pre-installed in the OS or browser trust store
      | signs
 [ Intermediate CA ]    sent by the server together with the leaf
      | signs
 [ Leaf certificate ]   SAN: www.example.com    valid 2026-10-01 to 2027-04-18
```

The client checks the chain reaches a trusted root, every certificate is in date, and the hostname matches. Common failures: **expired** (or "not yet valid", often a wrong client clock); **name mismatch** (often the server's default certificate was presented); **incomplete chain** (no intermediate sent: browsers sometimes cope, but curl, Java and many apps fail with "unable to get local issuer certificate"); **untrusted or self-signed root**.

Expiry is becoming more frequent: under CA/Browser Forum ballot SC-081, the maximum lifetime of public TLS certificates fell from 398 to 200 days on 15 March 2026, and is scheduled to fall to 100 days in March 2027 and 47 days in March 2029. Automated days-to-expiry monitoring matters more every year.

**SNI** (Server Name Indication, RFC 6066) puts the hostname in the ClientHello, so one IP can host many sites with the right certificate for each. A client sending no SNI (old client, or testing by IP) gets the default certificate and a name mismatch, which is why `openssl s_client` needs `-servername`.

In noc-pulse: the HTTPS probe records status code, latency and days until certificate expiry; an expiring-certificate alarm is the classic "fix it before it is an outage" case.

### A8. Latency, jitter, packet loss and bandwidth

Think of a highway. **Bandwidth** is the number of lanes (capacity, bit/s). **Throughput** is how many cars actually get through (the achieved rate, at or below bandwidth). **Latency** is one car's travel time (ms; ping shows round-trip time, while one-way delay needs synchronised clocks). **Jitter** is how much travel time varies between cars. **Loss** is the share of cars that never arrive (%). A wider road does not make an empty road shorter: going from 50 to 100 Mbit/s does not reduce latency on an idle link.

**The four components of latency.**
1. **Propagation:** distance / signal speed. Light in fibre travels about 200,000 km/s (roughly two-thirds of c), about 5 microseconds per km, so 1,000 km costs about 5 ms one way, 10 ms round trip. Fixed for a path; only a shorter path helps.
2. **Serialization:** packet size / link rate. A 1,500-byte packet (12,000 bits) takes 12 ms at 1 Mbit/s, 0.12 ms at 100 Mbit/s, 0.012 ms at 1 Gbit/s. Matters on slow links.
3. **Queuing:** waiting in router buffers behind other packets. Zero when idle, grows with load; the main source of jitter and of "latency rises in business hours".
4. **Processing:** lookups, inspection, encryption. Microseconds on hardware routers; more on firewalls, VPN gateways and software devices.

Worked example: 2,000 km of fibre, 1,500-byte packets, 10 Mbit/s access link, idle network. Propagation = 2,000 x 5 microseconds = 10 ms; serialization on the access link = 12,000 / 10,000,000 s = 1.2 ms (fast core links are negligible); queuing about 0; processing under 1 ms. One-way about 11-12 ms, RTT about 23 ms. At busy hour, queuing can add tens of ms.

**Jitter.** Packets sent every 20 ms that arrive 20, 22, 35, 18 and 20 ms apart show jitter, caused mainly by changing queue lengths. There are several ways to compute it (RFC 3550 defines a smoothed estimate for RTP), so state which you use.

**Packet loss** comes from congestion (full queues drop packets), physical errors (a dirty optic or damaged cable corrupts frames, visible as CRC/input errors), policing above the contracted rate, radio interference (Wi-Fi, LTE) or misconfiguration (duplex mismatch). Loss cuts TCP throughput and causes gaps in voice. A 100 Mbit/s link may deliver 60 Mbit/s for innocent reasons too: overhead, TCP window versus RTT, a busy or distant test server, Wi-Fi limits, or a contended access line.

**Averages hide problems.** Ten pings at 20 ms and one at 900 ms average about 100 ms, which describes none of them. NOCs use percentiles: p50 (median) is the typical experience; p95 means 95% of samples were at or below that value.

In noc-pulse: the KPIs are availability, p50/p95 latency, jitter and loss computed from probe samples (formulas in the KPI section).

### A9. Why voice is so sensitive

A VoIP call has **signalling**, which sets calls up and tears them down (SIP, RFC 3261, ports 5060/5061), and **media**, the audio itself, carried in RTP (RFC 3550) over UDP, with RTCP sending periodic loss and jitter reports. A codec turns audio into bits; typically 20 ms of audio goes in each packet, so 50 packets per second flow each way.

| Codec | Bit rate | Payload per 20 ms | Approx. IP bandwidth per direction (20 ms packets) |
|---|---|---|---|
| G.711 | 64 kbit/s, uncompressed, best quality | 160 bytes | (160 + 40) bytes x 8 x 50 = 80 kbit/s |
| G.729 | 8 kbit/s, compressed, slightly lower quality | 20 bytes | (20 + 40) bytes x 8 x 50 = 24 kbit/s |
| Opus | variable, about 6-510 kbit/s (RFC 6716) | varies | adapts to network conditions |

The 40 bytes are the IP (20), UDP (8) and RTP (12) headers; Ethernet adds more (G.711 is often quoted at about 87 kbit/s per direction on Ethernet).

```
 mouth -> encode + packetize -> network (links, queues) -> jitter buffer -> decode -> ear
          |--- codec delay ---|--- network delay, jitter, loss ---|--- buffer delay ---|
          one-way "mouth-to-ear" delay = the sum of all of these
```

**Jitter buffer.** The receiver holds packets briefly and plays them out at a steady 20 ms rhythm. A bigger buffer absorbs more jitter but adds delay; packets arriving after their play-out time are discarded, so heavy jitter becomes loss. Adaptive buffers resize themselves.

**What each impairment sounds like.**
- **Delay:** people talk over each other. ITU-T G.114 (2003 edition) says one-way delay should not exceed 400 ms for general network planning, and notes (as earlier editions stated) that below about 150 ms most applications are not significantly affected; 150 ms one way is the figure voice engineers quote.
- **Jitter:** choppy, garbled audio (late packets discarded).
- **Loss:** clipped words, robotic sound. Packet-loss concealment hides small scattered losses; bursts are much worse.
- **Echo:** hearing your own voice back, from acoustic coupling or analog conversion points; removed by echo cancellers, and more annoying as delay grows.
- **One-way audio:** almost always NAT, firewall or routing; the RTP stream in one direction is blocked or sent to a private address.

**MOS.** Mean Opinion Score comes from listening tests (ITU-T P.800): 5 Excellent, 4 Good, 3 Fair, 2 Poor, 1 Bad, averaged. Around 4.0 or above is good and most users are satisfied; toward 3.5 and below, a noticeable share complain.

**The E-model (ITU-T G.107)** estimates quality from network and equipment parameters instead of listening tests. It starts from a best-case transmission rating, the R-factor (0-100), subtracts impairments for delay, codec and loss (among others), and maps R to an estimated MOS that tops out at about 4.5. R of about 80 corresponds to MOS of about 4.0 ("satisfied"); lower R bands mean growing dissatisfaction. Because it needs only latency, jitter and loss, monitoring tools use it to turn ping-style measurements into a voice-quality score.

In noc-pulse: the MOS KPI is an E-model-style estimate from measured latency, jitter and loss (formula and constants in the KPI section). It rates the path's suitability for voice; it does not measure real calls.

### A10. QoS basics

When a link is congested, packets queue; QoS decides who waits and who is dropped. It creates no bandwidth and does nothing on an uncongested link. It earns its keep at congestion points, typically the WAN edge where a 1 Gbit/s LAN meets a 50 Mbit/s circuit.

1. **Classify** traffic (by port, application, source or existing marking).
2. **Mark** it with a DSCP value, a 6-bit field in the IP header (RFC 2474), so later devices treat it correctly.
3. **Queue and schedule:** voice goes into a strict-priority queue (sent first), policed to a maximum so it cannot starve others; other classes get weighted shares.
4. **Drop policy:** for TCP-heavy classes, drop early and randomly (WRED) rather than waiting for a full queue.
5. **Police or shape:** policing drops or re-marks traffic above a rate; shaping buffers the excess to smooth it.

| DSCP name | Value | Commonly used for |
|---|---|---|
| EF (Expedited Forwarding, RFC 3246) | 46 | voice media (RTP) |
| AF41 | 34 | interactive video |
| CS3 | 24 | call signalling (a common choice; models vary) |
| AF21 | 18 | important business data |
| Default | 0 | best effort |

Exact classes follow the provider's or customer's QoS model. The key limit: the public Internet generally does not honour your DSCP marks end to end, while MPLS providers offer QoS classes by contract, a big reason voice often runs on MPLS. SD-WAN over the Internet compensates with path selection rather than guarantees.

In noc-pulse: probes see the result of QoS (latency, jitter, loss on the path) but do not configure it.

### A11. WAN types: MPLS, Internet and LTE/5G

- **CPE (customer premises equipment):** any device at the customer site (router, modem, ONT, SD-WAN box), often provider-managed.
- **CE (customer edge) router:** the customer-side router connecting to the provider.
- **PE (provider edge) router:** the provider's router in its point of presence (PoP), where customer circuits terminate.
- **P (provider) router:** core router; only switches labels and knows nothing of customer routes.
- **Last mile / local loop:** the access circuit from PoP to building (fibre, copper or radio), often from a local carrier.

```
 Site A                          Provider MPLS network                            Site B
 [LAN]-[CE]---access---[PE1]=====[P]===========[P]=====[PE2]---access---[CE]-[LAN]
        eBGP or static  VRF "CUST-X"   label-switched core    VRF "CUST-X"  eBGP or static
```

**MPLS L3VPN (RFC 4364):** the provider carries the customer's private routes in its own network. Each customer has a VRF (separate routing table) on the PE, so customers stay isolated even with overlapping private addresses; the core forwards by labels; CE and PE exchange routes with eBGP or static routes.

**DIA (Dedicated Internet Access):** dedicated, usually symmetric, uncontended bandwidth, static public IPs, SLA on availability and repair time. **Broadband:** shared (contended), often asymmetric, best effort, cheap, little or no SLA. **LTE/5G:** wireless, quick to deploy, ideal for backup; latency and throughput vary with signal and cell load; often behind carrier-grade NAT; may have data limits.

| | MPLS L3VPN | DIA | Broadband | LTE/5G |
|---|---|---|---|---|
| Path | private provider network | public Internet | public Internet | Internet via mobile network |
| Performance | predictable, SLA-backed | good, SLA on access | best effort | variable |
| QoS classes | yes | not end to end | no | no |
| Cost per Mbit/s | high | medium | low | medium, may be metered |
| Typical role | site-to-site, voice, critical apps | primary Internet, cloud, SD-WAN underlay | small sites, SD-WAN underlay | backup, temporary sites |

MPLS is private but not encrypted by default; Internet underlays are normally protected with IPsec.

In noc-pulse: dependency-based correlation maps onto this chain. If a site's CE/CPE is unreachable, everything behind it fails too, so those alarms are symptoms, not separate faults.

### A12. SD-WAN

**The problem.** MPLS is reliable but expensive and slow to provision; Internet is cheap but best effort; classic routing only notices a link that is completely down, so a link losing 5% of packets still looks healthy.

**How it works.** An SD-WAN edge at each site builds encrypted overlay tunnels (usually IPsec) over every underlay (MPLS, DIA, broadband, LTE) to other sites, hubs and cloud gateways. A central controller/orchestrator pushes configuration and policy and collects visibility. Edges continuously probe every tunnel (often about once a second) for latency, jitter and loss. With **application-aware routing**, the edge identifies each application (first packets, ports, domain names), maps it to an SLA class, and sends it over a tunnel currently meeting that class's thresholds.

```
                 +----------------------------------+
                 |    Controller / orchestrator     |   policy, configuration, visibility
                 +----------------+-----------------+
                                  |  control channel to every edge
   Site A edge  ==== tunnel 1 over MPLS ============  Site B edge or hub
                ==== tunnel 2 over Internet (DIA) ===
                ==== tunnel 3 over LTE ==============
         probes run through every tunnel -> latency, jitter and loss per path
```

| Example SLA class (illustrative) | Latency | Jitter | Loss | Applications |
|---|---|---|---|---|
| Real-time | <= 150 ms | <= 30 ms | <= 1% | voice and video calls |
| Business-critical | <= 250 ms | - | <= 2% | ERP, VDI, CRM |
| Bulk / default | best effort | - | - | backups, updates (cheap paths, avoid metered LTE) |

**BFD** (Bidirectional Forwarding Detection, RFC 5880) is a lightweight hello protocol for fast failure detection: both ends send small packets at a negotiated interval, and the session is declared down after a set number are missed (the detect multiplier). For example 300 ms x 3 detects a failure in about 0.9 s, versus tens of seconds with routing timers. Many SD-WAN products run BFD or vendor-specific probes inside each tunnel for liveness and quality.

**Blackout vs brownout.** A blackout is a dead path: BFD detects it and traffic fails over in about a second. A brownout is a path that is up but degraded (say 4% loss or 300 ms latency): routing still uses it and only quality probes notice. Handling brownouts is SD-WAN's main advantage.

**Stability.** Switching on every bad sample makes traffic bounce between paths (flapping), and each switch can reorder packets, reset NAT/firewall state and glitch voice. Protections: an **averaging window** (judge over several seconds); **hysteresis** (for example leave when loss exceeds 2%, return only when it falls below 1%); a **hold-down timer** (stay a minimum time after switching, and require several consecutive good intervals before moving back); **damping** (penalise a path that keeps flapping).

In noc-pulse: sdwan.py simulates this path decision over measured path data. Explain it with this vocabulary: SLA class, brownout versus blackout, hysteresis and hold-down against flapping.

### A13. SLA, SLO, SLI and KPI

| Term | What it is | Example |
|---|---|---|
| SLI (service level indicator) | a measured number | % of one-minute intervals in which the HTTPS check succeeded; p95 RTT site A to data centre this month |
| SLO (service level objective) | internal target for an SLI | HTTPS availability >= 99.95% per month; p95 RTT <= 60 ms |
| SLA (service level agreement) | contract with the customer: commitment, measurement method, exclusions, remedy | 99.9% monthly availability per site excluding planned maintenance, service credits if missed; P1 restoration within 4 h |
| KPI (key performance indicator) | any metric of how well a team or service performs, contractual or not | MTTR, % tickets resolved within SLA, first-time-fix rate, backlog |

The SLO is set tighter than the SLA so the team reacts before the contract is breached (the gap between 100% and the SLO is sometimes called the error budget). Always check the details: calendar month or rolling 30 days, exclusions (planned maintenance, customer-caused outages), and the definition of "down".

### A14. Availability "nines"

Allowed downtime = (1 - availability) x period. With a 30-day month (43,200 min) and 365-day year (525,600 min):

| Availability | Downtime fraction | Per 30-day month | Per 365-day year |
|---|---|---|---|
| 99% | 1% | 432 min = 7 h 12 min | 87.6 h = 3 days 15 h 36 min |
| 99.5% | 0.5% | 216 min = 3 h 36 min | 43.8 h = 1 day 19 h 48 min |
| 99.9% | 0.1% | 43.2 min = 43 min 12 s | 8.76 h = 8 h 45 min 36 s |
| 99.95% | 0.05% | 21.6 min = 21 min 36 s | 4.38 h = 4 h 22 min 48 s |
| 99.99% | 0.01% | 4.32 min = about 4 min 19 s | 52.56 min = about 52 min 34 s |
| 99.999% | 0.001% | about 25.9 s | 5.26 min = about 5 min 15 s |

Check: 0.1% x 43,200 min = 43.2 min. A 31-day month gives 44.6 min, so ask which month length the contract uses. Worked example: 50 minutes down in a 30-day month gives (43,200 - 50) / 43,200 = 99.884%, which misses a 99.9% SLA.

**Series and parallel.** Components in series (all needed) multiply: CPE 99.9% x access circuit 99.9% = 99.8%. Redundant paths in parallel (either is enough): 1 - (1 - A1) x (1 - A2), so 99.9% MPLS plus a 99% LTE backup gives 1 - (0.001 x 0.01) = 99.999%, but only if failures are independent. Shared power, a shared building entry or a shared CPE break that assumption, so real redundancy never quite matches the arithmetic.

## Part B - ITIL and service management for a NOC

ITIL is a widely used framework of good practice for IT service management. ITIL 4 calls each area a "practice" (incident management, problem management, change enablement, service request management, monitoring and event management, service desk). Use the terms precisely; you do not need word-for-word definitions.

### B1. The vocabulary

| Term | Meaning (paraphrasing ITIL) | NOC example |
|---|---|---|
| Event | a change of state that matters for managing a service or device | "Interface Gi0/1 on CPE-12 is up" |
| Alarm / alert | an event needing attention: a failure or a threshold crossed | "CPE-12 unreachable for 3 polls" |
| Incident | unplanned interruption to a service, or reduction in its quality | "Pune branch cannot reach head-office apps" |
| Major incident | an incident so severe it gets a separate, faster procedure | core router outage hitting many customers |
| Problem | the cause, or potential cause, of one or more incidents | "Why do sites behind one PE drop every week?" |
| Known error | a problem with known cause or workaround, not yet permanently fixed | "CPE firmware X drops its WAN session; workaround: reset it" |
| Workaround | reduces the impact until the real fix is available | fail over to the LTE backup |
| Change | adding, modifying or removing anything that could affect services | router software upgrade in a maintenance window |
| Service request | a normal, pre-agreed request; nothing is broken | monthly utilisation report, new portal user |

Changes are **standard** (pre-approved, routine, low risk), **normal** (assessed and approved first) or **emergency** (urgent, for example to restore service, with fast-track approval).

Say the distinctions clearly: an **incident** is about restoring service fast; a **problem** is about removing the cause so incidents stop (one problem can sit behind many incidents). An incident means something is broken; a **service request** means something is wanted. Monitoring produces many **events**, a few become **alarms**, and an alarm becomes an **incident** when there is real or likely service impact that someone must act on. A **major incident** gets a named major incident manager, a bridge call, frequent scheduled updates, senior escalation and a mandatory post-incident review.

### B2. Priority: impact x urgency

**Impact:** how many users, sites or customers, and how critical the service. **Urgency:** how fast it must be fixed (is there a workaround, is damage growing, is there a deadline?). Example matrix (each organisation and contract defines its own):

| | Urgency High | Urgency Medium | Urgency Low |
|---|---|---|---|
| **Impact High** | P1 | P2 | P3 |
| **Impact Medium** | P2 | P3 | P4 |
| **Impact Low** | P3 | P4 | P4 |

Illustrative definitions with response / restore targets (real ones come from each contract): **P1** whole site or service down, no working backup (15 min / 4 h); **P2** site on backup with degraded performance, or one critical app down (30 min / 8 h); **P3** intermittent issue for a few users, workaround exists (2 h / 24 h); **P4** one user, cosmetic, or information request (8 h / 3 business days).

Justify the priority in the ticket with facts: "Impact high: all 120 users at the Pune site, all applications. Urgency high: no workaround, LTE backup also down, business hours. P1." Re-prioritise when facts change and record why (backup fails too: P2 becomes P1; workaround restores service: urgency drops).

### B3. Ticket lifecycle

```
  New --> Acknowledged --> In Progress ----------------> Resolved --> Closed
                             |      ^                       |
                             v      |                       | customer: "still broken"
                           Pending -+                       v
           (waiting for the customer, a vendor,          Reopened --> back to In Progress
            or an approved change window; reason recorded)
```

New: nobody owns it yet. Acknowledged: an engineer has taken ownership (satisfies the response clock). Resolved: service restored and fix documented, awaiting confirmation. Closed: final. States drive the SLA clocks, show ownership, feed MTTA/MTTR reports, make handover possible and stop tickets being forgotten. The Resolved-to-Closed gap lets a customer say "still broken" without a new ticket.

### B4. SLA timers, stop-the-clock and escalation

**Response time** runs from ticket creation (or alarm) to acknowledgement or first meaningful response; **resolution (restoration) time** runs until service is restored (or fully resolved), as the contract defines.

**Stop-the-clock.** The resolution clock may pause while the provider genuinely cannot proceed because of the customer (no site access, customer power off, contact unreachable, customer asks to delay testing) and restarts when the customer responds. Pause only for reasons the contract allows, with the reason and exact timestamps recorded. Waiting for your own carrier, vendor or field engineer usually does not stop the clock, since the provider answers for its subcontractors. Using pauses to hide breaches is an integrity problem.

**Breach handling.** Tools warn as a ticket nears its target (for example at 50/75/90% of the time used). Escalate before the breach, not after; if it happens, record why, tell the customer honestly, and expect it in the SLA report and possibly service credits.

**Escalation.** **Functional** escalation moves work to more expertise or access (L1 -> L2 -> L3, the carrier, a vendor's support centre, field team). **Hierarchical** escalation goes up the management chain (shift lead, NOC manager, service delivery manager) for decisions, resources, customer management or an imminent breach. A P1 usually needs both.

### B5. MTTA, MTTR and MTBF

- **MTTA:** mean time from alarm or ticket creation to acknowledgement; measures NOC responsiveness.
- **MTTR** is ambiguous: mean time to **repair** (fix the fault), **restore/recover** (service back, maybe on a workaround), **resolve** (ticket resolved and confirmed), or even **respond**. Always say which; customers care most about restore.
- **MTBF:** mean working time between failures = total uptime / number of failures (MTTF is for parts that are replaced, not repaired).
- **Availability** = MTBF / (MTBF + MTTR).

Worked example: in a 30-day month (720 h) a circuit failed 3 times, for 2 h, 1 h and 3 h; alarms were acknowledged after 4, 6 and 11 minutes. MTTA = (4 + 6 + 11) / 3 = 7 min. MTTR (restore) = 6 / 3 = 2 h. Uptime = 714 h, so MTBF = 714 / 3 = 238 h. Availability = 238 / (238 + 2) = 99.17%, which equals 714 / 720.

### B6. Root cause analysis, 5 whys and known errors

RCA finds why an incident happened so it does not recur, using a timeline (what happened, and what changed just before), the 5 whys, or a fishbone (Ishikawa) diagram of causes by category.

1. Why was site 12 down? The CPE router lost power.
2. Why? The rack UPS ran out during a mains power cut.
3. Why? Its batteries lasted 5 minutes instead of 60.
4. Why? They were five years old and never replaced.
5. Why? There was no preventive-maintenance schedule for branch UPS units.

Root cause: the missing process. Actions: replace batteries (corrective), create a schedule (preventive), monitor battery health (detective). Good RCA stops at a cause you can act on, allows for several causes, and is blameless (fix processes, not people).

**KEDB** (Known Error Database): known errors with symptoms, cause and workaround, so L1 can spot a repeat and restore service in minutes. **Workaround vs fix:** failing over to LTE restores service, but the fibre fault (and maybe a problem record) stays open until the permanent fix. **RFO** (reason for outage): the customer-facing summary of what happened, timeline, root cause, resolution and preventive actions.

### B7. Correlation, parent/child incidents, de-duplication and flapping

One fault can produce hundreds of "unreachable", "BGP down" and "high loss" alarms within seconds (an alarm storm). Correlation reduces them to the one that matters.

```
 PE-router-7 down (root cause)             -> parent incident INC-1000, P1
   |-- Customer A, Pune site unreachable    -> child INC-1001, linked
   |-- Customer B, Nashik site unreachable  -> child INC-1002, linked
   |-- Customer C, Satara site unreachable  -> child INC-1003, linked
 Work happens on the parent; updates and the resolution cascade to the children.
```

- **Topology/dependency-based correlation:** when an upstream device is down, alarms from everything behind it are marked as symptoms (or suppressed) and linked to the root alarm.
- **Time- and rule-based correlation:** alarms starting within a short window are probably related; "link down" from both ends of one circuit is one fault.
- **De-duplication:** repeats of the same alarm on the same object become one alarm with a count and first/last-seen times.
- **Flapping detection:** an object changing state many times in a window (say 5 transitions in 10 minutes) is flagged; individual notifications are suppressed and one flapping alarm is raised. Usual causes: layer 1 (dirty optic, loose connector, unstable power or radio) or an oscillating routing session.
- **Hysteresis:** raise and clear at different thresholds, or require consecutive samples, so a value hovering near a threshold does not toggle the alarm.

Example: raise at loss >= 3% for 2 consecutive samples; clear at <= 1% for 2 consecutive samples.

```
 sample loss %:  0.5   3.5      3.2      2.4   1.8   2.6   0.8       0.6
 alarm state:    OK    OK       RAISED   on    on    on    on        CLEARED
                       1st bad  2nd bad                    1st good  2nd good
```

With one 3% threshold and no hysteresis, the same data would raise at 3.5, clear at 2.4, and re-raise every time loss crossed 3%.

## Part C - How a NOC shift works

This is how NOCs commonly operate in general; each company has its own tools and procedures, learned in training.

### C1. Shift handover

Handover is where work gets lost if done badly. Do it in writing (log or ticket tool) and verbally, and have the incoming engineer confirm ownership of each item.

```
 HANDOVER  08-Dec 21:00 IST   Shift B -> Shift C
 Open P1/P2 : INC-1000 PE-router-7 down (parent of 3). Carrier ticket CR-55, ETA 23:00. Bridge open.
 Pending    : INC-0987 waiting for site access 09:00 tomorrow (clock paused 18:40, reason recorded).
 Changes    : CHG-210 core upgrade 02:00-04:00. Expect alarms on PE-3/PE-4; check them against the change.
 Watch list : Site-44 LTE backup flapped 6 times since 17:00. No customer impact yet.
 Callbacks  : Customer B expects an update at 22:00.
```

Cover open high-priority incidents (next steps, next update times), tickets waiting on others (with ETAs), planned changes, anything flapping or trending badly, and promised callbacks. Never hand over work that exists only in your head.

### C2. The monitoring console

Typical screens: the **alarm list** (time, object, alarm type, severity Critical/Major/Minor/Warning/Cleared, acknowledged by, linked ticket); a **topology map** coloured by state, showing whether a problem is one site or a region; **KPI dashboards** (latency, loss, jitter, utilisation per circuit; availability against SLA); the **ticket queue** with SLA countdowns; the **change calendar**. Watch for new criticals, clusters (many alarms in one region usually share one cause), slow trends (latency creeping up every afternoon suggests congestion) and tickets near breach. Check raw data before concluding from a dashboard.

### C3. L1, L2 and L3

- **L1:** watches the console; acknowledges and triages alarms; first checks (ping, traceroute, interface and session status); creates and updates tickets; first customer contact; follows runbooks; escalates with evidence.
- **L2:** deeper device troubleshooting (configuration, routing, logs); coordinates carriers and field engineers; complex or repeat issues; some changes.
- **L3:** engineering specialists and vendors: design faults, software bugs, complex routing/security issues, RCA and permanent fixes.

Trainees usually start with L1 work and grow into L2. The most valued L1 skill is escalating with a clear summary and evidence, so the next level does not start from zero.

### C4. Triage checklist for a new alarm

1. **Acknowledge** to show ownership (stops the MTTA clock).
2. **Read it:** which object, what condition, since when, what severity.
3. **Is it real?** Flapping or already cleared? Confirm from a second source (another probe, device CLI, another vantage point).
4. **Is it planned?** Check the change calendar and maintenance windows.
5. **Scope:** one target, site, customer, region, or many? Look for related or upstream alarms.
6. **Impact and priority:** which service, how many users, is a backup carrying traffic?
7. **Quick diagnostics:** ping, traceroute, TCP, DNS, HTTP; interface status, errors and routing sessions where you have access.
8. **Ticket:** create one or link to an existing/parent incident; record findings with timestamps.
9. **Notify and escalate** as the contract and severity require.
10. **Follow through:** set the next update time, watch until stable, confirm with the customer, close.

### C5. Ticket hygiene

The ticket is the official record; the next shift, a manager or an auditor must understand it without asking you.

```
 21:04 IST  Alarm "Site-12 CPE unreachable" acknowledged. Ping from NOC probe 0/10. LTE backup tunnel also down.
 21:08 IST  No change scheduled. PE reachable; other sites on the same PE normal -> isolated to Site-12.
 21:12 IST  Called site contact (details in contact field). Mains power cut at site since about 20:55.
            Clock paused 21:12, reason: customer power, as allowed by contract.
 21:38 IST  Site reports power restored 21:37. CPE reachable 21:38, 0% loss over 100 pings. Clock resumed.
 21:45 IST  Customer confirms all services working. Resolved; cause: customer power. Awaiting closure.
```

Timestamp every entry with the time zone. Record facts and evidence, pasting the relevant output ("ping ok" is not evidence). Keep internal and customer-visible notes separate, with no blame or speculation in customer-visible ones. Record every contact attempt, carrier reference and ETA; update the status when it changes; choose the correct cause code at closure, because reports and problem management depend on it.

### C6. Bridge calls for major incidents

Roles: an **incident manager** runs the call and decides; **technical leads** cover each area; a **scribe** keeps the timeline of facts, actions and decisions; a **communications lead** sends customer and management updates. Discipline: one conversation at a time; facts with timestamps; every action has an owner and report-back time; announce changes before making them so two people never change things at once; summarise for late joiners; send updates at the promised interval even with no news ("still investigating; next update 22:30"); close only after restoration is confirmed, then schedule the post-incident review.

### C7. Talking to customers

Collect: name, phone, email and an alternate contact; the **circuit or service ID**, site address, CPE name or IP; the exact symptom and error messages ("cannot reach SAP" beats "network down"); **start time with time zone**, constant or intermittent, any pattern; **scope** (all users or some, one site or many, wired and Wi-Fi, one application or all); recent changes on their side; CPE power and status lights; **evidence** (ping and traceroute to specific targets, a wired speed test with server and time noted; for voice, example calls with times, numbers and direction); site access hours and a contact for a field visit; business impact and any workaround.

How to speak: listen first, acknowledge the impact, avoid jargon and blame, say what you will do and when the next update comes, and never promise what you do not control ("the carrier's estimate is 23:00", not "it will be fixed by 23:00"). Follow up calls in writing.

### C8. Field engineers, carriers and the last mile

- **Carrier / last-mile provider:** raise a ticket with the circuit ID and evidence (interface down since a timestamp, loss figures, optical levels if available). Record their reference and ETA, chase at agreed intervals, and use their escalation matrix if the ETA slips. Carriers may run loop tests to locate the fault.
- **Field engineer dispatch:** when remote checks point to on-site hardware, cabling or power. Give them the facts, site contact, access approval and spares (for example a replacement CPE or optic), and stay available to test with them on site.

### C9. Closing a ticket

Before resolving, monitoring should show the service stable for a reasonable period and the customer should confirm; if they cannot be reached, follow the policy (for example a set number of attempts over a set number of days before auto-closure). The resolution note states the cause, the fix, the restoration time, workaround or permanent fix, and any linked problem record. Provide an RFO if the customer or contract requires it.

## Part D - Troubleshooting toolkit

### D1. Quick reference

| Task | Windows | Linux |
|---|---|---|
| IP, gateway, DNS servers | `ipconfig /all` | `ip addr`, `ip route`, `resolvectl status` |
| Ping N times / continuously | `ping -n 10 host` / `ping -t host` | `ping -c 10 host` / `ping host` |
| Payload size + don't-fragment | `ping -f -l 1472 host` | `ping -M do -s 1472 host` |
| Trace route | `tracert -d host` | `traceroute -n host` (`-I` ICMP, `-T -p 443` TCP) |
| Per-hop loss over time | `pathping -n host` | `mtr -rwn -c 100 host` |
| DNS lookup | `nslookup name [server]`, `Resolve-DnsName` | `dig name [@server]` |
| Flush local DNS cache | `ipconfig /flushdns` | `resolvectl flush-caches` |
| Routing table | `route print` | `ip route`, `ip route get <ip>` |
| ARP / neighbours | `arp -a` | `ip neigh` |
| Sockets and listeners | `netstat -ano` | `ss -tulpn`, `ss -tan` |
| TCP port test | `Test-NetConnection host -Port 443` | `nc -vz -w 3 host 443` |
| HTTP and TLS | `curl.exe -v`, `curl.exe -w ...` | `curl -v`, `curl -w ...` |
| Certificate | `openssl s_client` (if installed) | `openssl s_client` |

In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest`; type `curl.exe` for the real curl shipped with Windows 10 and 11.

### D2. ping

```
C:\> ping -n 4 198.51.100.10
Reply from 198.51.100.10: bytes=32 time=12ms TTL=57
Reply from 198.51.100.10: bytes=32 time=13ms TTL=57
Request timed out.
Reply from 198.51.100.10: bytes=32 time=12ms TTL=57
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
    Minimum = 12ms, Maximum = 13ms, Average = 12ms
```

- `Reply from X ... time=12ms`: reachable, RTT 12 ms.
- `Request timed out.`: no reply within the timeout (4 s default on Windows): lost, filtered or host down.
- `Reply from 192.168.1.1: Destination host unreachable.`: a router (or your own PC) could not deliver, often an ARP failure on the last segment. The reply comes from a different IP, and Windows counts it as "Received", so the loss figure can misleadingly read 0%.
- `TTL expired in transit.`: probably a routing loop.
- `General failure.`: local problem (no route, adapter disabled, local firewall or VPN client).
- `Ping request could not find host X.`: DNS failed; try the IP.

On Linux, gaps in `icmp_seq` show which packets were lost, and `rtt min/avg/max/mdev` gives `mdev`, a quick jitter indicator. Use 100+ pings to measure loss (4 pings cannot show 1%), and ping the gateway, then the first ISP hop, then the destination to localise a problem.

**MTU test.** ICMP payload + 8 (ICMP header) + 20 (IPv4 header) = packet size, so a 1,472-byte payload makes a 1,500-byte packet.

```
C:\> ping -f -l 1472 198.51.100.10
Packet needs to be fragmented but DF set.
C:\> ping -f -l 1464 198.51.100.10
Reply from 198.51.100.10: bytes=1464 time=13ms TTL=57
```

The largest size that works is 1,464, so path MTU = 1,464 + 28 = 1,492 bytes, typical of PPPoE; around 1,400 suggests a tunnel (GRE, IPsec). On Linux, `ping -M do -s 1472 host` reports "message too long" or "Frag needed". If large DF pings simply time out while small ones work, something drops big packets without sending the ICMP error, itself a clue.

### D3. Reading a traceroute correctly

```
C:\> tracert -d 198.51.100.10
  1     1 ms     1 ms     1 ms  192.168.1.1
  2     8 ms     7 ms     9 ms  100.64.12.1
  3     9 ms    10 ms     9 ms  203.0.113.17
  4     *        *        *     Request timed out.
  5    48 ms    11 ms    10 ms  203.0.113.90
  6    12 ms    11 ms    12 ms  198.51.100.1
  7    12 ms    12 ms    11 ms  198.51.100.10
Trace complete.
```

This trace is healthy. Hop 4's `*` with later hops answering means that router does not send (or rate-limits) Time Exceeded. Hop 5's single 48 ms is the router being slow to generate ICMP (low priority for its CPU), not slow forwarding, because later hops are fast. Private addresses inside the path (100.64.x.x, 10.x.x.x) are normal inside ISP networks.

**The rule:** only latency or loss that starts at a hop and continues through every later hop, including the destination, is real. Something at one middle hop that disappears afterwards is almost always ICMP rate limiting.

```
  4    10 ms    10 ms    10 ms  203.0.113.17
  5    95 ms    97 ms    96 ms  203.0.113.90
  6    96 ms    98 ms    95 ms  198.51.100.1
  7    97 ms    96 ms    99 ms  198.51.100.10
```

This is real: about 85 ms is added between hops 4 and 5 and persists to the destination. If geography explains it (say an undersea segment) and it is constant all day, it is propagation; if it rises and falls with the time of day, it is queuing.

Other traps: `* * *` on every hop after some point means the destination or its firewall drops the probes, so test the real service (`Test-NetConnection -Port 443`, `traceroute -T -p 443`). The return path may differ from the forward path, and each hop time includes the reply's trip back, so trace from both ends if you can. Some MPLS cores hide hops. Use `-d`/`-n` to skip slow reverse-DNS lookups.

### D4. pathping and mtr

Both combine traceroute with repeated pings to every hop.

```
$ mtr -rwn -c 100 198.51.100.10
HOST: noc-probe-01            Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 192.168.1.1            0.0%   100    0.6   0.7   0.5   1.9   0.2
  2.|-- 100.64.12.1            0.0%   100    7.9   8.3   7.1  15.2   1.1
  3.|-- 203.0.113.17          40.0%   100    9.4   9.8   8.9  14.1   0.8
  4.|-- 203.0.113.90           0.0%   100   10.2  10.6   9.8  18.4   1.0
  5.|-- 198.51.100.10          0.0%   100   11.1  11.4  10.6  19.9   1.2
```

Hop 3's 40% loss with 0% afterwards is ICMP rate limiting, not real loss; real loss starts at a hop and continues to the destination. High StDev or Wrst means jitter or queuing spikes. Windows `pathping` traces first, then sends 100 queries to each hop (250 ms apart by default, about 25 s per hop) and reports loss per hop and per link. Read the destination line first: 0% there means intermediate loss is just rate limiting.

### D5. nslookup, dig and Resolve-DnsName

```
C:\> nslookup www.example.com
Server:  resolver1.corp.example
Address:  10.0.0.53

Non-authoritative answer:
Name:    www.example.com
Address:  203.0.113.10
```

"Non-authoritative" only means the answer came from a resolver, not the zone's own server; normal. `nslookup www.example.com 8.8.8.8` asks a specific server: if the public resolver answers and the configured one fails, suspect the configured resolver (or split DNS by design). Also `nslookup -type=mx example.com`, `-type=ns`, and `nslookup 203.0.113.10` for PTR. Errors: "Non-existent domain" = NXDOMAIN; "Server failed" = SERVFAIL; "DNS request timed out" = no reply.

```
$ dig @1.1.1.1 www.example.com A
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 4242
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1
;; ANSWER SECTION:
www.example.com.        300     IN      A       203.0.113.10
;; Query time: 23 msec
;; SERVER: 1.1.1.1#53(1.1.1.1)
```

Read the `status`, ANSWER count, TTL (seconds left in cache), flags (`aa` authoritative answer, `ra` recursion available), query time (small when cached) and answering server. Useful: `dig +short name`; `dig +trace name` (walks root -> TLD -> authoritative and shows where a delegation breaks); `dig @ns1.example.com www.example.com` (ask the authoritative server, bypassing caches); `dig -x 203.0.113.10` (PTR). PowerShell: `Resolve-DnsName www.example.com -Type A -Server 1.1.1.1`.

### D6. ipconfig, route, arp, netstat and ss

- **`ipconfig /all`:** IPv4 address (169.254.x.x = DHCP failed), mask, default gateway (empty = no route off the subnet), DNS servers, DHCP lease, MAC, "Media disconnected", and extra adapters such as a VPN changing routes or DNS. `/flushdns` clears the local DNS cache (after a DNS change or a stale entry); `/displaydns` shows it; `/release` and `/renew` redo DHCP.
- **`route print` / `ip route`:** confirm the default route (`0.0.0.0  0.0.0.0  <gateway>` on Windows; `default via 192.168.1.1 dev eth0` on Linux), spot more-specific routes added by VPNs, compare metrics. `ip route get 198.51.100.10` shows the exact route, interface and source address Linux would use.
- **`arp -a` / `ip neigh`:** a gateway entry with a MAC should exist; missing, "incomplete" or FAILED means a layer 2 problem towards the gateway; one IP with changing MACs suggests a duplicate IP.
- **`netstat -ano`** (all, numeric, process ID) / **`ss -tulpn`** (listening sockets with processes): LISTENING = local service accepting on that port; ESTABLISHED = active; SYN_SENT = our SYN got no answer (remote filtered or unreachable); TIME_WAIT = normal after close; many CLOSE_WAIT = likely application bug. Map a PID with `tasklist /fi "PID eq 1234"`. On Linux, `ss -ti` shows per-connection RTT and retransmissions, good evidence of loss.

### D7. curl -v and curl -w timing

`curl -v -o /dev/null https://www.example.com` (Windows: `curl.exe -v -o NUL ...`) narrates each layer: `Trying 203.0.113.10:443...` (DNS worked), `Connected to` (TCP worked), `SSL connection using TLSv1.3` plus certificate subject, start date, expire date and issuer (TLS worked), `< HTTP/2 200` (response status).

| curl error (exit code) | Meaning |
|---|---|
| `Could not resolve host` (6) | DNS failure |
| `Connection refused` (7) | RST: port closed, host reachable |
| `Connection timed out` / `Timeout was reached` (28) | no answer: filtered or no route |
| `SSL certificate problem: certificate has expired` (60) | expired certificate |
| `unable to get local issuer certificate` (60) | missing intermediate or untrusted CA |
| `no alternative certificate subject name matches target host name` (60) | name mismatch (wrong certificate or SNI) |

```
$ curl -o /dev/null -s -w "dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n" https://www.example.com
dns=0.012 tcp=0.045 tls=0.083 ttfb=0.190 total=0.205 code=200
```

Values are cumulative seconds from the start, so subtract: DNS 12 ms; TCP handshake 45 - 12 = 33 ms (about 1 RTT); TLS 83 - 45 = 38 ms (about 1 RTT, consistent with TLS 1.3); waiting for the first byte 190 - 83 = 107 ms (about one RTT plus about 70 ms of server time); download 15 ms. Large DNS time: the resolver. Large TCP time: long or lossy path (a connect of a second or more often means a lost SYN was retransmitted). Large TLS time: high RTT or a slow server handshake. Large TLS-to-TTFB gap: slow server or back end.

### D8. Test-NetConnection (PowerShell)

```
PS> Test-NetConnection www.example.com -Port 443
ComputerName     : www.example.com
RemoteAddress    : 203.0.113.10
RemotePort       : 443
InterfaceAlias   : Ethernet
SourceAddress    : 192.168.1.20
TcpTestSucceeded : True
```

`TcpTestSucceeded : False` comes with a "TCP connect failed" warning plus a ping result. It proves only the TCP handshake (no TLS or HTTP), and does not clearly separate refused from filtered. Also `-TraceRoute` and `-InformationLevel Detailed`; alias `tnc`. Linux: `nc -vz -w 3 host 443` prints "succeeded", "Connection refused" or times out.

### D9. openssl s_client for certificate checks

```
$ openssl s_client -connect www.example.com:443 -servername www.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates
subject=CN = www.example.com
issuer=C = US, O = Example CA, CN = Example Issuing CA 1
notBefore=Oct  1 00:00:00 2026 GMT
notAfter=Apr 18 23:59:59 2027 GMT
```

- Always pass `-servername` (SNI). Newer OpenSSL versions send it automatically when you connect by hostname, older ones do not, and connecting by IP never sends it.
- In the full output, aim for `Verify return code: 0 (ok)`. Common failures: `10 (certificate has expired)`, `18 (self-signed certificate)`, `19 (self-signed certificate in certificate chain)`, `20 (unable to get local issuer certificate)`, `21 (unable to verify the first certificate)`, which usually means the server is not sending the intermediate.
- `-showcerts` prints every certificate sent, so you can see whether the chain is complete.
- `openssl x509 -noout -checkend 2592000` says whether the certificate expires within 30 days (2,592,000 s); handy in scripts.
- `</dev/null` stops s_client waiting for input (Windows cmd: `<NUL`). OpenSSL is not on Windows by default (Git for Windows includes it, for example).

### D10. Router-side commands you may see (Cisco IOS style)

Syntax varies by vendor; these are common Cisco IOS forms. `show ip interface brief`: interface status ("up/up" good, "administratively down" = shut in config, "down/down" suggests layer 1). `show interfaces <name>`: input errors, CRC errors, drops and current rates (rising CRCs mean a physical problem). `show ip route <ip>`: the route used. `show ip bgp summary`: one line per neighbour; a number in State/PfxRcd means Established (prefixes received), Idle or Active means down. `show logging`: recent events such as flaps, with timestamps. `ping`/`traceroute` with a `source` option test from the right address.

### D11. MTU and fragmentation symptoms

Overheads shrink the usable MTU below Ethernet's 1,500 bytes: PPPoE 8 bytes (1,492), GRE 24 bytes (1,476), IPsec ESP tunnel mode often 50-70+ bytes depending on algorithms, VXLAN 50 bytes.

Symptoms: ping works and TCP connections open, but large transfers hang; some pages load and others stall; SSH logs in but freezes on large output; file shares and VPNs misbehave; HTTPS can hang mid-handshake because the certificate message is large.

Usual cause: a Path MTU Discovery black hole. PMTUD (RFC 1191) relies on routers sending ICMP "fragmentation needed" (type 3 code 4); if a firewall blocks it, the sender never learns to send smaller packets. Fixes: allow that ICMP, clamp the TCP MSS on the tunnel or edge router (for example to 1,360 bytes for IPsec), or set a consistent lower MTU. Confirm with the DF ping test in D2.


## Formulas with worked examples

Every formula below is implemented in `src/noc_pulse/kpi.py`, `tickets.py` or `sdwan.py`,
and the worked numbers are asserted in the tests (`tests/test_kpi.py`, `tests/test_tickets.py`,
`tests/test_sdwan.py`). Do these by hand once with a calculator; interviewers like it when
you can produce a number on a whiteboard.

### 1. Availability

```
availability % = successful samples / total samples * 100
```

A ping sample in noc-pulse is one *burst* of 10 ICMP echoes. The sample counts as "up" if
at least one echo came back (reachability); the echoes that did not come back are counted
separately as packet loss.

Worked example: the gateway is pinged every 30 s, so a day has 24 * 3600 / 30 = 2,880
samples. If 3 bursts get no reply at all:

```
(2880 - 3) / 2880 * 100 = 2877 / 2880 * 100 = 99.896 %
```

That is below a 99.9 % target, even though only 90 seconds of "down" were seen. Note the
catch: an outage shorter than the 30 s interval can fall between two samples and never be
seen. Sampling-based availability is only as good as the sampling interval.

**Service availability (per minute).** A service has several member targets and probes.
noc-pulse cuts time into 60-second buckets; a minute is "up" only if *every* sample of
*every* member in that minute succeeded:

```
minute 10:01  gw ping ok, wan-cloudflare ping ok, wan-google ping ok     -> up
minute 10:02  gw ping ok, wan-cloudflare ping FAIL, wan-google ping ok   -> down
service availability = up minutes / minutes with data * 100
```

This is strict (one failed probe anywhere marks the whole minute down), which is the
conservative way to report an SLA.

### 2. Mean vs percentiles (p50, p95)

Percentile with linear interpolation (`kpi.percentile`, same as `numpy.percentile`):

```
sort the n values x[0..n-1]
h  = (n - 1) * p / 100
p-th percentile = x[floor(h)] + (h - floor(h)) * (x[ceil(h)] - x[floor(h)])
```

Worked example: ten RTTs (ms) `5.1 5.3 4.9 5.0 12.4 5.2 5.0 5.1 30.2 5.4`.
Sorted: `4.9 5.0 5.0 5.1 5.1 5.2 5.3 5.4 12.4 30.2` (index 0..9).

```
mean = 83.6 / 10 = 8.36 ms
p50: h = 9 * 0.50 = 4.5  -> x[4] + 0.5 * (x[5] - x[4]) = 5.1 + 0.5 * 0.1  = 5.15 ms
p95: h = 9 * 0.95 = 8.55 -> x[8] + 0.55 * (x[9] - x[8]) = 12.4 + 0.55 * 17.8 = 22.19 ms
```

The mean (8.36 ms) describes no real packet: eight packets were about 5 ms and two were
slow. The median says "a typical packet takes 5 ms", the p95 says "1 in 20 packets is worse
than 22 ms". SLAs and NOC dashboards use percentiles for exactly this reason.

### 3. Jitter

**Definition used (mean deviation of D).** Inside one ping burst the echoes are sent 200 ms
apart. D is the difference between the RTTs of two consecutive echoes:

```
D(i) = RTT(i) - RTT(i-1)
jitter = mean of |D(i)| over all consecutive pairs inside each burst
```

Worked example, one burst `RTT = 10, 12, 11, 15 ms`:

```
|12 - 10| = 2,  |11 - 12| = 1,  |15 - 11| = 4
jitter = (2 + 1 + 4) / 3 = 2.333 ms
```

Pairs are only formed *inside* a burst: the last echo of one burst and the first echo of
the next burst are 30 s apart, so their difference is not jitter.

**RFC 3550 estimator.** RTP receivers (VoIP phones) keep a running estimate:

```
J = J + (|D| - J) / 16          (start J = 0)
```

For the same burst:

```
J1 = 0      + (2 - 0)      / 16 = 0.125
J2 = 0.125  + (1 - 0.125)  / 16 = 0.1797
J3 = 0.1797 + (4 - 0.1797) / 16 = 0.4185 ms
```

The 1/16 gain makes J an exponentially smoothed version of |D|: over a long, steady stream
it converges to the mean |D| (a test feeds 400 packets with |D| = 4 and gets J = 4.0). Over
only 10 echoes it has not converged yet (0.42 vs 2.33 above), which is why the KPI uses the
plain mean of |D| (what the RFC calls the "mean deviation") and keeps the estimator for
reference. Both are in `kpi.py`. Caveat: ping gives *round-trip* times, so this is two-way
jitter; RTP jitter is one-way.

### 4. Packet loss

```
loss % = (echoes sent - echoes received) / echoes sent * 100
```

Worked example: in one 60 s decision interval the SD-WAN simulation has 2 bursts x 10
echoes = 20 echoes per path. One lost echo gives 1/20 = 5 % loss for that minute. That is
already above the 1 % voice threshold, so the *resolution* of the measurement matters: with
only 20 echoes per minute, the smallest non-zero loss you can see is 5 %.

### 5. E-model: R-factor and MOS (voice quality estimate)

The ITU-T G.107 E-model rates a voice path on a 0-100 scale (R) by subtracting
impairments from a best-case value. noc-pulse uses the common simplified form for a G.711
call:

```
d       = RTT/2 + 2*jitter + 20                         one-way mouth-to-ear delay (ms)
                                                         (half the RTT + a de-jitter buffer
                                                          of 2x jitter + 20 ms packetisation)
Id      = 0.024*d + 0.11*(d - 177.3)*H(d - 177.3)       delay impairment, H = step function
                                                         (Cole & Rosenbluth, 2001)
Ie_eff  = Ie + (95 - Ie) * Ppl / (Ppl + Bpl)            loss impairment (G.107, random loss)
          Ie = 0 and Bpl = 25.1 for G.711 with packet-loss concealment
R       = 93.2 - Id - Ie_eff                            93.2 = R0 - Is with G.107 defaults
MOS     = 1 + 0.035*R + R*(R - 60)*(100 - R)*7e-6       G.107 Annex B, MOS = 1 for R < 6.5,
                                                         4.5 for R > 100
```

The delay term is flat (0.024 per ms) until about 177 ms one-way and then gets 5.6 times
steeper: conversation breaks down when people start talking over each other. That is the
same reason ITU-T G.114 recommends keeping one-way delay under about 150 ms.

**Worked example A (good line): RTT 40 ms, jitter 5 ms, loss 0 %**

```
d      = 40/2 + 2*5 + 20 = 50 ms
Id     = 0.024 * 50 = 1.2                    (50 < 177.3, no second term)
Ie_eff = 0                                   (no loss)
R      = 93.2 - 1.2 - 0 = 92.0
MOS    = 1 + 0.035*92 + 92*(92-60)*(100-92)*7e-6
       = 1 + 3.22 + 92*32*8*7e-6 = 1 + 3.22 + 0.1649 = 4.38
```

**Worked example B (poor line): RTT 300 ms, jitter 20 ms, loss 2 %**

```
d      = 150 + 40 + 20 = 210 ms
Id     = 0.024*210 + 0.11*(210 - 177.3) = 5.04 + 3.597 = 8.637
Ie_eff = 0 + 95 * 2 / (2 + 25.1) = 190 / 27.1 = 7.011
R      = 93.2 - 8.637 - 7.011 = 77.55
MOS    = 1 + 0.035*77.55 + 77.55*17.55*22.45*7e-6
       = 1 + 2.714 + 0.214 = 3.93
```

**What the numbers mean** (R ranges from ITU-T G.109): R 90-100 "best", 80-90 "high",
70-80 "medium", 60-70 "low", 50-60 "poor". MOS above about 4.0 is what users call "toll
quality"; 4.41 is the ceiling with these defaults (R = 93.2). Even a perfect network
cannot score 5 because the codec and the handsets themselves have limits.

**Honest caveats.** This is an *estimate* from ICMP probes, not a measurement of real calls.
It assumes G.711, a 2 x jitter de-jitter buffer and 20 ms packetisation, uses RTT/2 as
one-way delay (paths can be asymmetric), and treats loss as random (bursty loss is worse;
G.107 has a BurstR factor for that, which is set to 1 here). The Bpl value of 25.1 is the
figure commonly quoted from ITU-T G.113 Appendix I for G.711 with packet-loss concealment;
check it against the Recommendation before quoting it in an interview, and note it is a
parameter (`kpi.r_factor(..., bpl=...)`).

### 6. Hysteresis timing (how fast an alarm is raised)

```
worst-case detection time ~ raise_after * interval + probe timeout
```

The gateway ping runs every 30 s with `raise_after: 3`, so a hard failure is alarmed after
about 60-90 s, and cleared after `clear_after: 2` good samples (about 30-60 s). The HTTP
probes run every 60 s, so an HTTP alarm takes about 2-3 minutes. In the fault-injection
demo the interval is 2 s, so detection took a few seconds. Trade-off: a smaller N means
faster detection but more false alarms from single lost packets.

### 7. Ticket priority and SLA timers

```
priority = matrix[impact][urgency]

             urgency: high   medium   low
impact high            P1      P2      P3
impact medium          P2      P3      P4
impact low             P3      P4      P4
```

* impact = the most important service affected by the failing element (a failure that
  hits 3 or more services is always "high").
* urgency = from alarm severity: critical -> high, major -> medium, minor/warning -> low.

SLA targets (from `config/inventory.yaml`, typical of telecom NOCs but illustrative):

| Priority | Response (acknowledge) | Resolve (restore) |
|---|---|---|
| P1 | 15 min | 4 h |
| P2 | 30 min | 8 h |
| P3 | 2 h | 24 h |
| P4 | 8 h | 72 h |

Worked example: a P1 opens at 02:00. Response is due at 02:15 and resolution at 06:00. The
engineer acknowledges at 02:20 -> **response SLA breached** (and it stays breached even after
the ticket is later resolved). Service is restored at 05:10 -> resolve SLA met.
If an alarm clears on its own before anyone acknowledges, the resolution time counts as the
first response.

### 8. MTTA and MTTR

```
MTTA = mean(acknowledged_at - opened_at)   over acknowledged tickets
MTTR = mean(resolved_at - opened_at)       over resolved tickets
```

Worked example (the numbers in `tests/test_tickets.py`):

| Ticket | Opened | Acked | Resolved | Time to ack | Time to resolve |
|---|---|---|---|---|---|
| a | 0 s | 60 s | 600 s | 60 s | 600 s |
| b | 100 s | 280 s | 1000 s | 180 s | 900 s |
| c | 200 s | - | - | - | - |

```
MTTA = (60 + 180) / 2 = 120 s        MTTR = (600 + 900) / 2 = 750 s
```

Ticket c is still open, so it is in neither mean (but it shows up as a response-SLA breach
once 30 minutes pass, because it is a P2 that nobody acknowledged). Be ready to say that
"MTTR" is ambiguous (repair / restore / resolve / recovery); here it means *open ->
Resolved*, i.e. time to restore service as seen by the ticket.

### 9. SD-WAN: static vs SLA-aware steering

Score = % of decision intervals in which the chosen path actually met the application SLA.
The dynamic policy decides using the *previous* interval's measurements (it cannot see the
future), switches only when the current path broke the SLA and another path met it, waits
a hold-down of 3 intervals after each switch, and returns to the primary once the primary
has met the SLA for 3 intervals in a row.

Worked example (the "brown-out" test in `tests/test_sdwan.py`): 25 intervals; the primary
path a has 10 % loss during intervals 5-14 (a brown-out: degraded but not dead), path b is
always clean; scoring starts at interval 1, so there are 24 scored intervals.

```
static   : stays on a (a is never "down"), fails intervals 5..14  -> 14/24 = 58.3 %
dynamic  : sees the loss in interval 5, moves to b for interval 6, comes back to a once
           a is clean for 3 intervals                              -> 23/24 = 95.8 %
oracle   : some path met the SLA in every interval                 -> 24/24 = 100 %
```

The dynamic policy loses exactly one interval: the first interval of the brown-out, which
it can only detect after the fact. That reaction delay is real in every SD-WAN product
(probes need time to measure loss).


## How noc-pulse works: a file-by-file tour

Read the files in this order. For each one: what it does, the key functions, and what to
say if asked about it.

### `config/inventory.yaml` - the model of the network

The whole tool is driven by this file. It has six sections:

* `targets` - the monitored elements (in ITIL language, configuration items). Each has a
  `host`, a `role` (gateway, dns, transit, app), a list of `probes` with intervals, and
  `depends_on` - the parent it needs in order to work. `site-gw` uses the special host
  `auto-gateway` (looked up from `ipconfig` at start-up) and `dns-site` uses `system`
  (whatever resolver Windows is configured with).
* `services` - what a customer buys: a group of targets with an SLA class, an `impact`
  level used for ticket priority, and optionally `kpi_probe` (which probe's latency is the
  service KPI).
* `sla_policies` - KPI targets per class (gold / silver / bronze).
* `alarm_rules` - threshold rules: which probe kinds, which field, comparison, threshold
  (can differ per probe kind), severity, `raise_after`, `clear_after`, and
  `service_affecting` (only those can be a root cause).
* `alarm_settings` - `flap_window_s` and `correlation_window_s`.
* `ticketing` - the priority matrix, severity-to-urgency mapping, SLA minutes per priority,
  and which severities open tickets (critical and major; minor and warning stay on the
  console).
* `sdwan` - which measured series play the role of WAN paths, the primary, the interval,
  hold-down and the application SLA classes.

### `inventory.py` - load and validate the model

`parse_inventory()` turns YAML into dataclasses (`Target`, `ProbeSpec`, `Service`,
`SlaPolicy`, `RuleSpec`, `Inventory`) and refuses bad input: duplicate ids, unknown
dependencies, **dependency cycles** (depth-first search with a "visiting" state), unknown
SLA classes, and probe intervals under 30 s for public addresses (`is_external()`; private
and loopback addresses are exempt so the localhost demo can use 2 s).

Graph helpers used by correlation and ticketing: `ancestors()` (nearest first),
`descendants()`, `impacted_services()` (services that use the target or anything below
it) and `impact_of()`.

### `probes.py` - measuring things (asyncio)

Every probe returns a `ProbeResult` and never raises (an exception becomes `ok=False` with
an `error` string) so one bad target cannot crash the collector.

* `ping_probe()` - a burst of 10 ICMP echoes 200 ms apart via **icmplib**, which works for
  a normal (non-admin) user on Windows; it falls back to the system `ping` command and
  `parse_ping_output()` if the ICMP socket cannot be opened. Windows prints `time<1ms` for
  very fast replies (recorded as 0.5 ms) and counts "Destination host unreachable" as a
  *received* reply - the parser does not, because that reply came from a router, not the
  target.
* `tcp_probe()` - resolves the name, then times `asyncio.open_connection()` = the TCP
  three-way handshake, roughly one RTT.
* `dns_probe()` - dnspython's async resolver against a specific server (or the OS
  resolver); the time of one A-record query.
* `http_probe()` - does the request by hand so each phase can be timed: DNS
  (`getaddrinfo`), TCP connect, TLS handshake (`start_tls`), and time to the status line
  (TTFB). It also reads the server certificate's `notAfter` and computes days to expiry. It
  sends one `GET` with `Connection: close`, reads only the status line, and closes.
* Derived metrics on `ProbeResult`: `loss_pct`, `jitter_ms`, and `metrics()` (the
  dictionary the alarm rules look at).

### `collector.py` - scheduling

One asyncio task per (target, probe). Each loop starts at a random offset inside its
interval (so 19 loops do not fire at the same instant) and schedules itself on a fixed
grid (`start + k * interval`) so it does not drift. If the PC was asleep, missed ticks are
skipped instead of firing in a burst. A heartbeat task updates the `runs` table every 15 s
so coverage and gaps can be reported. `KeepAwake` calls the Windows API
`SetThreadExecutionState` so the laptop does not go to sleep during an measurement run (the
setting ends when the process exits).

### `store.py` - SQLite

Tables: `samples` (raw results; target *ids* only, never the gateway IP), `runs`,
`alarms`, `tickets`, `ticket_events` (worklog). WAL mode lets the dashboard read while the
collector writes. `upsert()` uses `INSERT ... ON CONFLICT DO UPDATE`.

### `kpi.py` - the maths

`percentile`, `mean_abs_delta` (jitter), `rfc3550_jitter`, `availability_pct`, `loss_pct`,
the E-model (`one_way_delay_ms`, `delay_impairment`, `loss_impairment`, `r_factor`,
`mos_from_r`), `summarize()` (KPIs for one series) and `evaluate_sla()`. Pure functions,
no I/O - that is what makes them easy to test with hand-computed numbers.

### `alarms.py` - from samples to alarms

For every sample and every rule that applies to its probe kind, `evaluate_rule()` returns
True (bad), False (good) or None (no evidence: e.g. a failed probe has no latency, so the
latency rule neither raises nor clears). Then `AlarmEngine.evaluate()`:

1. updates the streak counters for the key `target|probe|rule`;
2. raises after `raise_after` consecutive bad samples (`_raise`), or re-opens a recently
   cleared alarm with the same key if it is inside `flap_window_s` (flap);
3. while active, counts further bad samples on the same alarm (de-duplication);
4. clears after `clear_after` consecutive good samples (`_clear`).

Correlation lives in `_root_for()` and the end of `_raise()`:

* a new alarm whose own target or any ancestor already has an active
  **service-affecting** alarm is marked `suppressed` with `root_cause_id`. If that
  alarm is itself a symptom, the chain is followed upwards - but only through alarms
  that are still active, with a guard against loops (`_top_of_chain()`);
* if the parent's alarm arrives *after* the children (timing is never perfect), the
  children already active below it are re-linked ("correlated" event);
* when the root clears, its children get `orphan_grace_s` (300 s) to clear by themselves;
  if they are still failing after that, they are treated as independent faults
  ("unsuppressed" event) and get their own ticket.

### `tickets.py` - ITIL incident records

`TicketState` and the `TRANSITIONS` table are the state machine; `Ticket.transition()`
rejects anything not in the table with `InvalidTransition` and records timestamps and a
worklog line. `TicketManager.open()` computes urgency, priority and the response/resolve
due times. `response_breached()` / `resolve_breached()` / `remaining()` are the SLA
timers. `mtta_s()`, `mttr_s()` and `sla_report()` produce the reports.

### `engine.py` - the pipeline

`Engine.process(sample)` = alarm evaluation -> event handling -> pending tickets -> auto
close. Event handling:

* raised and not suppressed -> wait `correlation_window_s` (60 s in the real inventory, 4 s
  in the demo); if the alarm is still active and still not suppressed, open a ticket
  (critical/major only) with the impacted services, and start auto-diagnostics in the
  background (live mode);
* raised but suppressed -> one worklog line on the root cause's ticket, no new ticket;
* correlated (late parent) -> link the child ticket to the parent ticket (parent/child
  incidents);
* re-raised (flap) -> re-open the Resolved ticket instead of opening a new one;
* cleared -> auto-resolve the ticket, and resolve its child tickets with it.
* when a parent's ticket is finally opened (after its correlation window), tickets
  already open for its children are linked to it as child incidents.
* before changing any ticket the engine re-reads it from the database (`_sync()`), so an
  acknowledgement or closure typed in the CLI while the engine runs is not overwritten.
* alarm and ticket numbers continue after whatever is already in the database.

`replay()` clears alarms/tickets and feeds all stored samples through a fresh engine. Live
and replay use the same code; only the clock differs (sample timestamps).

### `diagnostics.py` - L1 triage on ticket open

Runs, with time-outs, `ping` (4 echoes), `tracert -d` (12 hops), DNS lookups via the OS
resolver and via 1.1.1.1 (so you can tell "my DNS is broken" from "the name is broken"),
a TCP connect to the service port and an HTTP GET. `summarize_findings()` turns the results
into one hypothesis line, e.g. "host answers ping but port refused/filtered -> application
or firewall issue". This is the same layered reasoning as the troubleshooting answers in
Part E.

### `sdwan.py` - path selection simulation

`bucketize()` aligns the ping series of each path into 60 s intervals (latency, jitter,
loss per path per interval; intervals where *no* path has data are collector gaps and are
dropped; a path missing data in an interval counts as down). `simulate_static()`,
`simulate_dynamic()` and `simulate_oracle()` implement the three policies; `meets()` checks
an interval against an application class.

### `report.py`, `cli.py`, `dashboard.py`, `faultdemo.py`

* `report.py` - `coverage()` (span, samples, gaps), `target_kpis()`, `service_kpis()`,
  `alarm_summary()`, `ticket_summary()`, `sdwan_report()` and `render_markdown()`.
* `cli.py` - the Typer commands (`collect`, `probe`, `report`, `replay`, `alarms`,
  `tickets [show|ack|start|resolve|close]`, `sdwan`, `diagnose`, `demo`, `export-sample`,
  `dashboard`) printing Rich tables.
* `dashboard.py` - Streamlit page built from the same report functions.
* `faultdemo.py` - starts local listeners, runs the collector with the live engine, injects
  the two faults, plays the L1 operator, and writes `results/fault_demo/`.

### Tests

`tests/conftest.py` builds a 4-node lab inventory (gw -> dns -> app, api) and a `sample()`
helper. Each test file matches one module. Nothing touches the network.

## Design decisions (and what to say about them)

| Decision | Why | Trade-off you should mention |
|---|---|---|
| asyncio for probes | 19 probe loops are I/O-bound; one thread, no locks | a CPU-heavy step would block every loop |
| icmplib with system `ping` fallback | sub-millisecond RTTs, works unprivileged on Windows; fallback keeps it portable | Windows `ping` only gives whole milliseconds |
| ping as 10-echo bursts every 30 s | gives loss and jitter per sample while staying polite (1 burst / 30 s) | loss resolution: 1 echo = 10 % of a burst |
| HTTP probe written by hand | separate DNS / TCP / TLS / TTFB timings and the certificate; a library hides these | only reads the status line, no redirects followed |
| store raw samples, compute KPIs later | formulas can be fixed without re-collecting; replay possible | more storage than pre-aggregated counters |
| SQLite (WAL) | zero setup, one file, readers do not block the writer | single writer; not for many collectors |
| same engine for live and replay | the measured results come from the code that runs live; deterministic tests | diagnostics cannot run in replay |
| hysteresis N=3 / M=2 | one lost burst never pages anyone | slower detection (raise_after x interval) |
| correlation by dependency tree + correlation window | one ticket per real fault instead of an alarm storm; handles the child-first race | needs an accurate inventory (a wrong `depends_on` hides real faults) |
| only critical/major open tickets | minor/warning are console-only, like most NOCs | a slow-burning latency problem needs someone watching the console |
| impact from services, urgency from severity | mirrors the ITIL matrix; P1 means "big customer impact now" | real NOCs also use contract tiers and VIP flags |
| percentiles, not averages | averages hide tails | p95 needs enough samples to be meaningful |
| jitter = mean abs delta per burst | matches the RFC 3550 definition (mean deviation of D) and converges with few samples | RTT-based, so two-way |
| SD-WAN decides on the previous interval | no "peeking" at the future - the comparison is fair | reaction delay of one interval |
| SLA thresholds are maxima (`<=`) | "loss must not exceed 1 %" is how SLAs are written | with 20 echoes per minute, one lost echo is already 5 % |
| MOS only judged for voice services | MOS describes a phone call; a web or DNS service should not fail its SLA on it | the per-target tables still show MOS for every ping series |
| independent code review | a second reader found real correlation bugs (see P19) | none - do it on every project |
| oracle policy | gives an upper bound, so you know how much room there was | not achievable in reality |
| Streamlit + Typer | pure Python UI and CLI, nothing to build | Streamlit is not a multi-user NOC wallboard |
| tests without network | CI is fast and deterministic | probes themselves are tested by running them, not in CI |


## What the real numbers mean

The run lasted 1.98 hours (23:36 to 01:35 IST, 23-24 Nov 2026) before it was stopped. All
numbers are in the README "Real results" section and in `docs/FACTS.md`; this section is
about how to *talk* about them.

* **Low latency, small jitter.** About 5-6 ms RTT to 1.1.1.1 / 8.8.8.8 / 9.9.9.9 means
  the anycast instances are close to the ISP. Jitter of 1-2 ms is normal for Wi-Fi plus
  broadband. Those numbers alone give an E-model MOS around 4.4 (the model's ceiling with
  default parameters is 4.41).
* **One real incident.** At 00:33-00:36 every Internet target failed for 2-3 minutes while
  the gateway kept answering: a short upstream (ISP-side) outage. That single event is why
  availability is 97.5-98.3 %, why loss to the anycast targets is about 1.7-1.8 %, why the
  voice MOS over the whole window is 4.25 instead of 4.40, and why every service shows an
  SLA breach for this window. Say it plainly: "a 3-minute outage in a 2-hour window costs
  about 2.5 % availability - that is why SLAs are measured over a month, where the same
  outage would be about 99.99 %."
* **What the engine did with it.** 10 alarms, 4 suppressed as same-target symptoms, 6
  tickets that auto-resolved within about a minute. The six tickets are the lesson: the
  inventory had no node for the ISP uplink, so the WAN targets and web sites looked like
  independent siblings. Adding an `isp-uplink` parent would have produced one P1. This is a
  very good interview story - it shows you understand that correlation depends on the
  dependency model (the CMDB in a real NOC).
* **SD-WAN result.** All three "paths" failed together during the outage (they share one
  access line), so static, dynamic and oracle scored the same. That is the honest result:
  path selection only helps when paths fail independently - which is exactly why real
  SD-WAN sites buy a second, diverse circuit (for example broadband + LTE). The unit test
  with a synthetic brown-out shows the case where steering does help (58 % -> 96 %).
* **Fault-injection demo.** Seconds-scale numbers from a localhost lab; they prove the
  pipeline (alarm -> correlation -> one ticket -> diagnostics -> resolve -> close), not an
  operational MTTR.


## 7-day learn-it plan

About 3 hours a day. Each day: read, run, do the exercises *without looking*, then check the
answers at the end of the section. By day 7 you should be able to explain every line of the
README and every number in `docs/FACTS.md`.

### Day 1 - Run it, and learn what a packet is

* Read: Part A sections on OSI/TCP-IP, IP addressing, gateway, ICMP.
* Do: `pip install -e ".[dev,dashboard]"`, `noc-pulse probe`, `pytest -q`.
* Do: in a terminal, `ipconfig /all`, `ping -n 10 <your gateway>`, `ping -n 10 1.1.1.1`,
  `tracert -d 1.1.1.1`.
* Exercises
  1. Your gateway answers in about 2 ms and 1.1.1.1 in about 5 ms. Where are the other
     ~3 ms spent?
  2. In `tracert`, hop 3 shows `* * *` but hop 4 and the destination answer. Is hop 3
     broken?
  3. A target is pinged every 30 s for 12 hours and 4 bursts get no reply. Availability?

### Day 2 - TCP, DNS, HTTP, TLS (read `probes.py`)

* Read: Part A sections on TCP, DNS, HTTP/TLS. Then `probes.py` top to bottom.
* Do: `nslookup www.tatacommunications.com`, `nslookup www.tatacommunications.com 1.1.1.1`,
  `Test-NetConnection www.google.com -Port 443` (PowerShell), `curl -v https://www.wikipedia.org/ -o NUL`.
* Do: `noc-pulse diagnose web-wikipedia` and read every line of the output.
* Exercises
  4. Why does the HTTPS probe report ~200-400 ms when TCP connect to the same host is
     ~6 ms? Name the phases.
  5. "Connection refused" vs "connection timed out": what does each tell you about the
     target?
  6. Why can a DNS query to 8.8.8.8 take 15 ms the first time and 5 ms the next time?

### Day 3 - KPIs by hand (read `kpi.py`, the Formulas section)

* Do every worked example in the Formulas section with a calculator.
* Do: `pytest tests/test_kpi.py -v`, then change one expected number and watch it fail.
* Exercises
  7. RTTs `8, 9, 8, 30, 9`: mean, p50, p95?
  8. One burst `20, 24, 21, 21`: jitter (mean |D|)?
  9. RTT 100 ms, jitter 10 ms, loss 1 %: d, Id, Ie_eff, R, MOS?

### Day 4 - Alarms and correlation (read `alarms.py`, `tests/test_alarms.py`)

* Do: `noc-pulse demo` and read `results/fault_demo/README.md` line by line.
* Do: in `tests/test_alarms.py`, change `raise_after` in `conftest.py` from 3 to 1 and see
  which tests break and why.
* Exercises
  10. Draw the dependency tree of `config/inventory.yaml`. If `dns-site` dies, which alarms
      fire, which are suppressed, how many tickets open?
  11. Why does a failed HTTP sample not clear a `high_latency` alarm?
  12. What happens if the parent alarm is raised 20 s after the child alarm?

### Day 5 - ITIL and tickets (read Part B, `tickets.py`, `engine.py`)

* Do: `noc-pulse tickets --db data/demo.db`, `noc-pulse tickets show INC000001 --db data/demo.db`,
  then try `noc-pulse tickets start INC000001 --db data/demo.db` and read the rejection.
* Exercises
  13. Impact medium, severity critical: priority? Response and resolve due times if opened
      at 14:00?
  14. Incident vs problem vs change: classify (a) a site is down now, (b) the same router
      has rebooted 5 times this month, (c) replacing the router on Saturday night.
  15. Three tickets: acked after 2, 4 and 12 minutes; resolved after 30, 50 and 220 minutes.
      MTTA and MTTR?

### Day 6 - Voice and SD-WAN (read Part A voice/SD-WAN, `sdwan.py`)

* Do: `noc-pulse sdwan`, then edit `config/inventory.yaml` (`hold_down: 0`, then
  `primary: path-c`) and run it again. Note the switch counts.
* Exercises
  16. Why does voice care more about jitter and loss than a file download does?
  17. In the brown-out test, why does the dynamic policy lose exactly one interval?
  18. Why is a hold-down needed? What happens without it on a flapping link?

### Day 7 - Interview rehearsal

* Say the 60-second pitch (top of this guide) out loud five times.
* Answer every question in Part E and in "Project-specific questions" out loud, timing
  yourself (aim for under 90 seconds each).
* Learn the numbers in `docs/FACTS.md` and how each was produced.
* Do one full troubleshooting role-play with a friend: they are the customer ("the
  internet is slow"), you are L1 with a terminal.

### Answers

1. The ~3 ms is the path beyond your router: the ISP access network, the ISP core and the
   link to Cloudflare's nearest anycast site, plus queueing and processing on the way.
2. Not necessarily. Many routers do not answer (or rate-limit) the ICMP Time Exceeded
   messages that traceroute relies on, while still forwarding traffic. Only a problem that
   persists to the destination matters.
3. 12 h / 30 s = 1,440 bursts; (1440 - 4) / 1440 = 99.72 %.
4. HTTPS total = DNS lookup + TCP handshake (1 RTT) + TLS handshake (1 RTT for TLS 1.3, 2
   for TLS 1.2, plus certificate work) + the server's time to produce the first byte
   (TTFB). The server think-time usually dominates; the TCP connect is only the network
   RTT.
5. Refused: the host is reachable and replied with a TCP RST - nothing is listening on that
   port (service down). Timed out: no answer at all - host down, path broken, or a firewall
   silently dropping.
6. The first query may make the resolver walk root -> TLD -> authoritative servers (or the
   record was not cached at that anycast site); the second is answered from its cache until
   the TTL expires.
7. Sorted `8 8 9 9 30`; mean = 64/5 = 12.8; p50 = 9; p95: h = 4 * 0.95 = 3.8 -> 9 + 0.8 *
   (30 - 9) = 25.8 ms.
8. |24-20| = 4, |21-24| = 3, |21-21| = 0 -> 7/3 = 2.33 ms.
9. d = 50 + 20 + 20 = 90 ms; Id = 0.024 * 90 = 2.16; Ie_eff = 95 * 1 / 26.1 = 3.640;
   R = 93.2 - 2.16 - 3.64 = 87.40; MOS = 1 + 3.059 + 87.40 * 27.40 * 12.60 * 7e-6 = 1 +
   3.059 + 0.211 = 4.27.
10. `dns-site` dies: its DNS probe alarms (root cause, 1 ticket). The web targets' HTTP
    probes then fail on name resolution; their alarms are suppressed under the dns-site
    alarm (dns-site is their ancestor). The gateway and the public resolvers are not below
    dns-site, so they stay green. One ticket.
11. Because a failed sample has no latency value, `evaluate_rule()` returns None ("no
    evidence"); only a measured latency below the threshold counts as good.
12. The child alarm is raised first and its ticket waits in the correlation window (60 s).
    When the parent alarm is raised, the child is re-linked ("correlated") and marked
    suppressed, so when the window ends no child ticket is opened. If the child's ticket was
    already open (parent later than the window), it is linked to the parent ticket as a
    child incident and resolved together with it.
13. medium x high = P2; response due 14:30, resolve due 22:00 (30 min / 8 h).
14. (a) incident, (b) problem (find the root cause of repeated incidents), (c) change
    (planned, approved, in a maintenance window).
15. MTTA = (2 + 4 + 12) / 3 = 6 min. MTTR = (30 + 50 + 220) / 3 = 100 min (note how one long
    ticket dominates the mean - report the median as well).
16. Voice is real-time: a late packet is as bad as a lost one (it misses its playout slot),
    and the jitter buffer adds delay to absorb variation. A download uses TCP, which
    retransmits lost data and only gets slower.
17. It decides with the previous interval's measurements, so it can only notice the
    brown-out after the first bad interval has already happened.
18. Without hold-down, a path that alternates good/bad makes the policy chase it every
    interval; every switch can disturb sessions (re-ordering, NAT/state changes). Hold-down
    trades a little reaction speed for stability (the `test_hold_down_prevents_flapping`
    test shows fewer switches and no worse SLA).


## Part E - Generic interview questions with model answers

Practise saying these in your own words. A short, structured answer with an example beats a memorised definition. If you do not know something, say what you do know and how you would find out.

### E1. Networking fundamentals

**Q1. Explain the OSI model. Why does it matter in a NOC?**
Seven layers (physical, data link, network, transport, session, presentation, application), each relying on the one below. It gives me a method: link (L1), VLAN/ARP (L2), IP and routing with ping (L3), port open (L4), then DNS, TLS and the application. It also tells me who to involve: a fibre cut goes to the carrier, an expired certificate to the application owner.

**Q2. TCP vs UDP?**
TCP is connection-oriented: handshake, acknowledgements, retransmission and ordering, so it is reliable but slows down under loss (web, email, SSH). UDP has no handshake or retransmission; the application handles loss (DNS, voice, video, NTP, SNMP). For voice, a retransmitted packet would arrive too late to play, so UDP fits.

**Q3. What happens when you type a URL and press Enter?**
DNS resolves the name (cache, then recursive resolver). The browser opens TCP to port 443 (one RTT), does the TLS handshake (one RTT in TLS 1.3) and checks the certificate and hostname, sends an HTTP GET with the Host header, gets a status such as 200 and the HTML, then fetches other resources, reusing connections. Each step can fail, and I test them in that order.

**Q4. What network is 192.168.10.37/26 in, and how many hosts?**
/26 leaves 6 host bits, so blocks of 64 (.0, .64, .128, .192). .37 is in the .0 block: network 192.168.10.0, broadcast .63, hosts .1 to .62, so 62 usable. Mask 255.255.255.192.

**Q5. What is a default gateway, and what if it is wrong?**
The router a host uses for any destination outside its own subnet. If it is wrong or missing, the PC reaches local devices but nothing beyond. I check `ipconfig /all` and ping the gateway.

**Q6. What is NAT, and what problems can it cause?**
It rewrites private source addresses to a public one, usually with port translation so many hosts share one IP. Problems: inbound connections need port forwarding, idle UDP mappings time out, and protocols carrying IPs in the payload (such as SIP) can break, giving one-way audio. Behind carrier-grade NAT even the customer's "public" IP is shared.

**Q7. What is TTL, and how does traceroute use it?**
A per-packet counter each router decrements; at zero the router drops the packet and returns ICMP Time Exceeded, which prevents loops. Traceroute sends probes with TTL 1, 2, 3..., so each router reveals itself. Windows tracert uses ICMP Echo; Linux traceroute uses UDP by default.

**Q8. If ping to a server fails, is it down?**
Not necessarily: firewalls often block ICMP and routers rate-limit it. I test the real service with `Test-NetConnection server -Port 443` or `curl -v`; if TCP connects, the server is up. Equally, a working ping does not prove the application works.

**Q9. What is BGP? What is an AS?**
An AS is a network under one organisation's routing policy (an ISP, a cloud provider) with its own AS number. BGP is how ASes exchange reachability: peers talk over TCP 179 and advertise prefixes with an AS path, and the best path is chosen by policy, not speed. A BGP session going down usually withdraws routes, so traffic fails or moves to backup.

**Q10. What is anycast?**
One prefix announced from many locations; BGP sends each user to the nearest. 8.8.8.8, 1.1.1.1 and the DNS root servers use it. It gives low latency and resilience; for monitoring, pings from different cities hit different servers, and a sudden latency step may be a routing change, not a fault.

**Q11. Will upgrading 50 to 100 Mbit/s reduce latency?**
Only if congestion was the cause. Latency is propagation + serialization + queuing + processing. More bandwidth removes queuing on a full link and slightly cuts serialization, but propagation depends on distance, so on an idle link ping stays about the same.

**Q12. "Connection refused" vs "connection timed out"?**
Refused means a TCP RST came back: the path works but nothing listens on that port, so look at the service. Timed out means no reply: a firewall dropping the SYN, host down or broken routing, so look at the network and firewalls. Refused is fast; a timeout takes seconds because of retries.

**Q13. What is MTU, and what happens if it is wrong?**
The largest packet a link carries, normally 1,500 bytes on Ethernet, reduced by tunnels and PPPoE. If ICMP "fragmentation needed" is blocked, large packets silently vanish: ping and small requests work, big transfers and some pages hang. I test with `ping -f -l 1472`, lowering the size until it works; fixes are MSS clamping or allowing that ICMP.

### E2. DNS, HTTP and TLS

**Q14. How does DNS resolution work?**
The stub resolver asks a recursive resolver. If not cached, the resolver asks a root server (referral to .com), the TLD server (referral to the domain's authoritative servers), then the authoritative server, which answers. The resolver returns and caches the answer for its TTL. `dig +trace` shows each step.

**Q15. NXDOMAIN vs SERVFAIL vs timeout?**
NXDOMAIN: an authoritative "name does not exist" (typo, deleted record, expired domain). SERVFAIL: the resolver could not get a valid answer (authoritative servers broken or unreachable, DNSSEC failure). Timeout: no reply (resolver down or UDP 53 blocked). Each has a different owner, so I record which one I saw.

**Q16. We changed a DNS record but some users still reach the old IP. Why?**
Caching: resolvers and PCs keep the old answer until its TTL expires. I check the TTL with `dig`, query the authoritative server directly to confirm the new value, and flush the cache on affected PCs. Lowering the TTL before a planned change avoids it.

**Q17. 502 vs 503 vs 504?**
Server-side errors, usually from a proxy or load balancer. 502 Bad Gateway: invalid response from the back end. 503 Service Unavailable: overloaded or in maintenance. 504 Gateway Timeout: the back end did not answer in time. The network to the front end works, so I look at the back end and the path from the proxy to it.

**Q18. Explain the TLS handshake. Why does HTTPS take longer to start than HTTP?**
After TCP, the client sends a ClientHello (versions, ciphers, SNI, and in TLS 1.3 a key share); the server replies with its choice, certificate and proof it holds the private key; both derive keys and exchange Finished. TLS 1.3 adds 1 RTT, TLS 1.2 adds 2, so on a 100 ms path the request cannot be sent before about 200 ms (TLS 1.3) or 300 ms (TLS 1.2). Keep-alive, resumption and HTTP/3 reduce this.

**Q19. What is SNI, and why might a certificate work in a browser but fail in curl?**
SNI puts the hostname in the ClientHello so a multi-site server can present the right certificate; without it you get the default one and a name mismatch. Browser-versus-curl differences are usually a missing intermediate: browsers may fetch it themselves, while curl, Java and apps fail with "unable to get local issuer certificate". I check with `openssl s_client -connect host:443 -servername host -showcerts`.

### E3. Voice and QoS

**Q20. Why is voice so sensitive to network conditions?**
It is real time: small RTP packets over UDP, typically every 20 ms, played continuously. Delay makes people talk over each other, jitter makes packets miss the jitter buffer, and loss leaves gaps. There is no time to retransmit.

**Q21. What is jitter, and what does a jitter buffer do?**
Jitter is variation in packet delay, mostly from changing queue lengths. The jitter buffer holds packets briefly and plays them out evenly; a bigger buffer absorbs more jitter but adds delay, and packets arriving too late are discarded, so high jitter becomes loss.

**Q22. What is MOS, and what is a good value?**
Mean Opinion Score: under ITU-T P.800, listeners rate quality 1 (bad) to 5 (excellent), averaged. About 4.0 or above is good. Networks estimate it with the ITU-T G.107 E-model, which computes an R-factor from delay, loss and codec impairments and maps it to an estimated MOS that tops out near 4.5.

**Q23. What one-way delay is acceptable for voice?**
ITU-T G.114 is the reference: 400 ms one way should not be exceeded for general network planning, and below about 150 ms most applications are not significantly affected, which is why 150 ms is the usual design target. The figure is mouth to ear, so codec and jitter-buffer delay count too.

**Q24. What is QoS, and what is DSCP EF?**
QoS decides which traffic waits or is dropped when a link is congested. Traffic is classified and marked with DSCP; voice is usually EF (46) in a strict-priority queue, policed so it cannot starve others. QoS adds no bandwidth, only helps at congestion points, and is not honoured end to end across the public Internet, while MPLS providers offer QoS classes by contract.

**Q25. How much bandwidth does a G.711 call need?**
64 kbit/s of audio; with 20 ms packets, 160 bytes payload + 40 bytes IP/UDP/RTP headers at 50 packets/s = 200 x 8 x 50 = 80 kbit/s per direction at the IP layer, about 87 kbit/s with Ethernet framing. G.729 needs about 24 kbit/s at the IP layer.

### E4. WAN, MPLS and SD-WAN

**Q26. MPLS L3VPN vs an Internet circuit?**
MPLS L3VPN is a private routed service in the provider's network: VRFs separate customers, SLAs cover latency and loss, QoS classes exist, but it costs more and takes longer to deliver. Internet (DIA or broadband) is cheaper and reaches the cloud directly but is best effort beyond the access link, so it is protected with IPsec and, with SD-WAN, steered by measured quality.

**Q27. What are CE, PE, P and CPE?**
CE: customer edge router at the site. PE: provider edge router in the PoP, where the circuit lands and the customer's VRF lives. P: provider core routers that only switch labels. CPE: any equipment at the customer premises (router, modem, SD-WAN box), often provider-managed.

**Q28. What is SD-WAN, and how does it differ from a traditional WAN?**
Encrypted overlay tunnels over any mix of underlays (MPLS, Internet, LTE), managed by a central controller. Edges measure each tunnel's latency, jitter and loss continuously and steer each application to a path meeting its SLA class. A traditional WAN routes by static config and protocols that only see up or down, so it cannot react to a degraded link.

**Q29. Brownout vs blackout? What is BFD?**
A blackout is a dead path, easy to detect and fail over. A brownout is a path that is up but degraded (say 3% loss): routing still uses it, so only quality probes catch it, which is SD-WAN's main value. BFD (RFC 5880) sends lightweight hellos and declares a path down after a set number are missed, for example 300 ms x 3, under a second.

**Q30. Why do SD-WAN policies use hold-down timers and hysteresis?**
To avoid flapping. Moving traffic on every bad sample would bounce it between paths, causing reordering, session resets and voice glitches. So a path must stay bad for a while before traffic leaves, stay good for longer before traffic returns, and leave/return thresholds differ, exactly like hysteresis in alarm thresholds.

### E5. ITIL and NOC process

**Q31. Incident vs problem vs change vs service request?**
An incident is an unplanned interruption or degradation; aim: restore fast. A problem is the cause of one or more incidents; aim: remove it. A change is anything added, modified or removed that could affect services, so it is assessed and approved. A service request is a normal, pre-agreed request (report, new user); nothing is broken.

**Q32. How do you decide a ticket's priority?**
Impact (how many users, sites or customers, how critical the service) times urgency (how fast it must be fixed, whether a workaround exists). A whole site down with no backup in business hours is P1; the same site running on backup might be P2. I write the facts behind the priority in the ticket and reassess when they change.

**Q33. SLA vs SLO vs SLI vs KPI?**
SLI: the measurement (say, % of successful probe intervals). SLO: the internal target (99.95%). SLA: the contractual promise (99.9% with service credits); the SLO is tighter to leave margin. KPI: any performance metric, such as MTTR or % of tickets within SLA.

**Q34. What does 99.9% availability allow per month?**
0.1%: 43,200 min x 0.001 = 43.2 minutes in a 30-day month, about 8.76 hours a year. 99.99% allows about 4.3 minutes a month. I would also check how the contract defines downtime and what it excludes.

**Q35. Explain MTTA, MTTR and MTBF.**
MTTA: mean time to acknowledge. MTTR: mean time to repair, restore or resolve; I clarify which. MTBF: mean uptime between failures. Example: 3 outages totalling 6 h in 720 h gives MTTR 2 h, MTBF 714 / 3 = 238 h, availability 238 / 240 = 99.17%.

**Q36. What is "stop the clock"? Functional vs hierarchical escalation?**
Stop the clock pauses the SLA resolution timer while the provider genuinely cannot proceed because of the customer (say, no site access), with reason and times recorded; waiting on our own carrier normally does not pause it. Functional escalation moves the issue to more expertise (L2, L3, carrier, vendor); hierarchical goes up the management chain for decisions or resources. A P1 often needs both.

**Q37. What is a major incident, and how is it run?**
A very high-impact incident (say a core outage hitting many customers) with its own procedure: a major incident manager runs a bridge call with technical leads, a scribe keeps the timeline, a communications lead sends updates at fixed intervals, and every action has an owner and deadline. Afterwards: a post-incident review and an RFO.

**Q38. What are alarm correlation, de-duplication and flapping?**
Correlation links many alarms to one cause (all sites behind a failed PE), so we work one parent incident with linked children. De-duplication merges repeats into one alarm with a count. Flapping is a state toggling up and down; suppress the individual notifications, raise one flapping alarm, and suspect layer 1. Hysteresis and consecutive-sample rules stop noisy metrics from flapping alarms.

**Q39. What is RCA? What is a KEDB?**
Root cause analysis finds why an incident happened (timeline, 5 whys, fishbone), ends with corrective and preventive actions, and is blameless. A Known Error Database records known problems with symptoms and workarounds, so L1 can recognise a repeat and restore service quickly.

### E6. Scenario walk-throughs

Method: **scope -> physical/L1 -> L2 -> L3 -> DNS -> application**, then **ticket and communication**. Name each command and what each result would mean.

**Q40. "The customer says the internet is slow."**
- **Scope:** one user or the site? All websites or one? Since when, constant or at certain times? Wired or Wi-Fi? Contracted bandwidth?
- **L1/L2:** CPE interface errors and link utilisation on our dashboards. A full link means congestion; rising CRC errors mean a physical fault.
- **L3:** from a wired PC, `ping -n 100` to the gateway, the first ISP hop and an external target. Loss from the gateway onward points to LAN/CPE; from the ISP hop onward, to the access circuit. Then `tracert -d` or `mtr`, looking for latency that persists to the destination.
- **DNS:** time `nslookup`; slow lookups feel like slow Internet.
- **Application:** a speed test to a nearby server, and `curl -w` to split DNS, connect, TLS and server time. Only one site slow means that site or the path to it.
- **Ticket:** record measurements with timestamps, compare with the SLA, escalate to the carrier if the access circuit is at fault, give the next update time.

**Q41. "A whole site is down."**
- **Scope:** only this site, or several behind the same PE or region (one upstream cause, parent incident)? Any planned change?
- **L1:** is the CPE reachable, is the PE access interface down, is the LTE backup up? Both paths down suggests site power; call the site about power, CPE lights and building work.
- **L2/L3:** CPE answers but LAN does not: customer side. Interface up but BGP down: `show ip bgp summary` and logs.
- **Action:** dead access circuit: carrier ticket with evidence and an ETA. Suspected hardware: dispatch a field engineer.
- **Ticket:** P1 if no backup; frequent updates; stop the clock only for valid customer-side reasons; confirm restoration with the customer before closing.

**Q42. "There is packet loss on one link only."**
- **Scope:** which link, since when, all day or at peak, do other links on the same device or PE show it?
- **L1:** interface counters at both ends. Rising CRC/input errors mean physical (optic, cable, light levels): carrier or field team. Errors at one end help localise it.
- **L2/L3:** utilisation near 100% with output drops means congestion; check QoS drops and policing above the contracted rate.
- **Measure:** `mtr`/`pathping` across the link. Loss starting at the far end and persisting confirms it; loss at one middle hop only is rate limiting.
- **Ticket:** attach counters and graphs; congestion becomes a capacity discussion, errors a carrier or field ticket.

**Q43. "The website does not open, but ping works."**
Ping proves layers 1-3 to that IP, so move up.
- **DNS:** does `nslookup site` return the IP I pinged? If not, it is DNS.
- **L4:** `Test-NetConnection site -Port 443`. Quick refusal: service down on the server. Timeout: firewall.
- **TLS:** `curl -v` or `openssl s_client -servername` for expired, mismatched or incomplete certificates.
- **Application:** HTTP status in `curl -v`: 5xx is the server or back end; 403 may be a WAF or geo-block. Also check client proxy settings, and MTU if the page starts loading then stalls.
- **Ticket:** record which layer failed with output; route to the application owner if network checks pass.

**Q44. "DNS resolution fails for one domain only."**
- **Scope:** one PC or everyone? One resolver or all?
- `nslookup domain` against our resolver, then `nslookup domain 8.8.8.8`. Public works, ours fails: our resolver (stale cache, forwarding, filtering). Both fail: the domain's side.
- Read the code: NXDOMAIN (name missing, deleted, domain expired), SERVFAIL (broken authoritative servers or DNSSEC), timeout (reachability).
- `dig +trace domain` shows where delegation breaks; `dig @<authoritative server> domain` asks it directly.
- **Fix and ticket:** flush stale caches (PC or resolver) or inform the domain owner; record the exact code and the resolvers tested.

**Q45. "Voice calls are choppy, or there is one-way audio."**
Choppy is usually jitter or loss; one-way is usually NAT, firewall or routing.
- **Scope:** all calls or some, internal or external, one site, a time pattern? Get example calls with times and numbers.
- **Network:** `ping -n 200` or `mtr` from the site to the voice gateway for loss, variation and latency; check utilisation (congestion at peak means jitter).
- **QoS:** is voice marked EF and landing in the WAN priority queue? Check queue drops.
- **One-way audio:** RTP allowed both ways through firewalls? SIP ALG or NAT rewriting (a private IP in the call signalling is a classic cause)? Asymmetric routing?
- **Ticket:** attach per-call evidence (RTCP or phone-system MOS if available); escalate to the voice team or carrier with the examples.

**Q46. "Latency is high only during business hours."**
A time-of-day pattern points to congestion (queuing grows with load).
- Compare the latency graph with link utilisation; near 100% when latency rises means the link is full.
- Find the users of it: flow data or top talkers on the CPE, daytime backups or updates, new applications.
- Check QoS: are critical apps protected, any drops in the default queue?
- `mtr` at a busy and a quiet time shows where the latency begins: our access link or further along.
- **Ticket:** usually capacity, not a fault; share graphs and options (upgrade, QoS tuning, rescheduling bulk traffic, another path) with the customer and account team.

**Q47. "A link keeps dropping intermittently (flapping)."**
- **Scope:** how often, since when, any pattern? Get up/down timestamps from logs.
- **L1 first:** error counters, optical levels, site power events, loose or dirty connectors; flapping is very often physical.
- **L2/L3:** do routing/BGP sessions flap with the link or on their own?
- Correlate with weather, power and other sites on the same carrier segment.
- **Mitigate:** if a backup exists, consider shutting the unstable link so traffic stays on the backup (with customer agreement and a change record), and dampen alarms.
- **Ticket:** escalate to the carrier with flap timestamps; keep one ticket, not one per flap.

**Q48. "A certificate-expiry alarm fires."**
- **Verify:** `openssl s_client -connect host:443 -servername host | openssl x509 -noout -dates -subject` shows notAfter and which certificate is served; check every server behind the load balancer.
- **Assess:** days remaining set urgency; already expired means clients are failing now, so it is an outage with higher priority.
- **Act:** ticket to the certificate or application owner with host, expiry date and issuer; check why automatic renewal failed.
- **After renewal:** verify again including the chain (`-showcerts`, "Verify return code: 0"), and confirm monitoring covers every endpoint with enough warning.

**Q49. "SD-WAN keeps switching paths."**
- On the SD-WAN dashboard: which application class, which paths, how often, and which metric triggers each switch.
- One path genuinely unstable: treat it as a flapping link, check the underlay (ISP loss, LTE signal), raise it with that carrier.
- Both paths hovering at the threshold: policy too sensitive; review thresholds, averaging window, hold-down and hysteresis with the SD-WAN engineering team, through a change.
- Confirm the probes are healthy (probe loss can come from edge CPU or policing).
- **Ticket:** document the pattern, triggering metric, user impact with timestamps and the proposed change.

**Q50. "The customer reports an issue, but all our monitoring is green."**
Monitoring tests only what it is configured to test, from where it sits.
- Take it seriously; get specifics: users, application, error, time.
- Check coverage: we may probe the CPE WAN side while the problem is in the LAN, Wi-Fi, DNS, one application or a destination we do not probe.
- Test from the customer side: ping/traceroute from an affected PC, `nslookup`, `curl -v`, `Test-NetConnection`.
- Look at short time windows; a 5-minute average can hide 30-second drops.
- If it is outside our scope, say so politely with evidence and help route it; if it exposes a monitoring gap, add a probe. Record evidence either way.

**Q51. "Three alarms arrive at once. How do you prioritise?"**
- Acknowledge all three quickly so the team sees they are owned.
- Check if they are related (same device, site or upstream path); if so, it is one incident with one likely cause.
- If not, rank by impact and urgency: core or multi-customer outage first, then a single site down with no backup, then degradations and warnings. SLA timers and customer criticality break ties.
- Tell the shift lead and hand one to a colleague rather than handling three badly alone.
- Open or link a ticket for each so nothing is lost.

### E7. Behavioural and shift-work questions

Base these on your real experience; the notes give a structure, not a story to recite.

**Q52. Are you comfortable with night and rotational shifts?**
Yes. A NOC is 24x7 because networks do not fail only in office hours. I would keep a fixed sleep routine around the rotation and make handovers thorough, because tired people forget things and the written log protects the customer. Mention a genuine example of sustained late-night work if you have one.

**Q53. How would you handle an angry customer?**
Listen without interrupting, acknowledge the impact ("your whole branch is down; that is serious"), and do not argue about blame. Then take control: confirm the facts I need, say exactly what I am doing and when they will hear next, and keep that promise even with no news. If they need more than I can give, involve my shift lead, and note the conversation in the ticket.

**Q54. Tell me about a mistake you made.**
Pick a real, modest example from a project, internship or college work. Structure: situation, mistake, how you noticed, how you fixed it, what you changed so it cannot recur, spending most time on the fix and lesson. Lessons that map well to a NOC: announcing and recording changes before making them, double-checking before acting, and escalating early instead of hiding a problem.

**Q55. What do you do if you are stuck on an issue at 3 a.m.?**
Follow the runbook and KEDB, gather clean evidence (what I checked, outputs, timestamps), and escalate on time to the on-call L2 or shift lead, keeping the customer updated meanwhile. Staying stuck in silence is the real mistake; escalating with good notes is part of the job.


## Project-specific interview questions

These are the questions an interviewer is likely to ask about *this* project. The numbers
come from the measurement run and the demo (2026-11-23 23:36:31 to 2026-11-24 01:35:31); see `docs/FACTS.md` for
how each was produced.

**P1. Walk me through your project in two minutes.**
"noc-pulse is a small service-assurance tool I wrote in Python. A YAML inventory describes
monitored elements - my site gateway, DNS resolvers, three public anycast addresses I treat
as WAN paths, and four HTTPS sites including tatacommunications.com - plus the dependency
tree between them and five customer services with SLA classes. An asyncio collector probes
them politely: ICMP bursts, TCP connect, DNS query time and HTTPS with a DNS/TCP/TLS/TTFB
breakdown and certificate expiry. From the raw samples I compute availability, p50/p95
latency, jitter, loss and an E-model MOS for voice, and check them against the SLA. An
alarm engine applies hysteresis and suppresses child alarms when a parent is down, so one
fault opens one ticket. Tickets get P1-P4 from an impact x urgency matrix, response and
resolve SLA timers, and L1 diagnostics - ping, tracert, DNS, TCP, HTTP - attached
automatically. I ran it for 2.0 hours (2,738 samples), and I have a
fault-injection demo that shows alarm, correlation, ticket, diagnostics and resolution end
to end. There is also an SD-WAN simulation comparing static routing with SLA-aware
steering on the measured data. 90 unit tests run in GitHub Actions."

**P2. What did the measured data show?**
"2.0 hours, 2,738 samples, 9,520 ICMP echoes. RTT to the anycast resolvers was about 5-6 ms with 1-2 ms jitter, and the voice estimate was MOS 4.25 for the whole window. The interesting part was a real 2-3 minute ISP-side outage at 00:33: every Internet target failed while my gateway stayed up. The engine raised 10 alarms, suppressed 4 as symptoms and opened 6 tickets that auto-resolved within a minute - and it showed me that my inventory was missing an ISP-uplink node, otherwise those 6 tickets would have been one P1."

**P3. Why did you use p95 and not the average?**
Averages hide the tail. Eight packets at 5 ms and two at 12 and 30 ms average 8.4 ms, a
value no packet actually had; p95 tells you what the worst 5 % of users experience, which
is what people complain about. SLAs are usually written on percentiles for that reason.

**P4. How do you compute jitter?**
Inside each 10-echo burst I take the absolute difference between consecutive RTTs and
average it - that is the "mean deviation of D" from RFC 3550. RFC 3550's running estimator
J += (|D| - J)/16 is the smoothed version of the same quantity for long RTP streams; I
implemented and tested it too, but with 10 echoes it has not converged, so the KPI uses the
plain mean. Caveat: it is two-way (RTT) jitter, not one-way RTP jitter.

**P5. How do you get a MOS from ping?**
With the simplified ITU-T G.107 E-model: one-way delay = RTT/2 + 2x jitter for the
de-jitter buffer + 20 ms packetisation; delay impairment from Cole & Rosenbluth; loss
impairment from G.107 with G.711 + PLC constants; R = 93.2 - Id - Ie_eff; then MOS from R
with the G.107 Annex B formula. It is an estimate, not a measured call - it assumes a
codec and a buffer size - but it turns three network numbers into one number a voice
customer understands. Example: RTT 40, jitter 5, no loss gives R 92, MOS 4.38.

**P6. What is hysteresis and why do you need it?**
Raise an alarm only after N consecutive bad samples (3) and clear it only after M
consecutive good ones (2). One lost ping burst on Wi-Fi should not page an engineer at 3 am,
and a link that is "almost fixed" should not clear and re-raise every minute. The cost is
detection delay: about N x interval, so 60-90 s for the gateway ping.

**P7. How does correlation work? What if the parent alarm arrives after the child?**
Each target has `depends_on` in the inventory. When an alarm is raised, the engine looks
at the same target and its ancestors; if one of them already has an active
service-affecting alarm, the new alarm is suppressed and linked to that root cause, so no
new ticket. Because timing is never perfect, two more pieces: a 60 s correlation window
before any ticket is opened, and "late parent" handling - when a parent alarm is raised,
active child alarms below it are re-linked, and any child ticket already open becomes a
child incident of the parent ticket and is resolved with it. In the demo the child alarms
were actually raised a moment before the edge alarm and still ended as one P1 ticket.

**P8. What is the difference between de-duplication and flap suppression?**
De-duplication: the same condition keeps failing - one alarm with an occurrence count,
not one alarm per sample. Flap handling: the condition clears and comes back within 10
minutes - the same alarm (and ticket) is re-opened with flap_count + 1 instead of a new
one.

**P9. How did you decide the priority of a ticket?**
ITIL impact x urgency. Impact comes from the customer services the failing element
affects (gold voice or three or more services -> high). Urgency comes from alarm severity
(critical -> high). The matrix maps high x high to P1, etc. Each priority has a response
target (acknowledge) and a resolve target (restore): P1 is 15 min / 4 h in my config.

**P10. What does your auto-diagnostics do and why?**
Exactly what an L1 engineer would do first: ping and tracert to the target, resolve the
name via the site resolver and via 1.1.1.1, TCP connect to the service port, and an HTTP
GET. It then writes a one-line hypothesis by layer - e.g. "host answers ping but port
refused -> application or firewall issue". It saves the engineer the first five minutes
and puts evidence in the ticket before anyone opens it.

**P11. Tell me about the fault-injection demo.**
"I start three local listeners that stand in for an edge router, a web app and an API,
with the app and API depending on the edge. Then I stop all three. The edge alarm was raised
10.0 s after the fault; the app and API alarms were suppressed under it and only
one P1 ticket opened, with diagnostics attached. A scripted operator acknowledges after
5.1 s; I restore the listeners and the ticket auto-resolves - 14.0 s from
ticket open to resolved. In the second phase only the web app fails while the edge is
healthy, so its alarm is not suppressed and it gets its own P2 ticket. Those times are
seconds because the demo probes every 2 s on localhost - it proves the pipeline, it is not
an operational MTTR."

**P12. What did the SD-WAN simulation show?**
"Over 119 one-minute intervals static and SLA-aware steering both met the voice SLA in 98.32 % of intervals, because the only bad minutes were the ISP outage when all three paths failed together. It is a simulation on measured data: my three 'paths' are three
destinations reached over the same home broadband line, so they fail together and the
comparison mainly shows how the policies behave. On a synthetic brown-out in the tests, the
SLA-aware policy keeps 95.8 % of intervals in SLA versus 58.3 % for static routing, because
static routing only fails over when the primary is completely down."

**P13. Why is ICMP not a perfect measure of a service?**
Routers and some servers deprioritise or rate-limit ICMP, some hosts block it, and anycast
addresses answer from the nearest site, not from the server the application uses. ICMP
also does not see the application (a web server can answer ping and still return 500s).
That is why noc-pulse also measures TCP connect, DNS and HTTPS.

**P14. Why is HTTPS latency hundreds of ms when ping is 5 ms?**
The HTTPS number is DNS + TCP handshake + TLS handshake + server time to first byte. TCP
connect to the CDN edge is about 6 ms, so the network is fast; most of the time is the TLS
handshake and the server generating the page. That breakdown is exactly why I time each
phase separately.

**P15. How did you test it without a network?**
The KPI maths, alarm engine, correlation, ticket state machine, SLA timers, SD-WAN policies
and inventory validation are pure Python and take `ProbeResult` objects, so the tests build
synthetic samples (for example "the gateway fails three times while the app fails too")
and assert the outcome. The worked examples in the guide are the same numbers as the
tests. The probes themselves are exercised by real runs, not in CI.

**P16. How precise are your availability numbers?**
They are sample-based over a short window, and the sampling cannot
see outages shorter than the interval. I report the number of samples and the gaps so the
precision is visible: with 238 gateway samples, one failed sample moves the
figure by 0.42 percentage points. The more interesting numbers are the loss
and tail latency.

**P17. How would you scale this to 10,000 devices?**
Split collection from processing: many collectors (per region) push samples to a message
bus (Kafka), a time-series database (Prometheus/VictoriaMetrics/InfluxDB) stores them,
the alarm engine runs as a stream processor keyed by device, and the CMDB provides the
dependency tree. Add SNMP polling / streaming telemetry and syslog/trap ingestion rather
than only active probes, and push tickets to a real ITSM tool (ServiceNow) via its API.

**P18. What would you change for production?**
Persist the alarm engine state so a restart does not forget active alarms; business-hours
calendars and "stop the clock" (pending customer) in SLA timers; escalation and
notifications; authentication on the dashboard; per-customer reports; more vantage points
so one probe location cannot cause false alarms; and SNMP/telemetry data from the devices
themselves.

**P19. What was the hardest bug or decision?**
Good honest candidates:
(a) A correlation bug found in a code review: on one target, an HTTP "unreachable" alarm
was the root and a TCP "unreachable" alarm its symptom. If the HTTP alarm cleared and
re-raised (flapped) while the TCP alarm was still active, the chain walk TCP -> HTTP led
back to the HTTP alarm itself, so it got suppressed under *itself*, its ticket never
re-opened, and a later chain walk could loop forever. Fix: walk the chain only through
alarms that are still active, keep a visited set, and treat "the chain leads back to me"
as "I am the parent". Two regression tests reproduce both variants.
(b) Race between parent and child alarms: children can be raised a moment before their
parent. Solved with a correlation window before ticketing, late-parent re-linking, and
linking already-open child tickets once the parent's ticket opens.
(c) The G.107 MOS polynomial dips below 1 for very low R, so it is clamped (a test caught
it).
(d) Windows `ping` counts "Destination host unreachable" as a received reply, which would
hide an outage - the parser ignores replies without a time.

**P20. What did you learn about service assurance from building it?**
That most of the work is not measuring but deciding what is worth waking someone up for:
thresholds, hysteresis, correlation and priorities. And that every KPI needs a definition
written down (what counts as "up", over what window, percentiles of what), otherwise two
people get two different availability numbers from the same data.

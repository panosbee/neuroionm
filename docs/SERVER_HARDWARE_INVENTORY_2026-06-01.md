# Server Hardware And Capacity Inventory

Date: 2026-06-01  
Host: `medresearch-ai-dedicated`  
Scope: read-only hardware, resource, service, storage, network and database scan.

## Hardware

- OS: Ubuntu 24.04.4 LTS, kernel `6.8.0-111-generic`.
- Board: ASUSTeK WS C422 DC.
- CPU: Intel Xeon W-2295, 18 physical cores, 36 threads, 3.00 GHz base, up to 4.8 GHz reported by `lscpu`.
- RAM: 251 GiB available to Linux, 8 x 32 GB DDR4 2933 MT/s DIMMs.
- Swap: 31 GiB, unused during scan.
- Disk: Samsung NVMe `MZQLB1T9HAJR-00007`, 1.7 TB raw.
- Partitions:
  - `/`: 98 GB ext4, 12 GB used, 82 GB available.
  - `/var`: 1.6 TB ext4, 48 GB used, about 1.5 TB available.
  - `/boot`: 975 MB ext3, 203 MB used.
- Inodes: `/var` uses about 1%, root uses about 2%.
- CPU package temperature during scan: 34 C.
- Network link: 1 Gbps full duplex on `enp6s0`.

## Runtime Load Snapshot

- Uptime: about 26 days.
- Load average observed around `5.20 8.35 9.20` on a 36-thread machine.
- CPU snapshot under active MedDiscovery load: about 42% user, 8% system, 50% idle.
- I/O wait: 0% during scan.
- NVMe utilization: effectively 0-0.01% in sampled `iostat`; no disk pressure.
- Processes: 484.
- Threads: 1708.
- Open file table: 5360 allocated, far from kernel max.

## Service Resource Usage

All core services were active and had zero systemd restarts at scan time.

| Service | Memory Current | Notes |
| --- | ---: | --- |
| `neuroionm.service` | ~156 MB | 2 Gunicorn/Uvicorn workers on `127.0.0.1:8200`; very light. |
| `medresearch-ai.service` | ~526 MB | Python app on port `8000`; systemd `MemoryMax=4G`. |
| `discovery-api.service` | ~2.55 GB | Gunicorn Discovery API on `127.0.0.1:8001`; largest CPU user during scan. |
| `hypothesis-hub.service` | ~983 MB | 4 Gunicorn/Uvicorn workers on `127.0.0.1:8100`; `MemoryMax=8G`. |
| `mongod.service` | ~6.77 GB | MongoDB 7.0.22, bound to `127.0.0.1`. |
| `nginx.service` | ~130 MB | Public 80/443 reverse proxy. |
| `meddiscovery-heavy-worker@1..6` | ~295 MB each | Six workers active, each with `MemoryMax=40G`. |

Observed high CPU was from MedDiscovery API/workers, not NeuroIONM. One Discovery API worker was using high CPU during the scan, while total machine idle CPU was still about 50%.

## MongoDB

- Version: 7.0.22.
- Bind: `127.0.0.1:27017`.
- Connections: 290 current, 39 active, 0 rejected, about 50k available.
- Resident memory: about 5.8 GB.
- WiredTiger cache configured max: about 125 GB; currently using about 5.9 GB.
- Database footprint:
  - `medresearch`: about 3.56 GB on disk, 118 collections, 271k objects.
  - `neuroionm_platform`: about 1.23 MB on disk, 21 collections, 274 objects.
  - `medical_discovery`: about 0.14 MB on disk.

## Storage Footprint

- `/var/www`: 25 GB.
- `/var/www/Caroline`: 25 GB.
- `/var/www/Caroline/discovery`: 17 GB.
- `/var/www/Caroline/discovery/medical_discovery`: 8.6 GB.
- `/var/www/Caroline/venv`: 7.5 GB.
- `/var/www/Caroline/discovery/venv`: 3.4 GB.
- `/var/www/Caroline/discovery/models`: 2.9 GB.
- `/var/www/Caroline/discovery/logs`: 1.4 GB.
- `/var/www/sites/neuroionm`: 322 MB.
- `/var/www/sites/neuroionm/shared/.venv`: 148 MB.
- `/var/www/sites/neuroionm/releases`: 174 MB across 9 releases.
- `/var/lib/mongodb`: 4.0 GB.
- `/var/backups`: 17 GB.
- `/var/backups/medresearch`: 13 GB across 3 backups.
- `/var/backups/neuroionm`: 1.8 MB across 1 backup.
- `/var/log`: 1.6 GB.
- `/var/log/mongodb`: 558 MB.
- systemd journal: 813 MB.
- Docker: not installed.

## Network And Exposure

- Public firewall: UFW active.
- Public inbound allow rules: SSH 22, HTTP 80, HTTPS 443 only.
- MongoDB: localhost only.
- NeuroIONM: localhost only, exposed through Nginx.
- Discovery API: localhost only on `127.0.0.1:8001`.
- HypothesisHub: localhost only on `127.0.0.1:8100`.
- MedResearch app listens on `0.0.0.0:8000`, but external test to `65.21.194.83:8000` failed due firewall. This is acceptable short-term, but binding it to localhost is a useful future hardening step during a MedResearch maintenance window.

## Timers And Resilience

- `medresearch-backup.timer`: active, daily around 00:10 UTC.
- `neuroionm-backup.timer`: active, daily around 00:40 UTC.
- `neuroionm-healthcheck.timer`: active, every 5 minutes.
- `certbot.timer`: active.
- `logrotate.timer`: active.
- `apt-daily` and `apt-daily-upgrade`: active.

## Capacity Interpretation

The server is substantially over-provisioned for NeuroIONM's current expected load. NeuroIONM is tiny relative to the machine: about 156 MB RAM and 2 workers. For 3-5 parallel NeuroIONM users, the bottleneck is not hardware; it is external AI latency and any simultaneous heavy MedDiscovery CPU jobs.

Current safe stance:

- Keep NeuroIONM at 2 workers for now.
- Move to 3 workers only if real usage shows queueing or latency under concurrent AI/debrief usage.
- Do not raise workers aggressively without observing MedDiscovery load, because Discovery is the current dominant CPU consumer.

## Risks And Recommendations

1. Add offsite backups. Local backups are verified, but do not protect against total disk/server loss.
2. Add monitoring alerts for `/api/ready`, failed timers, disk usage, failed units, and backup failures.
3. During a MedResearch maintenance window, consider binding the MedResearch app to `127.0.0.1` instead of `0.0.0.0:8000` and keeping public exposure only through Nginx.
4. Add release cleanup policy for NeuroIONM if release count grows; current 9 releases use only 174 MB, so no immediate pressure.
5. Keep MedDiscovery CPU load in mind before scaling NeuroIONM worker count.
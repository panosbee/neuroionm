# NeuroIONM Server Operations

NeuroIONM shares the Hetzner host with MedResearch services. Server operations must therefore be isolated to NeuroIONM units, ports, paths and database names unless a wider maintenance window is explicitly planned.

## Production Layout

- App root: `/var/www/sites/neuroionm`
- Current release: `/var/www/sites/neuroionm/current`
- Shared env and venv: `/var/www/sites/neuroionm/shared`
- Service: `neuroionm.service`
- Internal bind: `127.0.0.1:8200`
- Public domain: `https://neuroionm.eu`
- Mongo database: `IONM_MONGO_DB` from `/var/www/sites/neuroionm/shared/.env.production`

## Recovery Layers

- `neuroionm.service` uses `Restart=always` so crashed workers are restarted by systemd.
- `neuroionm-healthcheck.timer` runs every five minutes and calls `/api/ready` on localhost.
- The watchdog restarts only `neuroionm.service`, only after repeated readiness failures, and rate-limits restarts to once every ten minutes.
- `neuroionm-backup.timer` runs daily at 00:40 UTC with randomized delay, after the MedResearch backup window.

## Local Backups

Backups are stored at `/var/backups/neuroionm` with root-only permissions. Each backup contains:

- Mongo archive from `mongodump`.
- Current application release tarball.
- NeuroIONM systemd/nginx/env config archive.
- `RESTORE_NOTES.txt` and `SHA256SUMS`.

Commands:

```bash
systemctl list-timers neuroionm-backup.timer neuroionm-healthcheck.timer
systemctl start neuroionm-backup.service
systemctl status neuroionm-backup.service --no-pager
ls -lah /var/backups/neuroionm/latest
```

Retention defaults to 21 days and can be changed with `NEUROIONM_BACKUP_RETENTION_DAYS` in the backup service environment.

## Health Checks

```bash
systemctl is-active neuroionm.service
curl -fsS http://127.0.0.1:8200/api/ready
curl -fsS -L --max-time 15 https://neuroionm.eu/api/ready
journalctl -u neuroionm.service -n 100 --no-pager
journalctl -u neuroionm-healthcheck.service -n 100 --no-pager
```

## Shared Server Safety

Before and after NeuroIONM changes, check adjacent services:

```bash
systemctl is-active medresearch-ai.service hypothesis-hub.service discovery-api.service mongod.service nginx.service
curl -fsS http://127.0.0.1:8001/openapi.json >/dev/null
curl -fsS -L --max-time 15 https://medresearch-ai.org/ >/dev/null
```

Do not restart `nginx.service`, `mongod.service`, or MedResearch units for a NeuroIONM-only change unless the specific change requires it and the adjacent services have been checked first.

## Restore Outline

1. Pick a backup under `/var/backups/neuroionm`.
2. Verify `SHA256SUMS`.
3. Restore the release into a new `/var/www/sites/neuroionm/releases/<release-id>` directory.
4. Point `/var/www/sites/neuroionm/current` to that release.
5. Restore Mongo with `mongorestore` using the documented archive path.
6. Restart only `neuroionm.service`.
7. Run local and HTTPS readiness checks.
# Hisho — Home Assistant Integration (install via HACS)

This repository hosts the **Hisho Home Assistant integration**. Hisho is a
local-first fleet operations platform: a control plane watches over a small fleet
of machines (machine reachability, service status, compute workers, power state)
and exposes that fleet over a private HTTP API. This integration makes
Home Assistant the dashboard for it.

## What you get

One hub device (`Hisho Control Plane`) plus one device per machine in your
fleet, with:

| Entity | Type | What it tells you |
|---|---|---|
| `sensor.hisho_fleet_health` | enum | `healthy` / `degraded` / `error` — the fleet verdict |
| `sensor.hisho_machines_reachable` | measurement | Machines answering |
| `sensor.hisho_machines_total` | measurement | Total machines in fleet |
| `sensor.hisho_services_active` | measurement | Active services count |
| `sensor.hisho_compute_workers` | enum | `all_ok` / `degraded` / `no_workers` |
| `sensor.hisho_last_fleet_sweep` | timestamp | When the watchdog last ran |
| `sensor.hisho_version` | diagnostic | Control-plane version |
| `binary_sensor.hisho_fleet_ok` | problem | On when machines are unreachable |
| `binary_sensor.hisho_services_ok` | problem | On when services are down |
| `binary_sensor.<machine>_reachable` | connectivity | Per-machine reachability |
| `binary_sensor.<service>` | running | Per-service active state |
| `sensor.<machine>_status` | enum | Per-machine `reachable` / `unreachable` |
| `sensor.<worker>_compute` | enum | Per-worker `ok` / `error` / `unknown` |
| `button.<worker>_compute_check` | button | Check compute worker status |
| `button.<worker>_compute_wake` | button | Wake compute worker |
| `button.<worker>_compute_sleep` | button | Sleep compute worker |
| `button.ipmi_<target>_power_*` | button | IPMI power on/off/cycle/status |

### Power action buttons

IPMI and compute buttons call the Hisho API with `execute=1` — the same
safety pattern used by the CLI. All power actions require the API token
configured during setup.

## Requirements

- A running Hisho control plane (Kihon), reachable from your Home Assistant host.
- The control plane's API port (default `8787`).
- An API token (optional but recommended for power actions).

If your control plane is only reachable over a VPN such as Tailscale, use its
VPN address when adding the integration.

## Install

### With HACS (recommended)

1. In Home Assistant, open **HACS**.
2. Open the three-dot menu (top right) → **Custom repositories**.
3. Paste `https://github.com/YepizTechJonathan/hisho-ha`, choose category **Integration**, and click **Add**.
4. Search HACS for **Hisho** and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration → Hisho**, enter your
   control plane's host, port, and optional token, then submit. The flow tests
   the connection before saving.

### Manually (no HACS)

Copy `custom_components/hisho/` into your Home Assistant `config/custom_components/`
directory, restart Home Assistant, then add the integration as in step 6 above.

## Options

After setup, use **Configure** on the integration entry to change the poll
interval (10–600 seconds).

## Fleet naming

This integration uses the Hisho naming scheme:

| Name | Role |
|---|---|
| **Kihon** (基本) | Sync/control machine (runs the control plane API) |
| **Souko** (倉庫) | Storage/warehouse machine |
| **Hashira Ren** (蓮) | GPU compute worker |
| **Hashira Ten** (天) | Compute worker (hermes-server role) |

## Troubleshooting

**"Could not reach a Hisho control plane at that address"** — check that the
API is running on the control plane and that the host/port are correct. From the
control plane itself, `curl http://127.0.0.1:8787/api/fleet-health` should
return JSON.

**Entities show as unavailable** — the control plane stopped answering. The
integration marks itself unavailable rather than reporting stale data; check the
control plane's service.

## Changelog

### v0.2.0 (2026-09-29)

- **Renamed** from NomadDeck to Hisho (domain `nomaddeck` → `hisho`).
- **Adapted** to live Hisho fleet-health payload shape (counts/machines/services/compute_workers).
- **Added** button platform: IPMI power actions + compute worker actions (check/wake/sleep), gated by token and `execute=1`.
- **Added** config flow token field for API authentication.
- **Added** per-service binary sensors and per-compute-worker sensors.
- **Direction change**: Hisho feeds HA (not HA polling a separate health endpoint).

### v0.1.1 (2026-09-17)

- Initial NomadDeck integration (read-only polling).

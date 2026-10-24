# Hisho for Home Assistant

Shows a [Hisho](https://github.com/YepizTechJonathan) fleet in Home Assistant: a **Hisho** hub device and one
device per machine, with its state, readings, sync and services. Home Assistant polls Hisho Core once a minute;
when Core stops answering, every entity goes unavailable instead of showing stale values.

## What you get

**Hisho** (hub)

| Entity | Kind |
|---|---|
| Machines online, Machines faulted | count |
| Sync problem | problem |
| Sync conflicts | count |
| Sync *folder* (one per synced folder) | in sync / needs attention / error |
| *Peer* sync (Syncthing peers that are not machines, e.g. a phone) | connectivity |
| *Worker* compute (one per compute worker) | running / blocked |

**Each machine** (linked to the hub)

| Entity | Kind |
|---|---|
| Online | connectivity |
| State | up / asleep / away / maintenance / unreachable / faulted |
| Memory usage, Disk usage, GPU usage (machines with a GPU) | % |
| Jobs running, Jobs queued | count |
| Sync connected | connectivity |
| Safe to shut down, Last boot, Containers running, Heartbeat age (off by default) | diagnostic |
| One sensor per Hisho service, on the machine that runs it | running |

A sleeping machine shows **Online: off** and **State: Asleep**; its readings are unavailable rather than zero.
Machines added to or retired from the fleet appear and disappear on their own.

## Requirements

- Hisho Core with the Home Assistant route (`GET /api/ha/snapshot`), reachable from Home Assistant.
  With Hisho on a tailnet, install the Tailscale add-on in Home Assistant and use Core's `https://<host>.<tailnet>.ts.net` name.
- A read-only token. On the Core machine:

  ```bash
  hisho home-assistant token
  ```

  It shows the token once and saves it as `HISHO_HA_READ_TOKEN`. It can read Hisho's state and nothing else.

## Install

### HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/YepizTechJonathan/hisho-ha`, type **Integration** → **Add**.
2. Find **Hisho** in HACS → **Download**.
3. Restart Home Assistant.
4. **Settings → Devices & services → Add integration → Hisho**. Enter Core's URL and the token.

### By hand

Copy `custom_components/hisho` into your Home Assistant `config/custom_components/`, restart, then step 4.

## When things change

- **Token rotated** (`hisho home-assistant token --rotate`): Home Assistant shows a *Re-authenticate* card; paste the new token.
- **Core moved to another address**: Settings → Devices & services → Hisho → ⋮ → **Reconfigure**.
- **Diagnostics**: Settings → Devices & services → Hisho → ⋮ → **Download diagnostics** (the token is redacted).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

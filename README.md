# Hisho for Home Assistant

Brings a [Hisho](https://github.com/YepizTechJonathan) fleet into Home Assistant with the controls of
Hisho's own dashboard: a **Hisho** hub device, one device per machine, one per box Hisho's add-ons know
(servers, VMs, storage), one per add-on, and Hisho's needs-you list as a to-do list. Home Assistant polls
Hisho Core once a minute; when Core stops answering, every entity goes unavailable instead of showing
stale values, and **Core reachable** turns off.

## Set up: no tokens

1. **Settings → Devices & services → Add integration → Hisho**, and type Hisho Core's address
   (with Hisho on a tailnet: its `https://<host>.<tailnet>.ts.net` name; install the Tailscale add-on in
   Home Assistant and add `100.100.100.100` to its DNS servers).
2. Home Assistant shows a code, like **K7Q-4ZP**.
3. In Hisho, **Needs you** (and your phone) shows *"Home Assistant asks to connect (code K7Q-4ZP)"*.
   Check the codes match and press **approve**.
4. Done. Home Assistant got its own key; Hisho keeps only its fingerprint.

What this Home Assistant may do is set in Hisho, on the **Add-ons** page under **Home Assistant**:
*see the fleet*, *use the controls*, *change Hisho's setup*. Revoke it there too; Home Assistant then asks
to approve it again. (`hisho home-assistant clients` does the same on the command line.)

A read-only token made with `hisho home-assistant token` still works in the optional token field.

## What you get

**Hisho** (hub): Core reachable · Needs you · Critical alert · Machines online / faulted · Jobs active /
failed · Projects active / waiting on you · Sync problem, conflicts, folders, phone peer · Compute workers ·
Fleet maintenance (switch) · Hide acknowledged · Clear phone notifications · Fail stuck jobs · the
**Needs you** to-do list · the **Alert** event · the **Phone app** notify target.

**Each machine**: Online · State · Memory / Disk / GPU usage · Jobs · Sync connected · Last boot ·
Parking (reserve machines) · **Wake** / **Park** · **Maintenance** · its services (switch: start/stop,
button: restart) · buttons add-ons offer for it.

**Each box an add-on knows** (TrueNAS, a Proxmox host, IONOS...): Power · Health · one sensor per thing
watched (a pool, a VM, updates) · its add-on buttons (start/shut down a VM...) · Maintenance.

**Each add-on**: Status (ok / needs a look / problem / needs setup / off) · Enabled · Maintenance ·
its actions.

Buttons that power something off or remove something start **disabled**: enabling one is your yes.
Hisho refuses those actions unless they are confirmed.

## Needs you

The **Needs you** to-do list is Hisho's list: ticking an item acknowledges it in Hisho (no more pushes
until it resolves), unticking un-acknowledges it. Claude's investigation shows in the item's description.
The **Alert** event fires *opened* / *resolved* for automations.

## Services

| Service | Does |
|---|---|
| `hisho.run_action` | Any of Hisho's controls (`action`, `target`, `args`, `confirm`); the list is the hub's *Actions allowed* sensor |
| `hisho.alert` | Acknowledge, un-acknowledge, snooze, re-notify or investigate a needs-you item |
| `hisho.maintenance` | Maintenance on/off for the fleet, a machine, a box or `addon:NAME`, with reason and duration |
| `hisho.job` | Cancel, retry, clear, dispatch or resume a job |
| `hisho.create_job` | Queue a job (e.g. a transcription) |

## Install

### HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/YepizTechJonathan/hisho-ha`, type **Integration** → **Add**.
2. Find **Hisho** in HACS → **Download**, restart Home Assistant, then set up as above.

Updates show in **Settings → Updates**.

### By hand

Copy `custom_components/hisho` into your Home Assistant `config/custom_components/`, restart, then set up.

## When things change

- **Revoked in Hisho, or Hisho reset**: a *Re-authenticate* card appears; submit, then approve in Hisho.
- **Core moved**: Settings → Devices & services → Hisho → ⋮ → **Reconfigure**.
- **Diagnostics**: Settings → Devices & services → Hisho → ⋮ → **Download diagnostics** (the key is redacted).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
```

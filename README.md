# swarbs-home

A tiny Flask app for a home NAS (built on an Odroid HC4) that does two things:

1. **Landing page** — a card homepage (derived from an old template inspired by album artwork & music posters) linking to OpenMediaVault, Transmission, the recorder, and whatever
   else you run.
2. **One-click DJ recorder** — a big Start/Stop button that records the USB
   audio codec (`hw:CODEC`) to `Music/Mixes` as date-stamped **FLAC**.

No auth — it's a LAN appliance. Reachable by name via mDNS at `http://nas.local/`.

## How the recorder works

Capture is two plain subprocesses wired by a pipe — no shell wrapper, no
systemd-run, no transient units. **Start** launches `arecord | flac`; **Stop**
sends SIGTERM to `arecord`, which finalises and closes its output, so `flac` sees
EOF and finishes the file cleanly. Audio is staged to a hidden `.incoming/` file
and **atomically moved** into `Music/Mixes` only when complete, so Syncthing never
sees a partial file. A recording in progress acts as the single-recording lock.

```
arecord -D hw:CODEC -f S16_LE -c 2 -r 44100 -t wav - | flac --best -o <name>.flac.part -
        └─ stop: SIGTERM arecord ─► flac hits EOF ─ finalise ─ mv ─► Music/Mixes/<name>.flac
```

## Develop locally (uv)

```bash
uv run swarbs-home serve --port 8000
# open http://localhost:8000
```

On a dev machine there's no `arecord`, so the recorder page shows "unavailable"
while the landing page works normally.

Without uv you can use a plain venv:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
swarbs-home serve --port 8000
```

## Install on the NAS (pipx)

```bash
git clone <your-fork> swarbs-home && cd swarbs-home
sudo ./install.sh
```

`install.sh` will:

- `apt install` any missing deps (`flac`, `alsa-utils`, `pipx`, `avahi-daemon`),
- `pipx install` the package into `/usr/local/bin`,
- install the config, the systemd unit and the avahi service,
- print the one manual step to move **OMV off port 80** so the dashboard can own
  `http://nas.local/`.

Upgrade later with `git pull && sudo ./install.sh`.

## Configuration

TOML at `/etc/swarbs-home.conf` (see `config/swarbs-home.conf.example`).
Resolution order: `--config` → `$SWARBS_HOME_CONFIG` → `./swarbs-home.conf` →
`/etc/swarbs-home.conf` → built-in defaults. Edit the `[[cards]]` list to change
the homepage links.

**Fonts** are not bundled (avoids redistributing licensed fonts). The UI uses a
system sans-serif by default. To use your own, copy a `.woff2`/`.woff`/`.ttf`/
`.otf` to the NAS and set `font_family` + `font_file` under `[server]`; the app
serves it at `/font` and applies it site-wide.

## Access without an IP

`avahi`/mDNS publishes the box as **`<hostname>.local`** — no extra DNS setup
needed; browse straight there. The example config uses `nas.local`; change the
card URLs to match your machine's host name. Optionally add a DHCP reservation on
your router so the IP stays stable as a fallback.

## Optional services behind the Music & Library cards

The **Music** and **Library** cards expect two extra services. Neither is
required for the dashboard or recorder; install them when you want them.

### Music — MPD + myMPD web UI

[myMPD](https://github.com/jcorporation/myMPD) is a single-binary web front end
for MPD. MPD plays the library; myMPD is the browser UI.

```bash
sudo apt install mpd mympd

# Point MPD at your library and give it an output, in /etc/mpd.conf:
#   music_directory  "/home/<user>/Music"
#   (plus an audio_output — e.g. your USB codec or an httpd stream; see mpd docs)
sudo systemctl enable --now mpd

# myMPD stores each setting as a file in its config dir. Move it off :80
# (the dashboard owns :80) to match the "Music" card (8533):
sudo systemctl stop mympd
echo 8533 | sudo tee /var/lib/mympd/config/http_port
sudo systemctl enable --now mympd
```

Browse to `http://nas.local:8533` (the **Music** card).

**Play recordings from the recorder page:** install the MPD CLI client too —
`sudo apt install mpc`. When MPD is reachable, each mix in the recorder's
"Recent mixes" list gets a ▶ button that appends the file to MPD's queue and
plays it (via an absolute `file://` URI, so MPD must be able to read the Mixes
folder). If MPD isn't running or `mpc` isn't installed, the buttons simply don't
appear.

### Library — beets web plugin

[beets](https://beets.io/) manages the library; its `web` plugin serves a
browsable/queryable UI (default port 8337).

```bash
pipx install beets           # or: sudo apt install beets

# ~/.config/beets/config.yaml
#   directory: /home/<user>/Music
#   plugins: web
#   web:
#     host: 0.0.0.0
#     port: 8337
```

Run it as a small service so the **Library** card is always live:

```ini
# /etc/systemd/system/beets-web.service
[Unit]
Description=beets web UI
After=network-online.target
Wants=network-online.target

[Service]
User=<user>
ExecStart=/home/<user>/.local/bin/beet web   # apt install -> /usr/bin/beet
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now beets-web
```

Browse to `http://nas.local:8337` (the **Library** card).

## CLI

```bash
swarbs-home serve [--host H --port P --config F --debug]
swarbs-home record status|start|stop [--label NAME]
```

## Layout

```
src/swarbs_home/     app.py · recorder.py · player.py · config.py · __main__.py
                     templates/  static/(styles.css)
config/              swarbs-home.conf.example · *.service (systemd, avahi)
install.sh
```

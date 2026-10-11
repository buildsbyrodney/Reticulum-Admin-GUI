# Reticulum Admin GUI

A Docker-based web administration and observability interface for Reticulum nodes. It provides a managed `rnsd` deployment, Dashboard, Interfaces, Paths, Announces, Queues, Logs, Timeline, labels, Blackholes, Incident Recovery, and a narrow internal management agent. Connected-client detail appears only where Reticulum exposes reliable data.

**Supported by design:** Linux with Docker Engine and Docker Compose v2 on `x86_64`, `aarch64`, and `arm64`. Primary target distributions are Debian 12/13, Raspberry Pi OS 64-bit where practical, and Ubuntu Server. **Raspberry Pi 3 ARM64 has been physically validated through successful container build and stack start on Debian 64-bit, with Docker 29.9.0, Docker Compose 5.6.0, and Reticulum rns 1.5.7.**

## Install on Ubuntu/Linux

```sh
wget https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh
chmod +x install.sh
./install.sh
```

Or:

```sh
curl -fLO https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh
chmod +x install.sh
./install.sh
```

The installer is a normal file you can inspect before execution. It downloads a tagged stable release archive, verifies SHA-256 before extracting, and does not normally install arbitrary application files from `main`.

The installer checks Docker and Compose, asks for a port (default `8740`), access mode (`local` is the default), and timezone (default `America/Chicago`). On Debian, Ubuntu, Raspberry Pi OS, and other Debian-derived systems, it offers to install missing Docker Engine and Compose v2 from Docker's official APT repository. The default interactive answer is Yes; non-interactive use requires `INSTALL_DOCKER=yes`. It checks an occupied port when `ss` is available, creates a restricted `.env`, generates a random `ADMIN_SECRET_KEY`, then builds and starts the services. It preserves an existing `.env` unless you explicitly choose to replace it.

If Docker is installed but the current account cannot reach its daemon, the installer offers to add that account to the `docker` group. It can start a new `docker`-group shell and resume the installer after verifying daemon access, or exit so you can log out and back in.

## Single-file installer workflow

Download the installer for inspection, then run it; do not use `curl | bash`:

```sh
wget https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh
chmod +x install.sh
./install.sh
```

The standalone installer downloads the tagged GitHub Release `.tar.gz` asset and matching checksum, verifies it using `sha256sum`, then extracts to `/opt/reticulum-admin` when permitted (otherwise `~/reticulum-admin`). It preserves `.env` and `data/` across replacement.

Rerunning from an installation offers Update, Reconfigure, Repair, or Cancel. Reconfiguration saves the existing `.env`; application data, identities, config, labels, admin database, timeline, incidents, and Reticulum storage are never removed by this installer.

## Raspberry Pi 3 / Debian Lite live-test checklist

1. Install Debian Lite 64-bit and confirm `uname -m` returns `aarch64`.
2. Download the standalone `install.sh`, inspect it, then `chmod +x install.sh && ./install.sh`. Accept the Docker installation prompt if Docker is not already installed.
3. Confirm `docker compose version`.
4. Run `docker compose ps`; create the first administrator and check Dashboard, Interfaces, Paths, Queues, Logs, and Timeline.
5. Restart containers, then reboot the Pi; confirm state remains in `data/` and services return.
6. Run `./scripts/diagnose.sh` and save its output if troubleshooting is needed.
7. Exercise only safe test Incident Recovery operations against a test Reticulum instance; record `docker stats` CPU/RAM usage.

The default observer interval is 30 seconds and the web server uses one worker for Pi-friendly idle use. The stack has now built and started successfully on a Raspberry Pi 3 running Debian ARM64; collect workload-specific memory figures separately.

For automation, `./install.sh --non-interactive` accepts `ADMIN_PORT`, `ADMIN_ACCESS_MODE`, and `SERVER_TIMEZONE` from the environment (or their defaults). If Docker is missing, it requires the additional explicit opt-in `INSTALL_DOCKER=yes`; it never replaces an existing `.env` in that mode.

Local mode binds to `127.0.0.1`; network mode binds to `0.0.0.0`. Network mode does not configure a firewall, HTTPS, NAT, or reverse proxy.

## Operations

```sh
docker compose ps
docker compose logs -f rnsd
docker compose restart reticulum-admin  # does not restart rnsd
docker compose restart rnsd             # explicit daemon restart
docker compose down                     # preserves data/
```

Do not run `docker compose run --rm rnsd` with its normal entrypoint for diagnostics: that attempts to launch another managed Reticulum instance against the same persistent state. v0.2.3 safely rejects the duplicate with a lifetime `flock` lock, but the correct inspection command is:

```sh
docker compose run --rm --entrypoint sh rnsd
```

The rnsd Docker health check reads the observer snapshot every 15 seconds (5-second timeout, 3 retries, 90-second Pi-friendly startup period). It is healthy only when a fresh snapshot matches the active rnsd generation and confirms the live shared Reticulum instance is ready. A launcher heartbeat alone is not sufficient.

The managed startup order is deliberate: acquire the state lock, start rnsd, verify that `rnstatus` reports the local Shared Instance, record the verified generation, then start the observer and control helper. On restart the observer/helper are stopped first and cannot probe or execute commands during the gap. If the candidate daemon connects as a client to another local instance instead of owning the shared instance, the launcher stops it and remains unhealthy.

Open the printed URL and create the required first administrator account.

## Observability

The `rnsd` container includes a non-networked observer that queries the local shared instance using `rnstatus --json --all --totals --queues --announce-stats --pr-stats` and `rnpath --table --json` every 15 seconds. It writes a sanitized snapshot into the persistent Reticulum directory. The GUI reads that snapshot only.

Available data depends on the installed Reticulum interface and version. Dashboard, Interfaces, Timeline, Connected Clients, and Logs show fields only when Reticulum explicitly supplies them. Queue, RX/TX, announce, and path-request values appear when available. IFAC passphrases are viewable in authenticated interface configuration pages so operators can distribute shared interface credentials; they remain redacted from logs, review diffs, change history, and displayed snapshot metadata. Recent logs are redacted before display.

SQLite lives at `data/admin/admin.db`. It stores password hashes, timeline events, connection history, labels/notes, and observer state. Timestamps are stored in UTC and rendered with `SERVER_TIMEZONE`.

### Client correlation limitation

Reticulum identities and destination hashes are not inherently TCP source IP addresses. An identity/destination is shown as **confirmed** only when Reticulum emits it in the same client/sub-interface record. Otherwise no association is guessed. Labels are operator annotations only; they do not admit, authenticate, ban, or blackhole anyone.

## Incident Recovery

Incident Recovery is explicit dynamic-state cleanup, not an automatic ban system. It provides a review step before applying selected actions: blackhole an identity (optionally for a duration and with a reason), drop one path, drop all paths via a transport identity, clear all queued announces, and attach/detach/reload a named interface when the installed Reticulum version supports the relevant command.

Blackholing affects this operator's own Reticulum network segments; it does not globally ban an identity. Dropping paths removes currently learned route state, but routes can be learned again if they continue to be announced. **Drop queued announces is broad local queue cleanup**, not peer-selective, and requires typing `DROP ANNOUNCES`. Detaching an interface requires typing `DETACH`. Recovery steps execute in a deterministic displayed order and stop at the first failure; the durable incident record is marked Completed, Partially completed, or Failed.

## Safe Configuration Changes and Snapshots

### Understanding Interfaces

The **Interfaces** page separates the connections you explicitly configured from Reticulum runtime objects. A configured interface is persistent and can be edited, cloned, or deleted through the reviewed snapshot-and-rollback workflow. Reticulum's `LocalServerInterface` and `LocalClientInterface` are system-managed runtime plumbing; they are shown separately with read-only details and cannot be changed through the GUI. Runtime data for a configured interface is merged into its one logical row—for example, `AutoInterface[Default Interface]` is shown as the configured **Default Interface**, not as a second interface.

The **Configuration** page contains node-wide settings, configuration snapshots, history, and a concise list of configured interfaces. Selecting **Edit** always opens that named interface's dedicated edit page; it never silently edits a generic or unselected interface.

### TCP guidance

TCP Server fields describe where this machine listens: `listen_ip` is a local machine address (not normally its public Internet address) and `listen_port` is the room number on that address. A TCP Client uses the remote server's reachable hostname/IP and the same port the server listens on. Creating a TCP Server does not open a firewall or router. For private trusted links, Tailscale IPs or MagicDNS names can be used without public router forwarding; Reticulum Admin only explains this pattern and never installs or configures Tailscale.

Before creating a TCP Client, obtain the remote operator's hostname or address, Listen Port, and—if used—the IFAC network name, passphrase, and any explicitly required custom IFAC size. A client uses the connection for this node's own traffic with Transport Mode left Off. If other nodes should use this machine as a route to that remote network, enable Transport Mode deliberately and provide a separate usable local-side interface such as TCP Server, AutoInterface, or RNode. The provisioning review repeats these consequences without changing topology automatically.

IFAC credentials are specific to each interface. They are never inherited from another configured interface. New-interface forms keep IFAC fields disabled until **Enable IFAC** is selected; an enabled IFAC requires both network name and passphrase. Supplying IFAC data while the checkbox is off is rejected before review.

Configuration writes are performed only by the internal management agent. The web container retains a read-only Reticulum mount and has no Docker socket or file-write endpoint. Supported changes are deliberately structured: Transport Mode, interface enabled state, interface mode, IFAC network name/passphrase/size, and TCP server/client endpoint fields. Every change follows review diff → automatic snapshot → candidate validation → atomic write → interface reload or internal rnsd restart → health check. A failed health check restores the previous configuration and records the rollback.

Configuration restart verification is deliberately bounded for Raspberry Pi hardware: restart verification has a 35-second monotonic deadline, the management-agent Gunicorn worker has a 90-second timeout, and the web-to-agent request has a 100-second timeout. This leaves enough time for one failed apply verification and one rollback verification without a worker being killed. The rnsd launcher increments a persistent generation after each surviving launch and prompts its long-running observer for an immediate probe. A restart is accepted only when the observer writes a fresh valid snapshot for that new generation; an older heartbeat or snapshot alone is never treated as confirmation. The management agent does not run `rnstatus` as a health probe because it is not in rnsd's local shared-instance context. If the web request cannot obtain a result, the GUI records **Result Unknown / Verification Timed Out** and asks the operator to check current state before retrying—it never automatically repeats a persistent change.

Snapshots include the Reticulum configuration plus redacted metadata and are stored in persistent Reticulum state. They restore configuration settings, not learned paths, announces, queues, or other dynamic routing state. Snapshot diffs never display IFAC passphrases; passphrase changes appear only as `Changed`. Unknown configuration lines, custom sections, and comments are preserved when supported keys are edited. The first write also creates `config.original` and never overwrites it.

### Physical Pi validation checklist for configuration

1. Create a manual snapshot with a note.
2. Toggle Transport Mode ON, confirm the management-agent health result, then toggle it OFF.
3. Disable AutoInterface and confirm its runtime state changes; re-enable it.
4. Change one safe interface setting and verify interface reload behavior.
5. Compare and restore a previous snapshot; inspect Timeline and Change History.
6. In controlled test mode only, submit an invalid candidate and confirm rollback without touching production settings.
7. Reboot the Pi and verify persistent configuration and snapshots remain available.

## Management-agent boundary

`management-agent` is a separate internal-only Docker service. It has no published host port and accepts requests only from the web service over an internal Docker network using a randomly generated installation token. It validates a small fixed allowlist, then writes structured JSON requests into the shared control directory. A small rnsd-side helper validates them again and executes predefined argument-array commands locally beside rnsd, returning a structured JSON result. This covers blackhole/unblackhole, path drops, queue clearing, and interface attach/detach/reload. There is no shell runner, Docker socket, SSH access, or arbitrary utility endpoint.

## Architecture boundary and future management agent

Today, `rnsd` owns config, keys, storage, and its local Reticulum control channel. The observer makes fixed read-only utility calls. `reticulum-admin` reads snapshots and Reticulum logs/config through a read-only mount, and owns its own SQLite database.

Future write operations should use a separate, narrowly scoped management-agent container or local Unix-socket service. It should expose authenticated, audited, allowlisted actions such as attach/detach/reload a named interface, apply validated configuration fields, manage blackholes, and restart `rnsd`—not shell execution or a Docker socket. The web app should call that API and never gain direct root or unrestricted Reticulum-directory access.

Future snapshots should capture Reticulum configuration, admin configuration, interface definitions/modes, and blackhole state before configuration changes, and support comparison. They must not promise to restore dynamic path tables or queues; Incident Recovery is the safe tool for dynamic-state cleanup.

## Roadmap

- Per-user/self-registration and identity-based admission control
- Multiple admin roles and MFA
- Remote multi-node administration
- Automatic blackholing and notifications
- HTTPS automation
- Existing/external `rnsd` management mode

## Layout

```text
compose.yaml                 two-service deployment
install.sh                   interactive installer
rnsd/observer.py             fixed read-only Reticulum observer
reticulum-admin/             Flask UI and persistent SQLite app data
data/                         runtime state, ignored by Git
```

## Interface provisioning and topology

The Interfaces page can create, clone, or delete TCP Server, TCP Client, and AutoInterface definitions through purpose-based presets. Every provisioning action is reviewed, snapshotted, validated, applied by the management agent, and health-checked before it is accepted. Interface names and configured TCP listen endpoints must be unique. Deletion requires typed confirmation.

The recommended starting pattern is Public TCP → Boundary, Local/RF → Internal, and Trusted Backbone → Full. It is a useful pattern, not a rule. Use separate IFAC credentials for ordinary user access and trusted backbone links. A TCP Server does not automatically become reachable from the Internet: firewall, NAT, and router configuration remain under the operator's control.

For a physical Pi test: create a manual snapshot, add a TCP Server on an unused port, review/apply and verify `rnstatus`, connect a test client, clone it to a different port, delete the clone, add and disable/re-enable a TCP Client, then restore the original snapshot and reboot. Also exercise duplicate name, duplicate port, invalid port, blank TCP target, invalid IFAC size, and a forced-health-failure test mode without modifying production state.

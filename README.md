# Reticulum Admin GUI

A Docker-based web administration and observability interface for Reticulum nodes. It provides a managed `rnsd` deployment, Dashboard, Interfaces, Paths, Announces, Queues, Logs, Timeline, labels, Blackholes, Incident Recovery, and a narrow internal management agent. Connected-client detail appears only where Reticulum exposes reliable data.

## Interface Discovery (v0.5.0)

**Discovery** is about learning connectable Reticulum interfaces advertised by other nodes; it is separate from ordinary destination announces. The Discovery page exposes the installed Reticulum 1.5.7 settings through the existing reviewed configuration lifecycle:

- `discover_interfaces` — listen for advertised interfaces. It remains off until an operator enables it.

Interface Discovery in the managed `rnsd` image requires LXMF. The image pins
`lxmf==0.9.6` alongside `rns==1.5.7`, checks both imports while building, and
reports the actual LXMF runtime capability to the admin UI. If LXMF is not
available, Discovery cannot be enabled; this avoids applying a configuration
that rnsd cannot start.
- `autoconnect_discovered_interfaces` — bounded automatic connection count. `0` means off.
- trusted discovery identity sources, preferred auto-connect mode, unverified implementation policy, and an optional discovery stamp requirement.
- TCP Server discovery fields: `discoverable`, `discovery_name`, `reachable_on`, `announce_interval`, `publish_ifac`, and encryption only when a pre-existing Reticulum network identity makes it available.
- `bootstrap_only` on compatible managed interfaces.

No discovery action opens a firewall, configures Tailscale, publishes a credential, trusts a source, or enables auto-connect by default. `reachable_on` accepts an address or hostname only—not Reticulum script paths. Manual connection to an individual advertisement is intentionally not exposed because the Reticulum 1.5.7 control path does not provide a narrow safe operation for it; create an explicit TCP Client for a manual link.

### Pi discovery checklist

1. Update to v0.5.0 and confirm existing configuration has not changed.
2. Open **Discovery**. Leave auto-connect at `0` while testing.
3. Enable Interface Discovery, review the diff, and apply it.
4. On one safe TCP Server interface, open **Edit → Advanced Discovery Controls**, set a friendly Discovery Name and a real externally reachable address, then explicitly enable Discoverable if desired.
5. Verify the Discovery table and Timeline after a compatible advertisement is received. Do not expect data until another compatible node advertises an interface.
6. Add known trusted discovery identities before enabling any auto-connect limit.
7. If testing a bootstrap connection, set only that interface to Bootstrap-only, then confirm normal rnsd health, observer status, and persistence after a reboot.
8. Keep `publish_ifac` off unless every peer and the discovery arrangement are intentionally trusted.

## Web request reliability

The admin web service runs one Gunicorn worker on a Pi, with two lightweight request threads and a two-second keep-alive. This prevents an incomplete browser connection from monopolising the only worker while a separate reviewed configuration request is waiting. The management agent remains single-worker and configuration operations retain their explicit bounded health/restart budgets.

## Diagnostics and LXMF Test Echo Bot

The **Diagnostics** page separates network-facing configuration checks from Reticulum runtime observations. It includes per-interface diagnostics, shared-instance health, and path inspection. A successful TCP-level check alone does not prove that Reticulum or IFAC is working; the page reports those layers separately.

`lxmf-test-bot` is a separate, non-administrative service with its own persistent LXMF identity. It uses Linux host networking solely to attach to the same local Reticulum shared-instance socket as `rnsd`; it publishes no ports and never has the management token, Docker socket, admin database, or configuration-write access. Its identity and LXMF destination persist under `data/bot/`.

Open **Bot**, copy or announce its LXMF destination, then message it from MeshChat. The bot is local/offline by default; it has no AI or Internet dependency and cannot administer the node remotely:

```text
MENU / HELP   → command list
PING          → PONG
STATUS        → safe read-only node summary
TEST, JOKE, DADJOKE, 8BALL <question>, FACT, COFFEE, RADIO
VERSE or VERSE John 3:16 → curated local KJV verse
```

The bot announces on startup and every 60 minutes by default; the GUI enforces a 15-minute minimum periodic interval. It stores only operational metadata such as counters and timestamps, not message bodies. After updating on a Pi: announce the bot, add or discover the destination in MeshChat, send `PING`, restart only `lxmf-test-bot`, confirm its destination is unchanged, and send `PING` again. Test remote peers through a known route only after local VM messaging succeeds.

The Bot settings include an announce tagline for future compatible LXMF runtimes. The pinned LXMF 0.9.6 runtime sends the normal Unicode display name but does **not** support transmitting a separate tagline in its announce API, so clients may show only the display name.

The Bot bundles a complete offline KJV verse catalog generated from [OpenBible's KJV text file](https://openbible.com/textfiles/kjv.txt), identified by that source as the King James Bible: Pure Cambridge Edition and attributed to BibleProtector.com. The generated verse text is preserved exactly from the source; only reference keys are normalized (for example, `Psalms` becomes canonical `Psalm`). The source's distribution guidance requires preserving attribution and exact Bible text; the generated catalog records that provenance. The generator stores only structured verse data, not the original text file. Run `node scripts/generate-kjv-library.mjs` to regenerate the catalog, or add `--check` to compare the committed catalog to the source; it fails on malformed lines, duplicate references, blank verses, missing canonical books, or implausible verse counts.

**Supported by design:** native Linux with Docker Engine and Docker Compose v2 on `x86_64`, `aarch64`, and `arm64`. Primary target distributions are Debian 12/13, Raspberry Pi OS 64-bit where practical, and Ubuntu Server. The managed `rnsd` service uses Linux host networking so it can bind real host interfaces; Docker Desktop and WSL do not provide the required equivalent behavior. **Raspberry Pi 3 ARM64 has been physically validated through successful container build and stack start on Debian 64-bit, with Docker 29.9.0, Docker Compose 5.6.0, and Reticulum rns 1.5.7.**

## Install on Ubuntu/Linux

```sh
wget -O install.sh https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh
chmod +x install.sh
./install.sh
```

Or:

```sh
curl -fL https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh -o install.sh
chmod +x install.sh
./install.sh
```

The installer is a normal file you can inspect before execution. It downloads a tagged stable release archive, verifies SHA-256 before extracting, and does not normally install arbitrary application files from `main`.

If Reticulum Admin is already installed, normally run the installed `./install.sh` from that installation directory and choose **Update**. This avoids downloading a second installer with a name such as `install.sh.1` and accidentally running an older copy.

The installer checks Docker and Compose, asks for a port (default `8740`), access mode (`local` is the default), and timezone (default `America/Chicago`). On Debian, Ubuntu, Raspberry Pi OS, and other Debian-derived systems, it offers to install missing Docker Engine and Compose v2 from Docker's official APT repository. The default interactive answer is Yes; non-interactive use requires `INSTALL_DOCKER=yes`. It checks an occupied port when `ss` is available, creates a restricted `.env`, generates a random `ADMIN_SECRET_KEY`, then builds and starts the services. It preserves an existing `.env` unless you explicitly choose to replace it.

If Docker is installed but the current account cannot reach its daemon, the installer offers to add that account to the `docker` group. It can start a new `docker`-group shell and resume the installer after verifying daemon access, or exit so you can log out and back in.

## Single-file installer workflow

Download the installer for inspection, then run it; do not use `curl | bash`:

```sh
wget -O install.sh https://raw.githubusercontent.com/buildsbyrodney/Reticulum-Admin-GUI/main/install.sh
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

## Messaging

Messaging provides a persistent LXMF identity hosted by the Reticulum Admin node. It can receive and store messages while a desktop or mobile client is offline, and lets an authenticated administrator read and reply from the web interface.

Messaging groups stored messages into Conversations. The Messaging → Announces tab is a separate, chat-oriented directory of recently observed `lxmf.delivery` announces; selecting an announced peer opens a text-only conversation, even before messages have been exchanged. Announce observation is passive: it does not connect, message, save, or trust a peer automatically.

The Messaging service also observes the documented public `lxmf.propagation` and `nomadnetwork.node` announce aspects for Network Map enrichment. NomadNet node app data is decoded only as NomadNet’s documented UTF-8 node name; propagation announces are classified without inventing a friendly name. These observations remain informational and read-only.

If a large number of past public announces makes **Network Map** difficult to use, its **Clear observed announce history** action removes only the local `lxmf.delivery`, propagation, and NomadNet observation records. It does not remove Reticulum paths, private messages, Contacts, Bot or Messaging identities, or Reticulum configuration. New public announces will appear again as they are received; historical announces are not replayed. The action is performed by the Messaging service and does not restart Reticulum or Docker containers.

The chat workspace uses small authenticated read-only polls instead of WebSockets: roughly every two seconds just after sending, then about every five seconds while viewing. This lets queued messages, delivery changes, new inbound messages, unread badges, and new announces appear without a manual refresh. It works with the normal page flow when JavaScript is unavailable and is designed to remain usable in mobile Safari.

Names are presentation hints, not authentication. A saved Contact name takes precedence, followed by the latest public LXMF announce name, then a display name supplied by an incoming message when the pinned runtime exposes one, then a shortened destination hash. The Messaging service only records delivery announces it receives after it starts. With the pinned RNS/LXMF APIs there is no supported safe enumeration of historic announce app-data, so it does not scrape logs or invent peers that another client may have retained.

The workspace adapts for phones and iPhone Safari: narrow screens show the conversation/announce list first, then a full-width conversation with a Back control and usable bottom composer. v0.7.2 remains text-only; it does not provide file, image, audio, voice, or other attachments.

If the messaging service restarts while an outbound message is Pending or Queued, it retries that same stored message once when the new service process starts. Delivered and Failed messages are never resent automatically. LXMF 0.9.6 does not provide a durable exactly-once transaction shared with the local message database, so an abrupt crash at the exact delivery boundary can theoretically result in a duplicate network delivery; retrying is preferred to silently losing a queued message.

My Identity is separate from the Test Echo Bot. Messages persist locally under `data/messaging/`; they are not copied into Timeline, Diagnostics, or generic logs. The server must be running to receive direct messages. LXMF clients can cache a display name until a later announce or rediscovery.

Web Push notifications are an explicit, per-browser or per-Home-Screen-app opt-in in **Messaging → My Identity**. Set `WEB_PUSH_VAPID_SUBJECT` in `.env` to a real HTTPS origin (for example your private HTTPS Admin GUI address) or a valid `mailto:` URI before sending notifications. Blank, localhost, and malformed values are rejected; there is no localhost fallback. The Messaging service stores its VAPID key at `data/messaging/webpush-vapid.pem` with owner-only permissions and stores each provider subscription in the Messaging SQLite database. The private key and browser subscription secrets are never returned by the Admin GUI. A new inbound LXMF message, once durably stored, can send a bounded plain-text sender/preview notification; outbound messages, state changes, and read/unread activity do not. iPhone and iPad support requires iOS/iPadOS 16.4+ and installation of the trusted HTTPS Admin GUI to the Home Screen. If the notification is tapped while the private Admin URL is unreachable, the cached page says **Connect Tailscale, then tap Retry.** It tests reachability only; it cannot inspect or enable a VPN. Attachments, group chat, and remote administration through LXMF messages remain out of scope.

The push payload follows WebKit’s Declarative Web Push format (`web_push: 8030`), while the installed service worker provides the compatible replacement/fallback notification path for older browsers. Messaging records only bounded provider-side diagnostics: attempts, accepted/rejected result, HTTP status, and stale-subscription removals. “Accepted by push provider” is not a claim that an iPhone displayed a notification. Foreground unread refreshes can also update an app badge, so a badge change alone is not evidence of background push receipt. The service worker does not send unauthenticated telemetry to the server; client receipt cannot be proven by the server.

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

### rnsd host networking on Linux

On supported native Linux hosts, `rnsd` and its tightly scoped local shared-instance clients (`lxmf-test-bot`, `lxmf-messaging`, and `nomad-browser`) use `network_mode: host`. The clients use it only to reach Reticulum's namespace-local shared-instance socket; they publish no ports and mount Reticulum state read-only. This lets `rnsd` see the server's actual LAN and Tailscale interfaces instead of a Docker bridge interface, and lets dynamically configured TCP Server interfaces bind directly to their configured address and port. No Reticulum TCP ports are declared in Compose in advance.

That means `listen_ip = 0.0.0.0` listens on all host IPv4 interfaces, `listen_ip = 192.168.1.85` listens only on that assigned LAN address, and `listen_ip = 100.x.x.x` listens only on that assigned Tailscale address. A TCP Server interface still does not open a host firewall, router, or Tailscale policy. If an explicit listen address is not assigned to the server, the GUI rejects it when a current observer snapshot can verify that fact.

The web UI remains on its controlled published-port bridge network. The management agent remains internal-only on the private `management` network with no published port. Neither service communicates directly with rnsd over Docker DNS: observer status, control requests/results, config, and persistent Reticulum state use the existing shared-volume boundary. There is still no Docker socket in the UI or agent.

Host networking removes Docker bridge isolation as one possible obstacle to AutoInterface seeing real LAN/link-local interfaces. It does **not** guarantee that AutoInterface can autoconfigure: host IPv6/link-local configuration, multicast behavior, and local network conditions still decide that. Verify actual AutoInterface behavior on the physical Pi before relying on it.

To revert host networking without touching Reticulum state, stop the stack, restore the prior `compose.yaml` from the installer backup (or remove the `network_mode: host` entry under `rnsd`), then recreate the stack:

```sh
cd ~/reticulum-admin
docker compose down
# Restore compose.yaml from the backup created by the installer, or remove rnsd's network_mode: host line.
docker compose up -d --build
```

`data/reticulum` and `data/admin` are not removed by this procedure.

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

### Path floods and Paths by Transport

**Paths by Transport** groups the current Reticulum path table by the exact `via` transport hash reported for each destination. A destination hash identifies one learned route target; the `via` transport hash identifies the route source currently used for that path. A large concentration through one `via` is an observation worth reviewing, not proof that the transport is malicious. The display retains only a few destination samples so it stays usable during large incidents.

Use **Review drop-via** when one exact transport is responsible for routes you deliberately want to remove. It pre-fills the existing Incident Recovery review and does not execute anything by itself. **Drop queued announces** clears this node's local announce queues globally; it does not block a peer permanently and cannot stop a continuing source from learning routes again. Blackholing thousands of destination hashes is usually the wrong response to a path flood: destinations are learned route state, not necessarily verified identities or the common source of the paths.

## Safe Configuration Changes and Snapshots

### Understanding Interfaces

The **Interfaces** page separates the connections you explicitly configured from Reticulum runtime objects. A configured interface is persistent and can be edited, cloned, or deleted through the reviewed snapshot-and-rollback workflow. Reticulum's `LocalServerInterface` and `LocalClientInterface` are system-managed runtime plumbing; they are shown separately with read-only details and cannot be changed through the GUI. Runtime data for a configured interface is merged into its one logical row—for example, `AutoInterface[Default Interface]` is shown as the configured **Default Interface**, not as a second interface.

The **Configuration** page contains node-wide settings, configuration snapshots, history, and a concise list of configured interfaces. Selecting **Edit** always opens that named interface's dedicated edit page; it never silently edits a generic or unselected interface.

### TCP guidance

TCP Server fields describe where this machine listens: `listen_ip` is an address assigned to this server (not normally its public Internet address) and `listen_port` is the room number on that address. On the managed native-Linux deployment, rnsd uses host networking: `0.0.0.0` means all host IPv4 interfaces, a `192.168.x.x` address means that LAN address only, and a `100.x.x.x` address means that host's Tailscale address only. A TCP Client uses the remote server's reachable hostname/IP and the same port the server listens on. Creating a TCP Server does not open a firewall or router. For private trusted links, Tailscale IPs or MagicDNS names can be used without public router forwarding; Reticulum Admin only explains this pattern and never installs or configures Tailscale.

Before creating a TCP Client, obtain the remote operator's hostname or address, Listen Port, and—if used—the IFAC network name, passphrase, and any explicitly required custom IFAC size. A client uses the connection for this node's own traffic with Transport Mode left Off. If other nodes should use this machine as a route to that remote network, enable Transport Mode deliberately and provide a separate usable local-side interface such as TCP Server, AutoInterface, or RNode. The provisioning review repeats these consequences without changing topology automatically.

IFAC credentials are specific to each interface. They are never inherited from another configured interface. New-interface forms keep IFAC fields disabled until **Enable IFAC** is selected; an enabled IFAC requires both network name and passphrase. Supplying IFAC data while the checkbox is off is rejected before review.

Configuration writes are performed only by the internal management agent. The web container retains a read-only Reticulum mount and has no Docker socket or file-write endpoint. Supported changes are deliberately structured: Transport Mode, interface enabled state, interface mode, IFAC network name/passphrase/size, and TCP server/client endpoint fields. Every change follows review diff → automatic snapshot → candidate validation → atomic write → interface reload or internal rnsd restart → health check. A failed health check restores the previous configuration and records the rollback.

Configuration restart verification is deliberately bounded for Raspberry Pi hardware: restart verification has a 70-second monotonic deadline, the management-agent Gunicorn worker has a 170-second timeout, the web-to-agent request has a 180-second timeout, and the web worker has a 195-second timeout. This allows a slow candidate to fail, Docker to relaunch it, and—if needed—a full health-verified rollback, without an outer request timing out first. The rnsd launcher increments a persistent generation after each surviving launch and prompts its long-running observer for an immediate probe. A restart is accepted only when the observer writes a fresh valid snapshot for that new generation; an older heartbeat or snapshot alone is never treated as confirmation. The management agent does not run `rnstatus` as a health probe because it is not in rnsd's local shared-instance context. If the web request cannot obtain a result, the GUI records **Result Unknown / Verification Timed Out** and asks the operator to check current state before retrying—it never automatically repeats a persistent change.

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
compose.yaml                 rnsd, private management agent, and web UI deployment
install.sh                   interactive installer
rnsd/observer.py             fixed read-only Reticulum observer
reticulum-admin/             Flask UI and persistent SQLite app data
data/                         runtime state, ignored by Git
```

## Interface provisioning and topology

The Interfaces page can create, clone, or delete TCP Server, TCP Client, and AutoInterface definitions through purpose-based presets. Every provisioning action is reviewed, snapshotted, validated, applied by the management agent, and health-checked before it is accepted. Interface names and configured TCP listen endpoints must be unique. Deletion requires typed confirmation.

The recommended starting pattern is Public TCP → Boundary, Local/RF → Internal, and Trusted Backbone → Full. It is a useful pattern, not a rule. Use separate IFAC credentials for ordinary user access and trusted backbone links. A TCP Server does not automatically become reachable from the Internet: firewall, NAT, and router configuration remain under the operator's control.

For a physical Pi test: create a manual snapshot, add a TCP Server on an unused port, review/apply and verify `rnstatus`, connect a test client, clone it to a different port, delete the clone, add and disable/re-enable a TCP Client, then restore the original snapshot and reboot. Also exercise duplicate name, duplicate port, invalid port, blank TCP target, invalid IFAC size, and a forced-health-failure test mode without modifying production state.

## Network Map

Network Map is a read-only, local-observation graph. It draws the managed shared instance, interfaces, proven inbound TCP clients, local Bot and Messaging identities, known Reticulum paths, and observed LXMF delivery, LXMF propagation, and NomadNet node applications. Exact destination hashes correlate these capabilities into one node; selecting a node shows the exact local fields behind it. Double-clicking only opens a relevant existing Admin page when the relationship is clear.

It is intentionally conservative: a destination hash or public application name is not proof of a person, a physical device, or a verified Reticulum identity. The map does not infer route controls or claim remote topology that the managed runtime has not explicitly exposed. A Nomad-only node may open the separate, read-only Nomad Browser. Announced peers are passive observations, and their hop count is shown as unavailable when the most recent announce did not provide a reliable value.

## Nomad Browser

Nomad Browser is an optional, read-only client for public NomadNet pages. It uses a dedicated `nomad-browser` container that attaches to the managed Reticulum shared instance as a client; it does not run a second Reticulum master, host a Nomad node, publish a port, receive a management token, or write Reticulum configuration. Select an observed `nomadnetwork.node` destination (or enter its exact 32-character destination hash), then explicitly request a `/page/` path. The browser page polls its local state while the bounded path discovery, link establishment, and page request run, so a completed page or safe error appears without a manual refresh.

Remote page content is untrusted. This browser displays returned UTF-8 Micron content as escaped plain text: it does not execute scripts, submit forms, render HTML, or download files. It has its own persistent Browser identity under `data/nomad/`, separate from the Bot, Messaging, Admin, and rnsd identities. Public browsing is anonymous by default. For an identity-protected Nomad page, copy the Browser identity hash shown in the UI to the page owner and explicitly select **Identify this browser to the selected node** before loading it. This reveals that Browser identity to that selected node, but never any other local service identity. The public node name comes from its announce and is informational only, not proof of identity. NomadNet pages use Reticulum Link request/response on the `nomadnetwork.node` aspect; this implementation follows that documented model without embedding the NomadNet terminal UI. [NomadNet](https://github.com/markqvist/NomadNet) is upstream beta software.

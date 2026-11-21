---
name: blockhost
description: Install, configure, verify, upgrade, back up and troubleshoot Blockhost, the open-source Minecraft Bedrock server manager, on Windows, Linux, Docker, or k3s/Kubernetes. Use when the user wants to host a Minecraft Bedrock server, set up Blockhost, deploy it to a cluster, or fix a Blockhost install.
---

# Blockhost setup skill

Blockhost (https://github.com/buschmjm/blockhost) runs Mojang's Bedrock Dedicated Server (BDS) and gives it a web admin page on port 8080. Players connect on UDP 19132.

## Rules you must follow

1. **Never accept the Minecraft EULA for the user.** Before anything sets `BLOCKHOST_ACCEPT_EULA=true` or clicks the accept box, show the user https://www.minecraft.net/eula and get an explicit "yes, I accept". If they don't answer, leave it unset; they can accept on the web page.
2. **Ask before exposing anything to the internet** (router port forwards, public LoadBalancers, ingress hosts, `--host 0.0.0.0` on a machine with a public IP).
3. **Passwords:** ask the user for the admin password or generate one and show it to them once. Only store it in Blockhost's config (via the setup page or `BLOCKHOST_PASSWORD`) or in a Kubernetes Secret. Never write it to a repo, a log, or a shell history file you create.
4. **One server per data folder/volume.** Never run two Blockhost instances on the same data, and never scale the Deployment above 1.
5. Check the hardware first: BDS needs **x86_64 (amd64)** and **Windows 10+ or glibc Linux**. On ARM (Raspberry Pi, Apple Silicon, ARM VPS) stop and tell the user it can't run there.

## Pick a target

| Situation | Path |
|---|---|
| User's own Windows PC | A. Windows |
| Linux desktop or a Linux server without Docker | B. Linux binary |
| Docker or Docker Compose available | C. Docker |
| k3s / Kubernetes cluster (kubectl works) | D. Kubernetes |

Detect with: `uname -m` (must be `x86_64`), `docker version`, `kubectl get nodes -o wide` (check the `kubernetes.io/arch=amd64` label).

### A. Windows

1. Download `https://github.com/buschmjm/blockhost/releases/latest/download/blockhost-windows.exe` to a folder the user chooses (e.g. `%USERPROFILE%\Blockhost`).
2. Optionally verify it against `SHA256SUMS` from the same release (`Get-FileHash`).
3. Tell the user to double-click it (SmartScreen: More info → Run anyway; firewall: allow Private networks). The browser opens with the setup code filled in.
4. Guide them: set password → tick EULA (their decision) → Download and start.
5. For LAN management: run with `--host 0.0.0.0`. For autostart: shortcut in `shell:startup` with `--no-browser`.

### B. Linux binary

```bash
mkdir -p ~/blockhost && cd ~/blockhost
curl -fL -o blockhost https://github.com/buschmjm/blockhost/releases/latest/download/blockhost-linux
chmod +x blockhost
ldconfig -p | grep -q libcurl.so.4 || echo "install libcurl4 first"
```

Headless: run as a systemd user service (see docs/linux.md in the repo) with `ExecStart=%h/blockhost/blockhost --host 0.0.0.0 --no-browser` and `TimeoutStopSec=90`, then `loginctl enable-linger $USER`. The setup code is in `~/.local/share/blockhost/setup-code.txt`.

### C. Docker

```bash
docker run -d --name blockhost --restart unless-stopped \
  -p 8080:8080 -p 19132:19132/udp -p 19133:19133/udp \
  -v blockhost-data:/data --stop-timeout 90 \
  ghcr.io/buschmjm/blockhost:latest
docker logs blockhost | grep "Setup code"
```

Add `-e BLOCKHOST_ACCEPT_EULA=true` only after rule 1. Add `-e BLOCKHOST_PASSWORD=...` to skip the setup code. Compose file: `compose.yaml` in the repo.

### D. Kubernetes / k3s

```bash
kubectl create namespace blockhost
kubectl -n blockhost create secret generic blockhost-admin --from-literal=password='<password>'   # optional
kubectl apply -k "https://github.com/buschmjm/blockhost//deploy/kubernetes?ref=main"
kubectl -n blockhost rollout status deploy/blockhost --timeout=180s
```

- EULA (after rule 1): `kubectl -n blockhost set env deploy/blockhost BLOCKHOST_ACCEPT_EULA=true`.
- Web page: `kubectl -n blockhost port-forward svc/blockhost-web 8080:8080`, or an Ingress with TLS (`deploy/kubernetes/ingress.example.yaml`; ask about the host name).
- Game: `kubectl -n blockhost get svc blockhost-game`. No LoadBalancer support? Switch it to `NodePort`.
- Storage: the PVC uses the default StorageClass; ask the user if they want a specific one.

## Verify (all targets)

1. `curl -s http://<host>:8080/auth/status` returns JSON with `"version"`.
2. After the EULA is accepted, the web page shows **Server online** within ~2 minutes (first download is ~100 MB). Logs contain `Server started.`
3. UDP 19132 is listening: `ss -ulnp | grep 19132` (Linux), `Get-NetUDPEndpoint -LocalPort 19132` (Windows), or the Service has an address (Kubernetes).
4. Tell the user how players connect: the **Join this server** card on the Worlds tab shows the local address (and the internet address once set up) with copy buttons; players use Play → Servers → Add Server. Consoles (Xbox, PlayStation, Switch) can't add custom servers. In containers, set `BLOCKHOST_LAN_ADDRESS` so the local address is right.

## Operate

- **Update the game server:** web page → "Update to x.y.z" button (keeps worlds).
- **Update Blockhost:** replace the binary / `docker pull` + recreate / bump the image tag in kustomization and re-apply. Data persists.
- **Backup:** copy the whole data folder (`%LOCALAPPDATA%\Blockhost`, `~/.local/share/blockhost`, the `/data` volume, or the PVC) while stopped; or download worlds from the World library.
- **Forgot password:** stop Blockhost, remove `password_hash` from `config.json`, start, use the new setup code.

## Troubleshoot

| Symptom | Check |
|---|---|
| "server exited during startup" | Activity tab → Problems; logs/server.log. On Linux: missing libcurl. Port 19132 already in use by another server. |
| Install says platform unsupported | ARM or macOS. Needs x86_64 Windows/Linux. |
| Players can't connect from outside | Network tab (ask the user first; it changes their router or uses a third-party service): **Open port** for UPnP, or the playit.gg tunnel if the router refuses or the provider uses CGNAT. Then **Test from the internet**. See docs/networking.md. |
| Same-PC Minecraft can't connect (Windows) | Use 127.0.0.1; for the Store version run the `CheckNetIsolation` loopback command from docs/windows.md. |
| Mod fails to load | Mods tab "missing dependency" tag; upload the missing pack. Activity → Problems shows pack errors. |
| Web page 401 loop | Session expired or Blockhost restarted; sign in again. |

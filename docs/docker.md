# Blockhost in Docker

The image is `ghcr.io/buschmjm/blockhost` (linux/amd64). It contains Blockhost only. The Bedrock server is downloaded into the `/data` volume the first time you accept the Minecraft EULA, and survives image upgrades.

## docker run

```bash
docker run -d --name blockhost --restart unless-stopped \
  -p 8080:8080 -p 19132:19132/udp -p 19133:19133/udp \
  -v blockhost-data:/data \
  --stop-timeout 90 \
  ghcr.io/buschmjm/blockhost:latest
docker logs blockhost        # one-time setup code
```

## docker compose

Use [`compose.yaml`](../compose.yaml) from this repo: `docker compose up -d`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BLOCKHOST_ACCEPT_EULA` | `false` | `true` accepts the [Minecraft EULA](https://www.minecraft.net/eula) and downloads the server on start. Only set it if you have read and accept it. Otherwise accept it on the web page. |
| `BLOCKHOST_PASSWORD` | unset | Admin password (8+ chars). Used only if no password exists yet. Skips the setup code. |
| `BLOCKHOST_PORT` | `8080` | Web page port inside the container. |
| `BLOCKHOST_AUTH` | `password` | `none` turns off Blockhost's login. Only behind a reverse proxy that does its own authentication. |

## Upgrading

```bash
docker pull ghcr.io/buschmjm/blockhost:latest && docker rm -f blockhost && docker run ... (same command)
```

The Bedrock server itself updates from the web page (**Update to x.y.z** button).

## Backups

Stop the container (or use **Download** on a world in the library) and copy the volume: `docker run --rm -v blockhost-data:/data -v "$PWD":/backup alpine tar czf /backup/blockhost.tgz -C /data .`

## Notes

- Keep `--stop-timeout` / `stop_grace_period` at 60s or more so the world saves on shutdown.
- On ARM hosts (Raspberry Pi, Apple Silicon) the server won't run. Mojang ships x86_64 only.
- Put HTTPS in front of port 8080 (Caddy, Traefik, nginx) before exposing it beyond your LAN.

## Players over the internet

With the default bridge network, **Open port** (UPnP) can't reach your router; run with `--network host` or use the playit.gg tunnel instead. Set `BLOCKHOST_LAN_ADDRESS` to your computer's LAN address so the dashboard shows the right local address. See [networking.md](networking.md).

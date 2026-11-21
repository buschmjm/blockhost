# Blockhost

**A server manager for Minecraft Bedrock Edition.** Run it on a Windows or Linux PC, in Docker, or on Kubernetes, then manage everything from a web page: worlds, mods, players, game rules and the live console.

> Blockhost is not an official Minecraft product. It is not approved by or associated with Mojang or Microsoft.

## What it does

- **Hosts the server for you.** Downloads Mojang's official Bedrock Dedicated Server (after you accept the Minecraft EULA), runs it, restarts it if it crashes, and updates it with one click while keeping your worlds.
- **World library.** Upload `.mcworld` files or whole world folders, switch between worlds, create new ones from a seed with a step-by-step wizard, and download backups.
- **Mods.** Upload `.mcaddon` / `.mcpack` files, turn them on per world (required packs come along automatically), and change mod values with simple forms: mob health and damage, spawn rates, item stack sizes and durability, recipes on/off, loot and villager trades.
- **Players.** Allow list, roles (visitor / member / operator), and per-player game mode.
- **Live world.** Time, weather, difficulty, game rules and a console, without restarting.
- **Activity.** Who joined and left, plus a problems list that surfaces pack and script errors.
- **Join addresses.** The dashboard shows the local and internet addresses with copy buttons. One click opens the port on your router (UPnP / NAT-PMP), or sets up a free [playit.gg](https://playit.gg) tunnel when your provider blocks port forwarding (CGNAT). See [docs/networking.md](docs/networking.md).

## Install

| You want to... | Use | Guide |
|---|---|---|
| Host from your Windows PC | `blockhost-windows.exe` from [Releases](https://github.com/buschmjm/blockhost/releases) | [docs/windows.md](docs/windows.md) |
| Host from a Linux PC | `blockhost-linux` from [Releases](https://github.com/buschmjm/blockhost/releases) | [docs/linux.md](docs/linux.md) |
| Run it in Docker | `ghcr.io/buschmjm/blockhost` | [docs/docker.md](docs/docker.md) |
| Run it on k3s / Kubernetes | `deploy/kubernetes/` | [docs/kubernetes.md](docs/kubernetes.md) |
| Have an AI agent set it up | [`skills/blockhost/SKILL.md`](skills/blockhost/SKILL.md) | [docs/agents.md](docs/agents.md) |

Quickest start (Docker):

```bash
docker run -d --name blockhost -p 8080:8080 -p 19132:19132/udp -v blockhost-data:/data ghcr.io/buschmjm/blockhost:latest
docker logs blockhost   # shows the one-time setup code
```

Open http://localhost:8080, enter the setup code, choose a password, accept the Minecraft EULA, and the server downloads and starts. Players on your network connect to your machine's address on port **19132**; the dashboard shows it with a copy button. For friends over the internet, see [docs/networking.md](docs/networking.md).

## Requirements

- **64-bit x86 (Intel/AMD) CPU, Windows 10+ or Linux.** Mojang only publishes the Bedrock server for those. Raspberry Pi, ARM boards and Macs can't run it.
- About 2 GB of RAM for a small world with a few players, more with big mod packs.
- Players on Windows, Android and iOS can add your server by address. Xbox, PlayStation and Switch have no "add server" option for custom servers.

## Security in one paragraph

Blockhost has its own login. On first start it prints a one-time **setup code**; whoever has it creates the admin password, so nobody else on your network can claim the server first. The desktop builds only listen on `localhost` unless you choose otherwise. Don't expose the web page to the internet without HTTPS in front of it. See [docs/security.md](docs/security.md).

## Data

Everything lives in one folder: `%LOCALAPPDATA%\Blockhost` on Windows, `~/.local/share/blockhost` on Linux, `/data` in containers. Back that folder up and you have backed up everything.

```
config.json        password hash and settings
server/            Bedrock server, server.properties, the running world
library/worlds/    saved worlds
library/packs/     uploaded mods
library/tweaks/    your mod setting changes
logs/server.log    server output
```

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Blockhost is plain Python with no dependencies; `python -m blockhost` runs it from a checkout.

## License

MIT. See [LICENSE](LICENSE). Minecraft is a trademark of Mojang Synergies AB. The Bedrock Dedicated Server is Mojang's software under its own license and is downloaded directly from minecraft.net, never redistributed by this project.

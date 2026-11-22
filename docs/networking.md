# Letting players join

The **Worlds** tab shows two join addresses, each with a copy button:

- **Local network:** this computer's address on your Wi-Fi or LAN, plus the game port (19132 unless you changed `server-port`). Anyone on the same network can join with it right away.
- **Internet:** the address for everyone else. Until you set up one of the options below on the **Network** tab, it shows your public IP, detected automatically. A status line under it says whether that address actually answers from the internet (checked through [mcsrvstat.us](https://mcsrvstat.us) every few minutes). Your public IP only works for friends once UDP 19132 is forwarded to this computer.

Players add either one in Minecraft under **Play → Servers → Add Server** (Windows, Android, iOS). Xbox, PlayStation and Switch can't add custom servers.

## Option 1: open the port on your router (UPnP)

Press **Open port** on the Network tab. Blockhost asks your router (UPnP, or NAT-PMP on routers that use it) to forward UDP 19132 to this computer. It renews the forward every 20 minutes and removes it when you press **Close port** or stop Blockhost. The router's public address then shows on the dashboard.

It won't work when:

- UPnP is turned off on the router. Turn it on in the router's settings, or forward UDP 19132 by hand and use option 3.
- Your internet provider uses **CGNAT**: it shares one public address between many customers, so nothing from outside can reach your router. Blockhost detects this (the router's address differs from your real public address) and tells you to use option 2.
- Blockhost runs in Docker with bridge networking, or in Kubernetes. The router can't be reached from inside the container network. Use `--network host`, option 2, or option 3.

## Option 2: playit.gg tunnel

[playit.gg](https://playit.gg) is a free tunnel service that gives you a public address even behind CGNAT. It's a separate service with its own terms.

1. Press **Set up tunnel**. Blockhost downloads playit's agent (version 2.0.2, from playit's GitHub releases, checked against a fixed SHA-256 before it runs).
2. Open the link it shows and approve Blockhost in your playit.gg account (create a free account if you don't have one).
3. On playit.gg, add a tunnel of type **Minecraft Bedrock** pointing at local address `127.0.0.1` and port `19132`, using the Blockhost agent.

Blockhost picks up the tunnel's address within a minute and shows it on the dashboard. The agent runs only while Blockhost runs and the tunnel is turned on. **Unlink** deletes the key from this computer; delete the agent and tunnel on playit.gg as well if you're done with them.

The agent ships for 64-bit Linux and Windows, the same systems the Bedrock server runs on. Files: `<data>/playit/` (agent and key), `<data>/logs/playit.log`.

## Option 3: you set it up yourself

Already forwarded the port by hand, run a VPN, or own a domain name? Type the address players should use (for example `mc.example.com:19132`) and press **Save**. **Use my public IP** fills in your current public address. A saved address always takes priority on the dashboard; clear the box and save to remove it.

## Checking it works

**Test from the internet** asks [mcsrvstat.us](https://mcsrvstat.us) to reach your Internet address. Testing from inside your own network often fails even when friends can join (many routers don't support "hairpin" connections), so this is the reliable check. Its results are cached for about five minutes.

## Containers

Inside Docker or Kubernetes, the detected local address is the container's, not your computer's. Set `BLOCKHOST_LAN_ADDRESS` to the address players on your network should use (for example your computer's `192.168.x.x` address, or the LoadBalancer IP).

## What Blockhost sends where

Nothing below happens until you press the matching button.

| Feature | Contacts |
|---|---|
| Open port | Your router, on your local network only |
| Open port (CGNAT check), Use my public IP | `api.ipify.org`, to learn your public address |
| playit.gg tunnel | `github.com` (agent download), `api.playit.gg`, and playit's tunnel servers |
| Test from the internet | `api.mcsrvstat.us`, with your Internet address |

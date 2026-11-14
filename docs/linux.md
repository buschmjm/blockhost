# Blockhost on Linux

Needs a 64-bit x86 machine with glibc (Ubuntu, Debian, Fedora, Arch and similar). The Bedrock server needs `libcurl`, which most desktops already have (`sudo apt install libcurl4` on Debian/Ubuntu if not).

## Run it

```bash
curl -L -o blockhost https://github.com/buschmjm/blockhost/releases/latest/download/blockhost-linux
chmod +x blockhost
./blockhost
```

Your browser opens with the setup code filled in. Choose a password, accept the Minecraft EULA, and the server downloads and starts. Data lives in `~/.local/share/blockhost`.

From source instead: `pip install git+https://github.com/buschmjm/blockhost` then `blockhost`.

## Headless server (no desktop)

```bash
./blockhost --host 0.0.0.0 --no-browser
```

The setup code is printed in the output and saved to `~/.local/share/blockhost/setup-code.txt`. Open `http://<server-ip>:8080` from another device.

## Run as a service

`~/.config/systemd/user/blockhost.service`:

```ini
[Unit]
Description=Blockhost (Minecraft Bedrock server manager)
After=network-online.target

[Service]
ExecStart=%h/blockhost --host 0.0.0.0 --no-browser
Restart=on-failure
TimeoutStopSec=90

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now blockhost
loginctl enable-linger "$USER"   # keep it running after you log out
journalctl --user -u blockhost -f
```

## Firewall

Open UDP 19132 for players and TCP 8080 for the web page on your LAN, e.g. `sudo ufw allow 19132/udp` and `sudo ufw allow from 192.168.0.0/16 to any port 8080 proto tcp`.

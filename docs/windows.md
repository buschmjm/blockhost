# Blockhost on Windows

1. Download `blockhost-windows.exe` from the [latest release](https://github.com/buschmjm/blockhost/releases/latest).
2. Double-click it. Windows SmartScreen may warn that the app is unrecognized, because the build isn't code-signed. Click **More info → Run anyway**. (You can check the file against the `SHA256SUMS` on the release page.)
3. A console window opens and your browser opens Blockhost. The setup code is already filled in. Choose an admin password.
4. Tick the box to accept the Minecraft EULA and click **Download and start the server**.
5. When Windows Firewall asks about `bedrock_server.exe`, allow **Private networks** so people in your home can join.

Keep the console window open while you host. Closing it, or pressing **Ctrl+C** in it, saves the world and stops the server.

## Connecting

- **Same home network:** in Minecraft, go to **Play → Servers → Add Server**, and enter this PC's local address (run `ipconfig`; look for IPv4 Address, e.g. `192.168.1.20`) with port `19132`.
- **Same PC:** use address `127.0.0.1`. If the Microsoft Store version of Minecraft can't see a server on the same PC, run this once in an admin PowerShell:
  `CheckNetIsolation.exe LoopbackExempt -a -n="Microsoft.MinecraftUWP_8wekyb3d8bbwe"`
- **Friends over the internet:** open the **Network** tab and press **Open port** (your router forwards UDP 19132 for you), or set up the free playit.gg tunnel if that doesn't work. The dashboard then shows the internet address to share. See [networking.md](networking.md) and [security.md](security.md).

## Managing from your phone

By default the web page only listens on this PC. To reach it from other devices at home, start it from a terminal with:

```powershell
blockhost-windows.exe --host 0.0.0.0
```

then open `http://<pc-address>:8080` on the phone. Allow the firewall prompt for Blockhost on Private networks.

## Start with Windows

Press **Win+R**, type `shell:startup`, and put a shortcut to `blockhost-windows.exe` in that folder. Add `--no-browser` to the shortcut's target if you don't want a browser tab each time.

## Where things are

`%LOCALAPPDATA%\Blockhost`. Copy that folder to back up everything. Use `--data-dir D:\Blockhost` to keep it somewhere else.

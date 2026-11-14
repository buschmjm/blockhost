# Security model

Blockhost controls a game server and its files. Anyone signed in can run server console commands, upload mods (which contain scripts that the Bedrock server runs), and replace worlds. Protect access accordingly.

## Login

- **First run:** no password exists, so the only way in is the one-time **setup code** printed in the console and written to `<data>/setup-code.txt`. This stops someone else on your network from claiming the server before you do. The desktop builds open the browser with the code filled in.
- **Password:** 8+ characters, stored as a PBKDF2-SHA256 hash (600,000 iterations, random salt) in `config.json`. Changing it signs out every other session.
- **Sessions:** random 256-bit tokens kept in memory (restarting Blockhost signs everyone out), sent as `HttpOnly; SameSite=Strict` cookies, plus `Secure` behind HTTPS.
- **Brute force:** after 3 wrong passwords from one address, each further attempt waits longer (up to 5 minutes).
- **Forgot the password?** Stop Blockhost, delete the `password_hash` line from `config.json`, start it again, and use the new setup code.

## Browser protections

- Every request that changes something must include the `X-Blockhost: 1` header. Other websites can't add that header to requests to your server, which blocks cross-site request forgery.
- A strict `Content-Security-Policy`, `X-Frame-Options: DENY` and `nosniff` are sent on every response.

## Network exposure

| Setup | Default web address | Advice |
|---|---|---|
| Windows / Linux desktop | `127.0.0.1:8080` | Only this computer. Use `--host 0.0.0.0` for your LAN. |
| Docker / Kubernetes | `0.0.0.0:8080` in the container | Publish it on your LAN only, or behind HTTPS. |

Blockhost speaks plain HTTP. Over the internet, put a TLS reverse proxy (Caddy, Traefik, nginx, Cloudflare Tunnel) in front of it so the password isn't sent in clear text.

`BLOCKHOST_AUTH=none` disables Blockhost's login entirely. Use it only when a reverse proxy in front already requires login, and nothing can reach port 8080 except that proxy.

## Game port

UDP 19132 is the Bedrock game protocol. It's Mojang's server, not Blockhost's code. Turn on the **allow list** (Players tab) if the port is reachable from the internet, so only people you add can join.

## Mods

Bedrock add-ons can include JavaScript that runs inside the Bedrock server's script sandbox. Only install mods from sources you trust.

## Reporting

See [SECURITY.md](../SECURITY.md).

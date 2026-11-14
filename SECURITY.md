# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub's **Report a vulnerability** button on the Security tab of this repository. Don't open a public issue. We aim to reply within a week.

## What Blockhost protects, and how

See [docs/security.md](docs/security.md) for the full model. In short:

- One admin password, stored as a salted PBKDF2-SHA256 hash.
- First-run setup requires a one-time code printed to the console and saved to `setup-code.txt` in the data folder.
- Session cookies are `HttpOnly` and `SameSite=Strict`, and `Secure` when served over HTTPS through a proxy that sets `X-Forwarded-Proto`.
- Every write request must carry an `X-Blockhost` header (CSRF guard).
- Login attempts are rate limited per address.
- The web page sends a strict Content-Security-Policy and refuses to be framed.
- Mod setting edits are limited to known numeric fields and toggles inside known pack folders; uploads are unpacked with zip-slip checks.

Anyone with the admin password can run console commands on the game server and replace its files. Treat it like a server root password.

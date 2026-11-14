# Notes for coding agents working on this repo

- To **install or operate** Blockhost for a user, follow `skills/blockhost/SKILL.md`.
- Code: `blockhost/` (stdlib-only Python). `app.py` = HTTP API + world/mod logic, `bds.py` = Bedrock server download and process, `auth.py` = login, `mod_settings.py` = mod setting forms, `ui/index.html` = the whole web UI.
- Test: `python -m pytest -q` and `ruff check .`. Tests use a fake server; never download Mojang's server in tests or CI.
- Don't add runtime dependencies. Don't commit Mojang binaries, Minecraft assets, logos, or fonts.
- Never set `BLOCKHOST_ACCEPT_EULA=true` on a user's behalf.

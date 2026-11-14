# Contributing

Thanks for helping. Blockhost aims to stay small, dependency-free, and easy for non-experts to run.

## Run from source

```bash
git clone https://github.com/buschmjm/blockhost && cd blockhost
python -m blockhost --data-dir ./dev-data
```

Python 3.10+ only; no packages to install. The UI is a single file, `blockhost/ui/index.html`, served as-is.

## Tests

```bash
python -m pip install pytest
python -m pytest
```

Tests use a fake Bedrock server script, so they don't download anything or need the EULA.

## Guidelines

- No new runtime dependencies without discussion in an issue first. The desktop build and Docker image rely on the standard library only.
- Never commit or redistribute Mojang's server binary, Minecraft assets, logos or fonts.
- Keep user-facing text plain and friendly; the audience is often a parent setting up a server for their kids.
- Security-sensitive changes (auth, file paths, uploads) need a test.

## Releases

Push a tag like `v0.2.0`. GitHub Actions builds the Windows `.exe`, the Linux binary and the Docker image, and attaches them to the release.

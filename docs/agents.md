# Letting an AI agent set up Blockhost

This repo ships an agent skill at [`skills/blockhost/SKILL.md`](../skills/blockhost/SKILL.md). It tells a coding agent (Claude Code, Codex, Cursor, and others that read skill or instruction files) how to install, configure, verify, upgrade and back up Blockhost on Windows, Linux, Docker, or Kubernetes.

## Claude Code

```bash
mkdir -p ~/.claude/skills/blockhost
curl -L -o ~/.claude/skills/blockhost/SKILL.md \
  https://raw.githubusercontent.com/buschmjm/blockhost/main/skills/blockhost/SKILL.md
```

Then ask: *"Set up a Blockhost Minecraft server on this machine"* or *"Deploy Blockhost to my k3s cluster"*.

## Other agents

Point the agent at the raw SKILL.md URL above, or paste its contents into the agent's instructions. It is plain Markdown.

## What the agent will and won't do

- It will ask you before accepting the Minecraft EULA. Accepting a license is your decision, so the skill never sets `BLOCKHOST_ACCEPT_EULA=true` on its own.
- It will ask before opening ports to the internet or changing router/firewall settings.
- It never prints or stores your admin password anywhere except Blockhost's own config or a Kubernetes Secret you approve.

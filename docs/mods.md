# Mods and mod settings

## Installing mods

1. Download `.mcaddon` or `.mcpack` files (for example from MCPEDL or CurseForge). Marketplace content won't work: it's locked to the account that bought it.
2. **Mods** tab → **Upload**. Several files at once is fine.
3. **Worlds** tab → **Mods** on a world → tick the mod → **Apply**. On the running world this restarts the server.

If a mod needs other packs (a "library" pack, or its resource pack), Blockhost turns those on too when they're in your library. If one is missing, the mod shows **missing dependency**.

## Changing mod values

Most Bedrock add-ons have no settings file. Their values live in the JSON files that define mobs, items, recipes and so on. **Mods → Settings** finds those values and shows them as simple forms:

| Tab | What you can change |
|---|---|
| Spawns | spawn weight (0 = never), group size, how many can be nearby |
| Mobs | health, attack damage, speed, knockback resistance |
| Items | stack size, durability, damage, food points |
| Recipes | turn individual recipes off |
| Loot | rolls, drop weights, drop counts |
| Trades | villager prices, amounts, uses before restock |

Changes are saved as small patches in `library/tweaks/<pack-id>.json`, applied to every world that uses the mod, and re-applied whenever the mod is (re)installed in a world. The mod's original files are never modified, so **Reset this tab** always gets you back to the original.

Restart the server (top right) for changes to take effect.

Some mods keep their options in an in-game menu or inside their scripts. Those don't show up here; change them in game.

## Raw file editor

**Worlds → Files** lets you edit any JSON or text file inside a world's packs directly. Those edits are overwritten when the world's mods are changed, so prefer **Settings** where possible.

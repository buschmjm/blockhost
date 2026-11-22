import { world } from "@minecraft/server";

// One parseable line per action: [GRIEF] <action> | <player> | <dimension> x y z
const WATCH_USE = {
  "minecraft:lava_bucket": "lava",
  "minecraft:flint_and_steel": "fire",
  "minecraft:fire_charge": "fire",
};
const WATCH_PLACE = { "minecraft:tnt": "tnt", "minecraft:lava": "lava" };

function log(action, player, block) {
  const l = block.location;
  console.warn(`[GRIEF] ${action} | ${player.name} | ${block.dimension.id.replace("minecraft:", "")} ${l.x} ${l.y} ${l.z}`);
}

world.afterEvents.playerInteractWithBlock.subscribe((ev) => {
  if (!ev.isFirstEvent) return;
  const item = ev.beforeItemStack;
  const action = item && WATCH_USE[item.typeId];
  if (action) log(action, ev.player, ev.block);
});

world.afterEvents.playerPlaceBlock.subscribe((ev) => {
  const action = WATCH_PLACE[ev.block.typeId];
  if (action) log(action, ev.player, ev.block);
});

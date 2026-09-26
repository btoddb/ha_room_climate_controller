import type { RoomClimateControlConfig } from "../types";
import { getRoomsSync, roomMetaByKey } from "./store";
import type { RoomPresetConfig } from "./types";

/** Map a card config to its integration room key.

Prefers an explicit `profile_room_key`, then matches the configured manual-mode
entity against a room's manual-mode entity from the websocket store.
*/
export function resolveProfileRoomKey(config: RoomClimateControlConfig): string | undefined {
  const explicit = config.profile_room_key?.trim();
  if (explicit) return explicit;

  const manual = config.manual_mode?.trim();
  if (manual) {
    const byManual = getRoomsSync().find(
      (r) => r.entities.manual_mode === manual
    );
    if (byManual) return byManual.key;
  }

  // Legacy fall-back: input_boolean.<key>_climate_manual_mode naming.
  const m = config.manual_mode?.match(/^input_boolean\.([a-z0-9_]+)_climate_manual_mode$/);
  if (m && getRoomsSync().some((r) => r.key === m[1])) return m[1];
  return undefined;
}

/** Live room helpers from the card config (for copy-current-settings). */
export function buildLiveRoomFromConfig(
  config: RoomClimateControlConfig,
  roomKey: string
): RoomPresetConfig | undefined {
  const meta = roomMetaByKey(roomKey);
  if (!meta) return undefined;

  return {
    name: meta.label,
    roomKey,
    has_heating: meta.has_heating,
    has_fan: meta.has_fan,
    useCooling: config.use_ac,
    useHeating: meta.has_heating ? config.use_heater : undefined,
    fanOverride: config.ac_fan_only_override,
    cooling: config.target_cooling,
    heating: meta.has_heating ? config.target_heating : undefined,
    fans: config.fans.map((f) => ({
      slug: f.slug,
      label: f.label,
      // The live room has no stored preset scalars; the clipboard builder reads
      // the current on/temp/reverse straight from these entities' states.
      use: false,
      temp: null,
      reverse: false,
      reversible: f.reversible,
      useEntity: f.use,
      tempEntity: f.target,
      reverseEntity: f.reversible ? f.reverse : "",
    })),
  };
}

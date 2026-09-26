/** Resolve a minimal user config (`room`) into a fully-populated card config by
reading the integration's room metadata from the websocket `rooms/list` cache.

This is what replaces hand-wiring every sensor/helper entity in the dashboard:
the integration is the single source of truth, the card just picks a room. */
import { getRoomsSync, roomMetaByKey } from "./profiles/store";
import type { WsRoomLive } from "./profiles/api";
import {
  DEFAULT_OUTDOOR_SENSOR,
  DEFAULT_TIME_RANGE,
  defaultConfig,
  type RoomClimateControlConfig,
  type RoomClimateUserConfig,
} from "./types";

const EMPTY_LIVE: WsRoomLive = {
  use: null,
  target: null,
  medium_offset: null,
  high_offset: null,
};

/** The room key a card points at: explicit `room`, else legacy `profile_room_key`. */
export function resolveRoomKey(user: RoomClimateUserConfig): string | undefined {
  return user.room?.trim() || user.profile_room_key?.trim() || undefined;
}

/** True once the rooms cache has been populated at least once. */
export function roomsAvailable(): boolean {
  return getRoomsSync().length > 0;
}

/** True if `key` matches a configured integration room. */
export function roomKnown(key: string): boolean {
  return getRoomsSync().some((r) => r.key === key);
}

/** Build the full render config from the chosen room's integration entities.

Returns undefined when no room key is set or the room isn't in the cache yet
(e.g. rooms/list hasn't loaded). Presentation-only fields pass through. */
export function resolveRoomConfig(
  user: RoomClimateUserConfig
): RoomClimateControlConfig | undefined {
  const key = resolveRoomKey(user);
  if (!key) return undefined;
  const room = roomMetaByKey(key);
  if (!room) return undefined;

  const e = room.entities;
  const live = (device: string): WsRoomLive => e.live[device] ?? EMPTY_LIVE;
  const cool = live("cooling");
  const heat = live("heating");
  const fanOffsets = e.fan_offsets;
  const humidity = e.humidity_control;

  return defaultConfig({
    type: "custom:room-climate-control",
    room: key,
    profile_room_key: key,
    room_name: room.label,
    temp_sensor: e.temperature ?? "",
    humidity_sensor: e.humidity ?? "",
    power_sensor: e.power ?? "",
    ac_entity: e.ac_entity ?? "",
    heater_entity: e.heater_entity ?? "",
    window_sensors: e.window_sensors ?? [],
    manual_mode: e.manual_mode ?? "",
    ac_fan_only_override: e.ac_fan_only_override ?? "",
    heater_fan_only_override: e.heater_fan_only_override ?? "",
    fans: (e.fans ?? []).map((f) => ({
      entity_id: f.entity_id,
      slug: f.slug,
      label: f.label,
      reversible: f.reversible ?? false,
      use: f.use ?? "",
      target: f.target ?? "",
      reverse: f.reverse ?? "",
    })),
    use_ac: cool.use ?? "",
    target_cooling: cool.target ?? "",
    cooling_medium_offset: cool.medium_offset ?? "",
    cooling_high_offset: cool.high_offset ?? "",
    use_heater: heat.use ?? "",
    target_heating: heat.target ?? "",
    heating_medium_offset: heat.medium_offset ?? "",
    heating_high_offset: heat.high_offset ?? "",
    fan_medium_offset: fanOffsets?.medium_offset ?? "",
    fan_high_offset: fanOffsets?.high_offset ?? "",
    humidity_target: humidity?.target ?? "",
    humidity_medium_offset: humidity?.medium_offset ?? "",
    humidity_high_offset: humidity?.high_offset ?? "",
    // Outdoor + time-range fall back to the integration's hub entities (the
    // outdoor mirror and the graph time-range select) before any hard default.
    outdoor_sensor: user.outdoor_sensor ?? e.outdoor ?? DEFAULT_OUTDOOR_SENSOR,
    time_range: user.time_range ?? e.time_range ?? DEFAULT_TIME_RANGE,
  });
}

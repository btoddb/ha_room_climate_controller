/** Typed websocket client for the room_climate integration. */
import type { HomeAssistant } from "../ha-types";

export interface WsPresetDevice {
  use: boolean;
  temp: number | null;
  use_entity: string | null;
  temp_entity: string | null;
}

/** One fan's full per-profile preset (use + temp + reverse), resolved to the
profile's Use switch, Target number, and Reverse switch entity ids. */
export interface WsFanPreset {
  slug: string;
  label: string;
  use: boolean;
  temp: number | null;
  reverse: boolean;
  reversible: boolean;
  use_entity: string | null;
  temp_entity: string | null;
  reverse_entity: string | null;
}

export interface WsProfile {
  id: string;
  name: string;
  room: string;
  icon: string;
  enabled: boolean;
  time: string | null;
  has_heating: boolean;
  has_fan: boolean;
  fan_override: boolean | null;
  entities: {
    enabled: string | null;
    time: string | null;
    fan_override: string | null;
    presets: Record<string, WsPresetDevice>;
    fan_presets: WsFanPreset[];
  };
}

export interface WsRoomLive {
  use: string | null;
  target: string | null;
  medium_offset: string | null;
  high_offset: string | null;
}

/** One fan's live room entities. Each fan owns its Use switch, Target number and
Reverse switch; `reversible` is detected per fan (CC-22). */
export interface WsFanEntity {
  entity_id: string;
  slug: string;
  label: string;
  reversible: boolean;
  use: string | null;
  target: string | null;
  reverse: string | null;
}

/** Shared fan-speed offset number entities — one pair per room, not per fan. */
export interface WsFanOffsets {
  medium_offset: string | null;
  high_offset: string | null;
}

export interface WsRoom {
  key: string;
  label: string;
  area_id: string | null;
  has_ac: boolean;
  has_heating: boolean;
  has_fan: boolean;
  combined: boolean;
  entities: {
    manual_mode: string | null;
    ac_fan_only_override: string | null;
    heater_fan_only_override: string | null;
    temperature: string | null;
    humidity: string | null;
    power: string | null;
    outdoor: string | null;
    time_range: string | null;
    ac_entity: string | null;
    heater_entity: string | null;
    /** All the room's fans; each carries its own use/target/reverse entities. */
    fans: WsFanEntity[];
    /** Shared medium/high offsets for the room's fans, or null when no fan. */
    fan_offsets: WsFanOffsets | null;
    window_sensors: string[];
    /** Only "cooling"/"heating" now — the "fan" key moved to `fans`. */
    live: Record<string, WsRoomLive>;
  };
}

export function wsListRooms(hass: HomeAssistant): Promise<{ rooms: WsRoom[] }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/rooms/list" });
}

export function wsListProfiles(hass: HomeAssistant): Promise<{ profiles: WsProfile[] }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/list" });
}

export function wsCreateProfile(
  hass: HomeAssistant,
  params: { name: string; room: string; time?: string; copy_room_settings?: boolean }
): Promise<{ profile: WsProfile }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/create", ...params });
}

export function wsDeleteProfile(
  hass: HomeAssistant,
  profile_id: string
): Promise<{ success: boolean }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/delete", profile_id });
}

export function wsRenameProfile(
  hass: HomeAssistant,
  profile_id: string,
  name: string
): Promise<{ profile: WsProfile }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/rename", profile_id, name });
}

export function wsSetRoom(
  hass: HomeAssistant,
  profile_id: string,
  room: string
): Promise<{ success: boolean }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/set_room", profile_id, room });
}

export function wsApplyProfile(
  hass: HomeAssistant,
  profile_id: string
): Promise<{ success: boolean }> {
  return hass.callWS!({ type: "btoddb_room_climate_controller/profiles/apply", profile_id });
}

/** Extract a human message from a callWS rejection ({ code, message }). */
export function wsErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) {
    const m = (err as { message?: unknown }).message;
    if (typeof m === "string") return m.trim();
  }
  return "";
}

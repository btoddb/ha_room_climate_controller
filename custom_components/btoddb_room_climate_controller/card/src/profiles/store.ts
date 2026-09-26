/** Client-side cache of rooms/profiles, fetched from the room_climate websocket API.

Replaces the old discovery that scanned hass.states for `input_*.daily_routine_*`
helpers and fetched `/local/btoddb/*.json`. Maps the websocket payload into the
existing RoutineConfig/RoomPresetConfig shapes so the rendering code is unchanged.
*/
import type { HomeAssistant } from "../ha-types";
import { wsListProfiles, wsListRooms, type WsProfile, type WsRoom } from "./api";
import type { RoomPresetConfig, RoutineConfig } from "./types";

export interface ClimateRoomMeta {
  key: string;
  label: string;
  has_heating: boolean;
  has_fan: boolean;
}

let rooms: WsRoom[] = [];
let profiles: WsProfile[] = [];
let loaded = false;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

export function subscribeProfiles(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify(): void {
  for (const fn of listeners) fn();
}

/** Fetch rooms + profiles and update the cache. Coalesces concurrent calls. */
export async function refreshProfiles(hass: HomeAssistant): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const [r, p] = await Promise.all([wsListRooms(hass), wsListProfiles(hass)]);
      rooms = r.rooms ?? [];
      profiles = p.profiles ?? [];
      loaded = true;
      notify();
    } catch {
      // Leave the last good cache in place; integration may not be set up yet.
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function profilesLoaded(): boolean {
  return loaded;
}

export function getRoomsSync(): WsRoom[] {
  return rooms;
}

export function roomMetaByKey(key: string): WsRoom | undefined {
  return rooms.find((r) => r.key === key);
}

export function climateRooms(): ClimateRoomMeta[] {
  return rooms.map((r) => ({
    key: r.key,
    label: r.label,
    has_heating: r.has_heating,
    has_fan: r.has_fan,
  }));
}

function toRoutine(p: WsProfile): RoutineConfig {
  const presets = p.entities.presets ?? {};
  const room: RoomPresetConfig = {
    name: roomMetaByKey(p.room)?.label ?? p.room,
    roomKey: p.room,
    has_heating: p.has_heating,
    has_fan: p.has_fan,
    useCooling: presets.cooling?.use_entity ?? undefined,
    useHeating: presets.heating?.use_entity ?? undefined,
    fanOverride: p.entities.fan_override ?? undefined,
    cooling: presets.cooling?.temp_entity ?? "",
    heating: presets.heating?.temp_entity ?? undefined,
    fans: (p.entities.fan_presets ?? []).map((fp) => ({
      slug: fp.slug,
      label: fp.label,
      use: fp.use,
      temp: fp.temp,
      reverse: fp.reverse,
      reversible: fp.reversible,
      useEntity: fp.use_entity ?? "",
      tempEntity: fp.temp_entity ?? "",
      reverseEntity: fp.reverse_entity ?? "",
    })),
  };
  return {
    profileId: p.id,
    name: p.name,
    enabled: p.entities.enabled ?? "",
    time: p.entities.time ?? "",
    roomKey: p.room,
    room,
  };
}

export function routinesForRoom(roomKey: string): RoutineConfig[] {
  return profiles
    .filter((p) => p.room === roomKey)
    .map(toRoutine)
    .sort((a, b) => {
      const ta = profileTimeOf(a.profileId);
      const tb = profileTimeOf(b.profileId);
      if (ta !== tb) return ta.localeCompare(tb);
      return a.profileId.localeCompare(b.profileId, undefined, { numeric: true });
    });
}

function profileTimeOf(profileId: string): string {
  return profiles.find((p) => p.id === profileId)?.time ?? "99:99";
}

/** All profiles in a room, as plain WS records (for time-conflict checks). */
export function profilesInRoom(roomKey: string): WsProfile[] {
  return profiles.filter((p) => p.room === roomKey);
}

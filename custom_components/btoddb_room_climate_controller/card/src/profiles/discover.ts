/** Profile listing helpers. Discovery now comes from the websocket store. */
import { routinesForRoom as storeRoutinesForRoom } from "./store";
import type { RoutineConfig } from "./types";

export function routinesForRoom(_hassUnused: unknown, roomKey: string): RoutineConfig[] {
  return storeRoutinesForRoom(roomKey);
}

/** Profile names are now stored verbatim (no "01 - " prefix to strip). */
export function editableProfileName(friendlyName: string, _profileId: string): string {
  return friendlyName;
}

function entityConfigured(entityId?: string): boolean {
  return Boolean(entityId && entityId.trim());
}

export function routineIsConfigured(routine: RoutineConfig): boolean {
  const r = routine.room;
  const hasFan = r.fans.some(
    (f) => entityConfigured(f.tempEntity) || entityConfigured(f.useEntity)
  );
  return (
    entityConfigured(routine.enabled) &&
    entityConfigured(routine.time) &&
    (entityConfigured(r.cooling) ||
      entityConfigured(r.useCooling) ||
      entityConfigured(r.heating) ||
      hasFan)
  );
}

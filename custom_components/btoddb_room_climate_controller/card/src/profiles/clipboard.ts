import type { HomeAssistant } from "../ha-types";
import { entityConfigured } from "../helpers";
import type { RoomPresetConfig } from "./types";

export const CLIPBOARD_TYPE = "daily-routine-climate-temps" as const;

export interface ClipboardDevicePreset {
  use?: boolean;
  temp?: number;
}

/** v3 per-fan clipboard entry, matched onto a target room's fans by `slug`. */
export interface ClipboardFanPreset {
  slug: string;
  label?: string;
  use?: boolean;
  temp?: number;
  reverse?: boolean;
}

export interface ClipboardRoomTemps {
  fanOverride?: boolean;
  /** Legacy (v1/v2) single-fan reverse; superseded by per-fan `fans[].reverse`. */
  fanReverse?: boolean;
  cooling?: ClipboardDevicePreset | number;
  heating?: ClipboardDevicePreset | number;
  /** Legacy (v1/v2) single-fan preset; superseded by `fans`. */
  fan?: ClipboardDevicePreset | number;
  /** v3 per-fan presets. */
  fans?: ClipboardFanPreset[];
}

export interface RoutineClipboardPayload {
  version: 1 | 2 | 3;
  type: typeof CLIPBOARD_TYPE;
  rooms: Record<string, ClipboardRoomTemps>;
}

let memoryClipboard: string | null = null;

function readTemp(hass: HomeAssistant, entityId?: string): number | undefined {
  if (!entityConfigured(entityId)) return undefined;
  const v = parseFloat(hass.states[entityId!]?.state ?? "");
  return Number.isNaN(v) ? undefined : Math.round(v);
}

function readUse(hass: HomeAssistant, entityId?: string): boolean | undefined {
  if (!entityConfigured(entityId)) return undefined;
  const s = hass.states[entityId!]?.state;
  if (s === "on") return true;
  if (s === "off") return false;
  return undefined;
}

function normalizeDevice(
  value: ClipboardDevicePreset | number | undefined
): ClipboardDevicePreset | undefined {
  if (value === undefined) return undefined;
  if (typeof value === "number") return { temp: value };
  return value;
}

export function buildClipboardPayload(
  hass: HomeAssistant,
  rooms: RoomPresetConfig[]
): RoutineClipboardPayload {
  const roomsMap: Record<string, ClipboardRoomTemps> = {};
  for (const room of rooms) {
    const entry: ClipboardRoomTemps = {};
    const coolingTemp = readTemp(hass, room.cooling);
    const coolingUse = readUse(hass, room.useCooling);
    if (coolingTemp !== undefined || coolingUse !== undefined) {
      entry.cooling = { temp: coolingTemp, use: coolingUse };
    }
    if (room.has_heating !== false) {
      const heatingTemp = readTemp(hass, room.heating);
      const heatingUse = readUse(hass, room.useHeating);
      if (heatingTemp !== undefined || heatingUse !== undefined) {
        entry.heating = { temp: heatingTemp, use: heatingUse };
      }
    }
    if (room.has_fan !== false && room.fans.length > 0) {
      const fans: ClipboardFanPreset[] = [];
      for (const fan of room.fans) {
        const fanTemp = readTemp(hass, fan.tempEntity);
        const fanUse = readUse(hass, fan.useEntity);
        const fanRev = fan.reversible ? readUse(hass, fan.reverseEntity) : undefined;
        if (fanTemp === undefined && fanUse === undefined && fanRev === undefined) {
          continue;
        }
        const fanEntry: ClipboardFanPreset = { slug: fan.slug, label: fan.label };
        if (fanUse !== undefined) fanEntry.use = fanUse;
        if (fanTemp !== undefined) fanEntry.temp = fanTemp;
        if (fanRev !== undefined) fanEntry.reverse = fanRev;
        fans.push(fanEntry);
      }
      if (fans.length) entry.fans = fans;
    }
    const fanOvr = readUse(hass, room.fanOverride);
    if (fanOvr !== undefined) entry.fanOverride = fanOvr;

    roomsMap[room.name] = entry;
  }
  return { version: 3, type: CLIPBOARD_TYPE, rooms: roomsMap };
}

export function parseClipboardPayload(text: string): RoutineClipboardPayload | null {
  try {
    const data = JSON.parse(text) as RoutineClipboardPayload;
    if (
      (data?.version === 1 || data?.version === 2 || data?.version === 3) &&
      data?.type === CLIPBOARD_TYPE &&
      data.rooms &&
      typeof data.rooms === "object"
    ) {
      return data;
    }
  } catch {
    /* invalid JSON */
  }
  return null;
}

export function applyClipboardPayload(
  hass: HomeAssistant,
  rooms: RoomPresetConfig[],
  payload: RoutineClipboardPayload,
  setValue: (entityId: string, value: number) => void,
  setUse: (entityId: string, on: boolean) => void
): number {
  let applied = 0;
  for (const room of rooms) {
    const src = payload.rooms[room.name];
    if (!src) continue;

    if (src.fanOverride !== undefined && entityConfigured(room.fanOverride)) {
      setUse(room.fanOverride!, src.fanOverride);
      applied++;
    }

    const devices: {
      key: keyof ClipboardRoomTemps;
      useId?: string;
      tempId?: string;
      enabled: boolean;
    }[] = [
      { key: "cooling", useId: room.useCooling, tempId: room.cooling, enabled: true },
      {
        key: "heating",
        useId: room.useHeating,
        tempId: room.heating,
        enabled: room.has_heating !== false,
      },
    ];

    for (const dev of devices) {
      if (!dev.enabled) continue;
      const raw = dev.key === "cooling" ? src.cooling : src.heating;
      const preset = normalizeDevice(raw);
      if (!preset) continue;

      if (preset.use !== undefined && entityConfigured(dev.useId)) {
        setUse(dev.useId!, preset.use);
        applied++;
      }
      if (preset.temp !== undefined && entityConfigured(dev.tempId)) {
        setValue(dev.tempId!, preset.temp);
        applied++;
      }
    }

    applied += applyFans(room, src, setValue, setUse);
  }
  return applied;
}

/** Apply the clipboard's fan presets onto a target room's fans.

v3 payloads carry a per-fan `fans[]` matched by slug: a clipboard fan for a slug
the room lacks is ignored, and a room fan with no clipboard entry keeps its
current value. Legacy v1/v2 payloads carry a single `fan`/`fanReverse` with no
slug, which is applied to every fan in the target room. */
function applyFans(
  room: RoomPresetConfig,
  src: ClipboardRoomTemps,
  setValue: (entityId: string, value: number) => void,
  setUse: (entityId: string, on: boolean) => void
): number {
  if (room.has_fan === false || room.fans.length === 0) return 0;
  let applied = 0;

  if (Array.isArray(src.fans)) {
    const bySlug = new Map(src.fans.map((f) => [f.slug, f]));
    for (const fan of room.fans) {
      const cf = bySlug.get(fan.slug);
      if (!cf) continue; // target fan with no clipboard entry keeps its value
      if (cf.use !== undefined && entityConfigured(fan.useEntity)) {
        setUse(fan.useEntity, cf.use);
        applied++;
      }
      if (cf.temp !== undefined && entityConfigured(fan.tempEntity)) {
        setValue(fan.tempEntity, cf.temp);
        applied++;
      }
      if (
        cf.reverse !== undefined &&
        fan.reversible &&
        entityConfigured(fan.reverseEntity)
      ) {
        setUse(fan.reverseEntity, cf.reverse);
        applied++;
      }
    }
    return applied;
  }

  // Legacy single-fan clipboard (v1/v2): apply to every fan in the room.
  const legacy = normalizeDevice(src.fan);
  for (const fan of room.fans) {
    if (legacy?.use !== undefined && entityConfigured(fan.useEntity)) {
      setUse(fan.useEntity, legacy.use);
      applied++;
    }
    if (legacy?.temp !== undefined && entityConfigured(fan.tempEntity)) {
      setValue(fan.tempEntity, legacy.temp);
      applied++;
    }
    if (
      src.fanReverse !== undefined &&
      fan.reversible &&
      entityConfigured(fan.reverseEntity)
    ) {
      setUse(fan.reverseEntity, src.fanReverse);
      applied++;
    }
  }
  return applied;
}

export async function writeRoutineClipboard(payload: RoutineClipboardPayload): Promise<void> {
  const text = JSON.stringify(payload);
  memoryClipboard = text;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* keep memoryClipboard */
    }
  }
}

export async function readRoutineClipboard(): Promise<string | null> {
  if (navigator.clipboard?.readText) {
    try {
      const text = await navigator.clipboard.readText();
      if (text?.trim()) {
        memoryClipboard = text;
        return text;
      }
    } catch {
      /* fall through */
    }
  }
  return memoryClipboard;
}

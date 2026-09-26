/** Shapes the profiles panel renders. Data now comes from the websocket store. */

/** One fan's preset within a profile. Each fan owns its Use/Target/Reverse
entities; `reversible` gates the Reverse toggle. The `use`/`temp`/`reverse`
scalars are the profile's stored values (used for clipboard copy). */
export interface FanPresetConfig {
  slug: string;
  label: string;
  use: boolean;
  temp: number | null;
  reverse: boolean;
  reversible: boolean;
  useEntity: string;
  tempEntity: string;
  reverseEntity: string;
}

export interface RoomPresetConfig {
  name: string;
  roomKey: string;
  has_heating?: boolean;
  has_fan?: boolean;
  useCooling?: string;
  useHeating?: string;
  fanOverride?: string;
  cooling: string;
  heating?: string;
  /** One entry per fan in the room (empty when the room has no fan). */
  fans: FanPresetConfig[];
}

export interface RoutineConfig {
  profileId: string;
  name: string;
  enabled: string;
  time: string;
  roomKey: string;
  room: RoomPresetConfig;
}

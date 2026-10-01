/** Shapes the profiles panel renders. Data now comes from the websocket store. */

/** One fan's preset within a profile. Each fan owns its Use/Target/Reverse
entities; `reversible` gates the Reverse toggle. The `use`/`temp`/`reverse`
scalars are the profile's stored values (used for clipboard copy). Each fan
also owns its own humidity target preset (CC-28 amendment; PR-14); `humidity`/
`humidityEntity` are null/"" when the room has no humidity sensor. */
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
  humidity: number | null;
  humidityEntity: string;
}

/** The room's Vent Fan preset (issue #77, PR-13): use + temp target + humidity
target. humidity/humidityEntity are null/"" without a humidity sensor. */
export interface VentPresetConfig {
  use: boolean;
  temp: number | null;
  humidity: number | null;
  useEntity: string;
  tempEntity: string;
  humidityEntity: string;
}

export interface RoomPresetConfig {
  name: string;
  roomKey: string;
  has_heating?: boolean;
  has_fan?: boolean;
  has_vent_fan?: boolean;
  useCooling?: string;
  useHeating?: string;
  fanOverride?: string;
  cooling: string;
  heating?: string;
  /** One entry per fan in the room (empty when the room has no fan). */
  fans: FanPresetConfig[];
  /** The room's vent fan preset, or undefined when the room has no vent fan. */
  vent?: VentPresetConfig;
}

export interface RoutineConfig {
  profileId: string;
  name: string;
  enabled: string;
  time: string;
  roomKey: string;
  room: RoomPresetConfig;
}

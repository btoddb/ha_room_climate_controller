import type { HomeAssistant, LovelaceCardConfig } from "./ha-types";

/** What the user actually writes in the dashboard YAML / visual editor.

The card resolves every device/helper entity from the integration's
`room_climate_controller/rooms/list` for the chosen `room`, so the only required
field is `room`. Everything else is presentation-only (not owned by the integration). */
export interface RoomClimateUserConfig extends LovelaceCardConfig {
  type: "custom:room-climate-control";
  /** Integration room key, e.g. "todd_s_bedroom". */
  room?: string;
  outdoor_sensor?: string;
  time_range?: string;
  /** Advanced/legacy: explicit room key if `room` is absent. */
  profile_room_key?: string;
}

/** Fully-resolved config the card renders from: the user's presentation fields
plus every entity discovered from the integration for the chosen room. */
/** One fan in a room. Each fan owns its Use switch, Target number and Reverse
switch; `reversible` (detected per fan, CC-22) gates its Reverse control. The
medium/high offsets are shared across the room's fans, not stored here. */
export interface FanConfig {
  entity_id: string;
  slug: string;
  label: string;
  reversible: boolean;
  use: string;
  target: string;
  reverse: string;
}

export interface RoomClimateControlConfig extends LovelaceCardConfig {
  type: "custom:room-climate-control";
  /** Integration room key (new model: the card self-discovers entities). */
  room?: string;
  room_name: string;
  temp_sensor: string;
  humidity_sensor?: string;
  power_sensor?: string;
  ac_entity?: string;
  heater_entity?: string;
  /** Optional window contacts; while any reads "on" (open) the engine suppresses
  cooling/heating and the card disables their Use toggles (UX-26). */
  window_sensors?: string[];
  use_ac: string;
  use_heater: string;
  ac_fan_only_override?: string;
  heater_fan_only_override?: string;
  /** The room's fans (each with its own use/target/reverse + reversible flag). */
  fans: FanConfig[];
  manual_mode: string;
  target_cooling: string;
  cooling_medium_offset: string;
  cooling_high_offset: string;
  target_heating: string;
  heating_medium_offset: string;
  heating_high_offset: string;
  /** Shared medium/high fan-speed offsets for all the room's fans. */
  fan_medium_offset: string;
  fan_high_offset: string;
  /** Room-level humidity target + shared offsets (CC-28); empty when the room
  has no humidity control. */
  humidity_target: string;
  humidity_medium_offset: string;
  humidity_high_offset: string;
  outdoor_sensor?: string;
  time_range?: string;
  /** Room key for climate profiles (e.g. todds_bedroom). Inferred from manual_mode if omitted. */
  profile_room_key?: string;
}

export type DialogType = "settings" | "energy" | "history" | null;

export const DEFAULT_OUTDOOR_SENSOR = "sensor.outdoor_temperature";
// The integration owns the time-range select and the card discovers its entity
// id from rooms/list, so there is no hard default to fall back to.
export const DEFAULT_TIME_RANGE = "";

export function defaultConfig(
  partial: Partial<RoomClimateControlConfig> = {}
): RoomClimateControlConfig {
  return {
    type: "custom:room-climate-control",
    room_name: "Room",
    temp_sensor: "",
    use_ac: "",
    use_heater: "",
    fans: [],
    manual_mode: "",
    target_cooling: "",
    cooling_medium_offset: "",
    cooling_high_offset: "",
    target_heating: "",
    heating_medium_offset: "",
    heating_high_offset: "",
    fan_medium_offset: "",
    fan_high_offset: "",
    humidity_target: "",
    humidity_medium_offset: "",
    humidity_high_offset: "",
    outdoor_sensor: DEFAULT_OUTDOOR_SENSOR,
    time_range: DEFAULT_TIME_RANGE,
    ...partial,
  };
}

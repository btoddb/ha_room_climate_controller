import { html, nothing, type TemplateResult } from "lit";
import type { HomeAssistant } from "./ha-types";
import {
  entityConfigured,
  formatSensorValue,
  getStateObj,
  setInputNumber,
} from "./helpers";
import type { FanConfig, RoomClimateControlConfig } from "./types";

export interface DeviceSettingsFields {
  title: string;
  target: string;
  mediumOffset: string;
  highOffset: string;
  /** When true, computed thresholds subtract offsets from target (heating). */
  subtractOffsets?: boolean;
  /** Fan reverse switch entity; set only when the fan is reversible (UX-28). */
  reverseToggle?: string;
}

/** The room's fan settings: one target (+ reverse when reversible) per fan, plus
the single medium/high offset pair shared across all the room's fans. */
export interface FanSettingsFields {
  fans: FanConfig[];
  mediumOffset: string;
  highOffset: string;
}

function parseNum(hass: HomeAssistant, entityId: string, fallback = 0): number {
  const v = parseFloat(hass.states[entityId]?.state ?? "");
  return Number.isNaN(v) ? fallback : v;
}

function computedThreshold(
  hass: HomeAssistant,
  targetEntity: string,
  offsetEntity: string,
  subtract: boolean
): number | null {
  if (!entityConfigured(targetEntity) || !entityConfigured(offsetEntity)) return null;
  const target = parseNum(hass, targetEntity);
  const offset = parseNum(hass, offsetEntity, 1);
  return subtract ? target - offset : target + offset;
}

export function buildDeviceSettingsFields(
  config: RoomClimateControlConfig
): DeviceSettingsFields[] {
  const sections: DeviceSettingsFields[] = [];

  if (entityConfigured(config.ac_entity)) {
    sections.push({
      title: "Cooling",
      target: config.target_cooling,
      mediumOffset: config.cooling_medium_offset,
      highOffset: config.cooling_high_offset,
    });
  }

  if (entityConfigured(config.heater_entity)) {
    sections.push({
      title: "Heating",
      target: config.target_heating,
      mediumOffset: config.heating_medium_offset,
      highOffset: config.heating_high_offset,
      subtractOffsets: true,
    });
  }

  return sections;
}

/** Fan settings, or null when the room has no fan. Offsets are shared by all
fans, so they are surfaced once here rather than per fan. */
export function buildFanSettingsFields(
  config: RoomClimateControlConfig
): FanSettingsFields | null {
  if (config.fans.length === 0) return null;
  return {
    fans: config.fans,
    mediumOffset: config.fan_medium_offset,
    highOffset: config.fan_high_offset,
  };
}

function renderTargetRow(
  hass: HomeAssistant,
  entityId: string,
  label: string
): TemplateResult | typeof nothing {
  const obj = getStateObj(hass, entityId);
  if (!obj) return nothing;
  const min = Number(obj.attributes.min ?? 0);
  const max = Number(obj.attributes.max ?? 100);
  const step = Number(obj.attributes.step ?? 1);
  const val = parseNum(hass, entityId);

  return html`
    <div class="settings-row">
      <span class="settings-row-label">${label}</span>
      <div class="settings-row-control settings-target-control">
        <input
          type="number"
          class="settings-target-input"
          min=${min}
          max=${max}
          step=${step}
          .value=${String(val)}
          @change=${(ev: Event) => {
            const n = parseFloat((ev.target as HTMLInputElement).value);
            if (!Number.isNaN(n)) setInputNumber(hass, entityId, n);
          }}
        />
        <span class="settings-unit">°F</span>
      </div>
    </div>
  `;
}

function renderOffsetSlider(
  hass: HomeAssistant,
  entityId: string,
  label: string,
  computed: number | null
): TemplateResult | typeof nothing {
  const obj = getStateObj(hass, entityId);
  if (!obj) return nothing;
  const min = Number(obj.attributes.min ?? 1);
  const max = Number(obj.attributes.max ?? 20);
  const step = Number(obj.attributes.step ?? 1);
  const val = parseNum(hass, entityId, min);

  return html`
    <div class="settings-row">
      <div class="settings-row-label-block">
        <span class="settings-row-label">${label}</span>
        ${computed !== null
          ? html`<span class="settings-computed">→ ${computed.toFixed(0)}°F</span>`
          : nothing}
      </div>
      <div class="settings-row-control settings-slider-control">
        <input
          type="range"
          class="settings-slider"
          min=${min}
          max=${max}
          step=${step}
          .value=${String(val)}
          @input=${(ev: Event) => {
            const n = parseFloat((ev.target as HTMLInputElement).value);
            if (!Number.isNaN(n)) setInputNumber(hass, entityId, n);
          }}
        />
        <span class="settings-offset-value">${val}°F</span>
      </div>
    </div>
  `;
}

function renderReverseRow(
  hass: HomeAssistant,
  entityId: string | undefined,
  label = "Reverse"
): TemplateResult | typeof nothing {
  if (!entityId) return nothing;
  const obj = getStateObj(hass, entityId);
  if (!obj) return nothing;

  return html`
    <div class="settings-row">
      <span class="settings-row-label">${label}</span>
      <div class="settings-row-control">
        <ha-entity-toggle .hass=${hass} .stateObj=${obj}></ha-entity-toggle>
      </div>
    </div>
  `;
}

export function renderDeviceSettingsSection(
  hass: HomeAssistant,
  fields: DeviceSettingsFields
): TemplateResult {
  const subtract = Boolean(fields.subtractOffsets);
  const medComputed = computedThreshold(
    hass,
    fields.target,
    fields.mediumOffset,
    subtract
  );
  const highComputed = computedThreshold(
    hass,
    fields.target,
    fields.highOffset,
    subtract
  );

  return html`
    <div class="settings-section">
      <div class="settings-section-title">${fields.title}</div>
      ${renderTargetRow(hass, fields.target, "Target")}
      ${renderOffsetSlider(hass, fields.mediumOffset, "Medium offset", medComputed)}
      ${renderOffsetSlider(hass, fields.highOffset, "High offset", highComputed)}
      ${renderReverseRow(hass, fields.reverseToggle)}
    </div>
  `;
}

export function renderFanSettingsSection(
  hass: HomeAssistant,
  fields: FanSettingsFields
): TemplateResult {
  // The medium/high offsets are shared, so a single "→ X°F" preview is only
  // unambiguous with exactly one fan; with several fans (different targets) we
  // drop the preview rather than pick one fan's threshold arbitrarily.
  const soleTarget = fields.fans.length === 1 ? fields.fans[0].target : undefined;
  const medComputed = soleTarget
    ? computedThreshold(hass, soleTarget, fields.mediumOffset, false)
    : null;
  const highComputed = soleTarget
    ? computedThreshold(hass, soleTarget, fields.highOffset, false)
    : null;

  const multipleFans = fields.fans.length > 1;

  return html`
    <div class="settings-section">
      <div class="settings-section-title">Fan</div>
      ${fields.fans.map(
        (fan) => html`
          ${renderTargetRow(hass, fan.target, fan.label)}
          ${fan.reversible
            ? renderReverseRow(
                hass,
                fan.reverse,
                multipleFans ? `${fan.label} reverse` : "Reverse"
              )
            : nothing}
        `
      )}
      ${renderOffsetSlider(hass, fields.mediumOffset, "Medium offset", medComputed)}
      ${renderOffsetSlider(hass, fields.highOffset, "High offset", highComputed)}
    </div>
  `;
}

export function renderRoomSettingsSection(
  hass: HomeAssistant,
  config: RoomClimateControlConfig
): TemplateResult | typeof nothing {
  const items: TemplateResult[] = [];

  if (entityConfigured(config.temp_sensor)) {
    items.push(html`
      <div class="settings-readout-row">
        <span class="settings-row-label">Temperature</span>
        <span class="settings-readout-value"
          >${formatSensorValue(hass, config.temp_sensor)}</span
        >
      </div>
    `);
  }

  if (entityConfigured(config.humidity_sensor)) {
    items.push(html`
      <div class="settings-readout-row">
        <span class="settings-row-label">Humidity</span>
        <span class="settings-readout-value"
          >${formatSensorValue(hass, config.humidity_sensor)}</span
        >
      </div>
    `);
  }

  if (items.length === 0) return nothing;

  return html`
    <div class="settings-section">
      <div class="settings-section-title">Room</div>
      ${items}
    </div>
  `;
}

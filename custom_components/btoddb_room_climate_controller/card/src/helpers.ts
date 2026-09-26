import type { EntityState, HomeAssistant, LovelaceCardConfig } from "./ha-types";

export function entityConfigured(entityId?: string): boolean {
  return Boolean(entityId && entityId.trim());
}

export function entityAvailable(hass: HomeAssistant, entityId?: string): boolean {
  if (!entityConfigured(entityId)) return false;
  const state = hass.states[entityId!];
  if (!state) return false;
  return state.state !== "unavailable" && state.state !== "unknown";
}

/** True when ANY of the room's window contacts reads "on" (open). Unconfigured,
missing, unavailable, and unknown all read as closed — matching the engine's
fail-safe (CC-21). */
export function windowOpen(hass: HomeAssistant, entityIds?: string[]): boolean {
  return (entityIds ?? []).some(
    (id) => entityConfigured(id) && hass.states[id]?.state === "on"
  );
}

export function formatSensorValue(
  hass: HomeAssistant,
  entityId?: string,
  fallback = "—"
): string {
  if (!entityConfigured(entityId) || !hass.states[entityId!]) return fallback;
  const state = hass.states[entityId!];
  if (state.state === "unavailable" || state.state === "unknown") return fallback;
  const unit = (state.attributes.unit_of_measurement as string) || "";
  const num = parseFloat(state.state);
  if (!Number.isNaN(num)) {
    return `${Number.isInteger(num) ? num : num.toFixed(1)}${unit ? ` ${unit}` : ""}`;
  }
  return hass.formatEntityState(state);
}

export function formatPowerNow(hass: HomeAssistant, entityId?: string): string {
  if (!entityConfigured(entityId) || !hass.states[entityId!]) return "—";
  const state = hass.states[entityId!].state;
  if (state === "unavailable" || state === "unknown") return "—";
  const watts = Math.round(parseFloat(state));
  return Number.isNaN(watts) ? "—" : `${watts} W now`;
}

export function getHvacMode(hass: HomeAssistant, entityId: string): string {
  const state = hass.states[entityId];
  if (!state) return "—";
  const mode = state.attributes.hvac_mode as string | undefined;
  const raw = mode || state.state;
  if (!raw) return "—";
  return raw.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function getFanMode(
  hass: HomeAssistant,
  entityId: string,
  fanReversible = false
): string {
  const state = hass.states[entityId];
  if (!state) return "—";
  if (state.state === "off") return "Off";
  // Direction suffix for reversible fans while running, e.g. "50% (Reverse)"
  // (UX-29). Native-DIRECTION fans expose a "direction" attribute; preset-mode
  // fans (e.g. Dreo) express direction via preset_mode="reverse".
  const direction = state.attributes.direction as string | undefined;
  const preset = state.attributes.preset_mode as string | undefined;
  let suffix = "";
  if (direction === "forward" || direction === "reverse") {
    suffix = direction === "reverse" ? " (Reverse)" : " (Forward)";
  } else if (fanReversible && preset != null) {
    suffix = preset === "reverse" ? " (Reverse)" : " (Forward)";
  }
  const pct = state.attributes.percentage as number | undefined;
  if (pct != null) return `${pct}%${suffix}`;
  if (!state.state) return "—";
  return state.state.replace(/\b\w/g, (c) => c.toUpperCase()) + suffix;
}

export function getTargetTemp(hass: HomeAssistant, helperId: string, fallback = 72): number {
  return getTargetTempValue(hass, helperId) ?? fallback;
}

export function getTargetTempValue(
  hass: HomeAssistant,
  helperId: string
): number | undefined {
  const state = hass.states[helperId];
  if (!state) return undefined;
  const val = parseFloat(state.state);
  return Number.isNaN(val) ? undefined : Math.round(val);
}

/** Read a number entity's min/max attributes, with unbounded fallbacks. */
export function getNumberLimits(
  hass: HomeAssistant,
  helperId: string
): { min: number; max: number } {
  const attrs = hass.states[helperId]?.attributes ?? {};
  const min = Number(attrs.min ?? attrs.native_min_value);
  const max = Number(attrs.max ?? attrs.native_max_value);
  return {
    min: Number.isFinite(min) ? min : -Infinity,
    max: Number.isFinite(max) ? max : Infinity,
  };
}

export type TargetTempDevice = "cooling" | "heating" | "fan";

/** Compute the allowed target range for a device.

A room can now have several fans, but every fan shares one high offset and the
same min/max limits, and fans never constrain each other (no sibling coupling
like cooling↔heating). So each fan resolves independently by passing
`device: "fan"` with the shared `highOffset` and no `siblingTarget` — the "fan"
branch below is per-fan-agnostic. */
export function getEffectiveTargetLimits(
  device: TargetTempDevice,
  limits: { min: number; max: number },
  siblingTarget?: number,
  highOffset?: number
): { min: number; max: number } {
  const effectiveLimits = { ...limits };

  if (highOffset !== undefined && Number.isFinite(highOffset)) {
    if (device === "cooling" || device === "fan") {
      effectiveLimits.max = Math.min(effectiveLimits.max, limits.max - highOffset);
    } else if (device === "heating") {
      effectiveLimits.min = Math.max(effectiveLimits.min, limits.min + highOffset);
    }
  }

  if (siblingTarget === undefined || !Number.isFinite(siblingTarget)) {
    return effectiveLimits;
  }
  if (device === "cooling") {
    return {
      ...effectiveLimits,
      min: Math.max(effectiveLimits.min, siblingTarget + 1),
    };
  }
  if (device === "heating") {
    return {
      ...effectiveLimits,
      max: Math.min(effectiveLimits.max, siblingTarget - 1),
    };
  }
  return effectiveLimits;
}

/** Set a number value on `number.*` or legacy `input_number.*` (domain-aware). */
export function setInputNumber(
  hass: HomeAssistant,
  entityId: string,
  value: number
): void {
  const domain = entityId.split(".")[0];
  void hass.callService(domain, "set_value", {
    entity_id: entityId,
    value,
  });
}

/** Set a time on `time.*` or legacy `input_datetime.*` (domain-aware). */
export function setInputDateTimeTime(
  hass: HomeAssistant,
  entityId: string,
  time: string
): Promise<void> {
  const domain = entityId.split(".")[0];
  if (domain === "input_datetime") {
    return hass.callService("input_datetime", "set_datetime", {
      entity_id: entityId,
      time,
    });
  }
  return hass.callService("time", "set_value", {
    entity_id: entityId,
    time,
  });
}

/** Turn a toggle on/off on `switch.*` or legacy `input_boolean.*` (domain-aware). */
export function setToggle(
  hass: HomeAssistant,
  entityId: string,
  on: boolean
): void {
  const domain = entityId.split(".")[0];
  void hass.callService(domain, on ? "turn_on" : "turn_off", {
    entity_id: entityId,
  });
}

export function formatTimeState(state: string | undefined): string {
  if (!state || state === "unknown" || state === "unavailable") return "";
  const parts = state.split(":");
  if (parts.length >= 2) {
    return `${parts[0].padStart(2, "0")}:${parts[1].padStart(2, "0")}`;
  }
  return state;
}

/** 24h HH:MM → 12-hour display for profile list summaries (e.g. 6:30 AM). */
export function formatTime12h(hhmm: string): string {
  if (!hhmm) return "";
  const parts = hhmm.split(":");
  if (parts.length < 2) return hhmm;
  const hour = parseInt(parts[0], 10);
  const minute = parts[1].padStart(2, "0").slice(0, 2);
  if (Number.isNaN(hour)) return hhmm;
  const period = hour >= 12 ? "PM" : "AM";
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${h12}:${minute} ${period}`;
}

export function fireMoreInfo(element: HTMLElement, entityId: string): void {
  element.dispatchEvent(
    new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    })
  );
}

export function fireConfigChanged(
  element: HTMLElement,
  config: Record<string, unknown>
): void {
  element.dispatchEvent(
    new CustomEvent("config-changed", {
      bubbles: true,
      composed: true,
      detail: { config },
    })
  );
}

export async function createLovelaceCard(
  hass: HomeAssistant,
  config: LovelaceCardConfig
): Promise<HTMLElement | null> {
  if (!window.loadCardHelpers) return null;
  const helpers = await window.loadCardHelpers();
  const element = await helpers.createCardElement(config);
  if (element) element.hass = hass;
  return element;
}

type PlotlyGraphElement = HTMLElement & {
  setConfig: (config: Record<string, unknown>) => void;
  hass: HomeAssistant;
};

async function waitForCustomElement(tag: string, timeoutMs = 15000): Promise<boolean> {
  if (customElements.get(tag)) return true;
  try {
    await Promise.race([
      customElements.whenDefined(tag),
      new Promise<never>((_, reject) => {
        setTimeout(() => reject(new Error(`Timed out waiting for ${tag}`)), timeoutMs);
      }),
    ]);
    return true;
  } catch {
    return false;
  }
}

/** plotly-graph must be mounted directly; createCardElement returns a config error card. */
export async function createPlotlyGraphCard(
  hass: HomeAssistant,
  config: Record<string, unknown>
): Promise<PlotlyGraphElement | null> {
  if (!(await waitForCustomElement("plotly-graph"))) return null;

  const { type: _type, ...plotlyConfig } = config;
  const element = document.createElement("plotly-graph") as PlotlyGraphElement;
  element.setConfig(plotlyConfig);
  element.hass = hass;
  return element;
}

export function getStateObj(
  hass: HomeAssistant,
  entityId: string
): EntityState | undefined {
  return hass.states[entityId];
}

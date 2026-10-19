import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  formatUntilTime,
  getClimateHoldMode,
  getEffectiveTargetLimits,
  getHumidityTargetLimits,
  getNumberLimits,
  getStandaloneFanMode,
  getTargetTempValue,
  getVentFanMode,
  isHoldActive,
  pressButton,
  type TargetTempDevice,
  withOverrideSuffix,
} from "../src/helpers.ts";
import type { HomeAssistant } from "../src/ha-types.ts";

// Note: `profiles/clipboard.ts` is NOT exercised here. It has a runtime
// (non-type-only) import of `../helpers` with no extension — fine for the
// rollup/tsc "bundler" module resolution the build uses, but `node --test`
// resolves relative ESM specifiers with Node's own loader, which requires an
// explicit extension and throws ERR_MODULE_NOT_FOUND on that import. Adding
// the extension (`../helpers.ts`) fixes the test run but makes `npm run
// build` emit a TS5097 warning ("an import path can only end with a '.ts'
// extension when 'allowImportingTsExtensions' is enabled"), which requires a
// tsconfig.json change outside this brief's CARD/src + CARD/tests scope.
// Clipboard parsing/apply logic is therefore not testable in the existing
// harness without a build-system change; see the task report for the
// pre-humidity-payload behavior this would have covered.

function limitsFor(
  device: TargetTempDevice,
  siblingTarget: number | undefined,
  min = 60,
  max = 90,
  highOffset?: number
) {
  return getEffectiveTargetLimits(device, { min, max }, siblingTarget, highOffset);
}

describe("getEffectiveTargetLimits", () => {
  it("keeps cooling above the heating target", () => {
    assert.deepEqual(limitsFor("cooling", 68), { min: 69, max: 90 });
  });

  it("keeps heating below the cooling target", () => {
    assert.deepEqual(limitsFor("heating", 76), { min: 60, max: 75 });
  });

  it("preserves stricter static number-entity limits", () => {
    assert.deepEqual(limitsFor("cooling", 62, 66, 90), { min: 66, max: 90 });
    assert.deepEqual(limitsFor("heating", 82, 60, 78), { min: 60, max: 78 });
  });

  it("does not add cross-device limits for fan or missing sibling targets", () => {
    assert.deepEqual(limitsFor("fan", 72), { min: 60, max: 90 });
    assert.deepEqual(limitsFor("cooling", undefined), { min: 60, max: 90 });
  });

  it("keeps cooling and fan targets far enough below max for high offset", () => {
    assert.deepEqual(limitsFor("cooling", undefined, 60, 90, 6), {
      min: 60,
      max: 84,
    });
    assert.deepEqual(limitsFor("fan", undefined, 60, 90, 4), {
      min: 60,
      max: 86,
    });
  });

  it("keeps heating targets far enough above min for high offset", () => {
    assert.deepEqual(limitsFor("heating", undefined, 50, 80, 6), {
      min: 56,
      max: 80,
    });
  });

  it("combines static, cross-device, and offset limits", () => {
    assert.deepEqual(limitsFor("cooling", 68, 60, 90, 6), {
      min: 69,
      max: 84,
    });
    assert.deepEqual(limitsFor("heating", 76, 50, 80, 6), {
      min: 56,
      max: 75,
    });
  });

  it("resolves each fan independently against the shared high offset", () => {
    // A room can have several fans; they share one high offset and the same
    // min/max, and never constrain each other (no sibling coupling). So every
    // fan resolves the same effective range regardless of the others' targets.
    const shared = { min: 60, max: 90 };
    const highOffset = 5;
    const fanA = getEffectiveTargetLimits("fan", shared, undefined, highOffset);
    const fanB = getEffectiveTargetLimits("fan", shared, undefined, highOffset);
    assert.deepEqual(fanA, { min: 60, max: 85 });
    assert.deepEqual(fanB, { min: 60, max: 85 });
    // A sibling target is ignored for fans even when supplied.
    assert.deepEqual(limitsFor("fan", 70, 60, 90, 5), { min: 60, max: 85 });
  });

  it("passes vent limits through unchanged (no offsets, no sibling coupling)", () => {
    assert.deepEqual(limitsFor("vent", undefined, 60, 86), { min: 60, max: 86 });
    // A high offset or sibling target would be meaningless for the vent fan
    // (CC-37: no offsets exist), so both are ignored even if supplied.
    assert.deepEqual(limitsFor("vent", 70, 60, 86, 10), { min: 60, max: 86 });
  });
});

describe("getHumidityTargetLimits", () => {
  it("passes plain limits through with no offset", () => {
    assert.deepEqual(getHumidityTargetLimits({ min: 30, max: 90 }), {
      min: 30,
      max: 90,
    });
  });

  it("clamps max so target + high offset can't exceed 100% (CC-18 mirror)", () => {
    assert.deepEqual(getHumidityTargetLimits({ min: 30, max: 90 }, 15), {
      min: 30,
      max: 85,
    });
  });

  it("leaves max untouched when the high offset is missing", () => {
    assert.deepEqual(getHumidityTargetLimits({ min: 30, max: 90 }, undefined), {
      min: 30,
      max: 90,
    });
  });
});

describe("getNumberLimits", () => {
  it("reads Home Assistant number min and max attributes", () => {
    const hass = {
      states: {
        "number.cooling_target": {
          entity_id: "number.cooling_target",
          state: "72",
          attributes: { min: 60, max: 90 },
        },
      },
    } as HomeAssistant;

    assert.deepEqual(getNumberLimits(hass, "number.cooling_target"), {
      min: 60,
      max: 90,
    });
  });

  it("also accepts native min and max attribute names", () => {
    const hass = {
      states: {
        "number.cooling_target": {
          entity_id: "number.cooling_target",
          state: "72",
          attributes: { native_min_value: 60, native_max_value: 90 },
        },
      },
    } as HomeAssistant;

    assert.deepEqual(getNumberLimits(hass, "number.cooling_target"), {
      min: 60,
      max: 90,
    });
  });
});

describe("getTargetTempValue", () => {
  it("returns rounded live target state when present", () => {
    const hass = {
      states: {
        "number.cooling_target": {
          entity_id: "number.cooling_target",
          state: "72.4",
          attributes: {},
        },
      },
    } as HomeAssistant;

    assert.equal(getTargetTempValue(hass, "number.cooling_target"), 72);
  });

  it("returns undefined instead of a fallback for missing sibling targets", () => {
    const hass = { states: {} } as HomeAssistant;

    assert.equal(getTargetTempValue(hass, "number.heating_target"), undefined);
  });
});

describe("getVentFanMode", () => {
  const VENT = "switch.vent_fan";
  const OVERRIDE = "sensor.vent_fan_override";
  // 3:42 PM browser-local, serialized to a UTC ISO string like the backend's
  // `until` attribute, so assertions hold in any test-machine timezone.
  const UNTIL = new Date(2026, 9, 3, 15, 42).toISOString();

  function ventHass(
    ventState: string | undefined,
    override?: { state: string; until?: string | null }
  ): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (ventState !== undefined) {
      states[VENT] = { entity_id: VENT, state: ventState, attributes: {} };
    }
    if (override) {
      states[OVERRIDE] = {
        entity_id: OVERRIDE,
        state: override.state,
        attributes: { until: override.until ?? null },
      };
    }
    return { states } as HomeAssistant;
  }

  it("shows plain On/Off when there is no override entity id", () => {
    const hass = ventHass("on", { state: "on", until: UNTIL });
    assert.equal(getVentFanMode(hass, VENT), "On");
    assert.equal(getVentFanMode(hass, VENT, ""), "On");
  });

  it("shows plain On/Off when the override sensor reads none", () => {
    assert.equal(getVentFanMode(ventHass("off", { state: "none" }), VENT, OVERRIDE), "Off");
  });

  it("appends the override end time while an on override is active", () => {
    const hass = ventHass("on", { state: "on", until: UNTIL });
    assert.equal(getVentFanMode(hass, VENT, OVERRIDE), "On · override until 3:42 PM");
  });

  it("appends the override end time while an off override is active", () => {
    const hass = ventHass("off", { state: "off", until: UNTIL });
    assert.equal(getVentFanMode(hass, VENT, OVERRIDE), "Off · override until 3:42 PM");
  });

  it("pads minutes and handles midnight in the override end time", () => {
    const until = new Date(2026, 9, 4, 0, 5).toISOString();
    const hass = ventHass("on", { state: "on", until });
    assert.equal(getVentFanMode(hass, VENT, OVERRIDE), "On · override until 12:05 AM");
  });

  it("ignores an override with a null or unparseable until", () => {
    assert.equal(
      getVentFanMode(ventHass("on", { state: "on", until: null }), VENT, OVERRIDE),
      "On"
    );
    assert.equal(
      getVentFanMode(ventHass("on", { state: "on", until: "not-a-date" }), VENT, OVERRIDE),
      "On"
    );
  });

  it("treats an unavailable, unknown, or missing override sensor as none", () => {
    for (const state of ["unavailable", "unknown"]) {
      const hass = ventHass("on", { state, until: UNTIL });
      assert.equal(getVentFanMode(hass, VENT, OVERRIDE), "On");
    }
    assert.equal(getVentFanMode(ventHass("on"), VENT, OVERRIDE), "On");
  });

  it("stays Unavailable when the vent fan entity is unavailable or missing", () => {
    const override = { state: "on", until: UNTIL };
    assert.equal(
      getVentFanMode(ventHass("unavailable", override), VENT, OVERRIDE),
      "Unavailable"
    );
    assert.equal(getVentFanMode(ventHass(undefined, override), VENT, OVERRIDE), "Unavailable");
  });
});

describe("withOverrideSuffix", () => {
  const OVERRIDE = "sensor.fan_override";
  const UNTIL = new Date(2026, 9, 3, 15, 42).toISOString();

  function sensorHass(override?: { state: string; until?: string | null }): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (override) {
      states[OVERRIDE] = {
        entity_id: OVERRIDE,
        state: override.state,
        attributes: { until: override.until ?? null },
      };
    }
    return { states } as HomeAssistant;
  }

  it("appends the end time while the sensor reads active, on, or off", () => {
    for (const state of ["active", "on", "off"]) {
      const hass = sensorHass({ state, until: UNTIL });
      assert.equal(withOverrideSuffix(hass, "50%", OVERRIDE), "50% · override until 3:42 PM");
    }
  });

  it("returns the base for a none, unavailable, unknown, or missing sensor", () => {
    for (const state of ["none", "unavailable", "unknown"]) {
      const hass = sensorHass({ state, until: UNTIL });
      assert.equal(withOverrideSuffix(hass, "50%", OVERRIDE), "50%");
    }
    assert.equal(withOverrideSuffix(sensorHass(), "50%", OVERRIDE), "50%");
  });

  it("returns the base when there is no override entity id", () => {
    const hass = sensorHass({ state: "active", until: UNTIL });
    assert.equal(withOverrideSuffix(hass, "50%"), "50%");
    assert.equal(withOverrideSuffix(hass, "50%", ""), "50%");
  });

  it("returns the base for a null or unparseable until", () => {
    assert.equal(
      withOverrideSuffix(sensorHass({ state: "active", until: null }), "50%", OVERRIDE),
      "50%"
    );
    assert.equal(
      withOverrideSuffix(sensorHass({ state: "active", until: "not-a-date" }), "50%", OVERRIDE),
      "50%"
    );
  });

  it("never suffixes a status with no live device value", () => {
    const hass = sensorHass({ state: "active", until: UNTIL });
    for (const base of ["Unavailable", "Unknown", "—"]) {
      assert.equal(withOverrideSuffix(hass, base, OVERRIDE), base);
    }
  });
});

describe("getStandaloneFanMode", () => {
  const FAN = "fan.ceiling_fan";
  const OVERRIDE = "sensor.ceiling_fan_override";
  // 3:42 PM browser-local, serialized to a UTC ISO string like the backend's
  // `until` attribute, so assertions hold in any test-machine timezone.
  const UNTIL = new Date(2026, 9, 3, 15, 42).toISOString();

  function fanHass(
    fan: { state: string; attributes?: Record<string, unknown> } | undefined,
    override?: { state: string; until?: string | null }
  ): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (fan) {
      states[FAN] = { entity_id: FAN, state: fan.state, attributes: fan.attributes ?? {} };
    }
    if (override) {
      states[OVERRIDE] = {
        entity_id: OVERRIDE,
        state: override.state,
        attributes: { until: override.until ?? null },
      };
    }
    return { states } as HomeAssistant;
  }

  const running = { state: "on", attributes: { percentage: 50 } };

  it("shows the plain fan status when there is no override entity id", () => {
    const hass = fanHass(running, { state: "active", until: UNTIL });
    assert.equal(getStandaloneFanMode(hass, FAN), "50%");
    assert.equal(getStandaloneFanMode(hass, FAN, false, ""), "50%");
  });

  it("appends the override end time to a running fan's speed", () => {
    const hass = fanHass(running, { state: "active", until: UNTIL });
    assert.equal(getStandaloneFanMode(hass, FAN, false, OVERRIDE), "50% · override until 3:42 PM");
  });

  it("appends the override end time to an off fan", () => {
    const hass = fanHass({ state: "off" }, { state: "active", until: UNTIL });
    assert.equal(getStandaloneFanMode(hass, FAN, false, OVERRIDE), "Off · override until 3:42 PM");
  });

  it("keeps the reversible direction suffix before the override suffix", () => {
    const fan = { state: "on", attributes: { percentage: 50, preset_mode: "reverse" } };
    const hass = fanHass(fan, { state: "active", until: UNTIL });
    assert.equal(
      getStandaloneFanMode(hass, FAN, true, OVERRIDE),
      "50% (Reverse) · override until 3:42 PM"
    );
  });

  it("shows the plain status for a none, unavailable, unknown, or missing sensor", () => {
    for (const state of ["none", "unavailable", "unknown"]) {
      const hass = fanHass(running, { state, until: UNTIL });
      assert.equal(getStandaloneFanMode(hass, FAN, false, OVERRIDE), "50%");
    }
    assert.equal(getStandaloneFanMode(fanHass(running), FAN, false, OVERRIDE), "50%");
  });

  it("ignores an override with a null until", () => {
    const hass = fanHass(running, { state: "active", until: null });
    assert.equal(getStandaloneFanMode(hass, FAN, false, OVERRIDE), "50%");
  });

  it("stays Unavailable with no suffix when the fan is unavailable", () => {
    const hass = fanHass({ state: "unavailable" }, { state: "active", until: UNTIL });
    assert.equal(getStandaloneFanMode(hass, FAN, false, OVERRIDE), "Unavailable");
  });
});

describe("formatUntilTime", () => {
  it("formats an ISO timestamp as browser-local h:mm AM/PM", () => {
    assert.equal(formatUntilTime(new Date(2026, 9, 3, 15, 42).toISOString()), "3:42 PM");
    assert.equal(formatUntilTime(new Date(2026, 9, 4, 0, 5).toISOString()), "12:05 AM");
  });

  it("returns undefined for missing, empty, non-string, or unparseable values", () => {
    for (const until of [undefined, null, "", 42, "not-a-date"]) {
      assert.equal(formatUntilTime(until), undefined);
    }
  });
});

describe("getClimateHoldMode", () => {
  const AC = "climate.ac";
  const HOLD = "sensor.ac_setpoint_hold";
  // 3:42 PM browser-local, serialized to a UTC ISO string like the backend's
  // `until` attribute, so assertions hold in any test-machine timezone.
  const UNTIL = new Date(2026, 9, 3, 15, 42).toISOString();

  function climateHass(
    hvacState: string | undefined,
    hold?: { state: string; until?: string | null }
  ): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (hvacState !== undefined) {
      states[AC] = { entity_id: AC, state: hvacState, attributes: {} };
    }
    if (hold) {
      states[HOLD] = {
        entity_id: HOLD,
        state: hold.state,
        attributes: { until: hold.until ?? null },
      };
    }
    return { states } as HomeAssistant;
  }

  it("shows the plain mode when there is no hold entity id", () => {
    const hass = climateHass("cool", { state: "active", until: UNTIL });
    assert.equal(getClimateHoldMode(hass, AC), "Cool");
    assert.equal(getClimateHoldMode(hass, AC, ""), "Cool");
  });

  it("shows the plain mode when the hold sensor reads none", () => {
    assert.equal(getClimateHoldMode(climateHass("cool", { state: "none" }), AC, HOLD), "Cool");
  });

  it("appends the hold end time while a hold is active", () => {
    const hass = climateHass("cool", { state: "active", until: UNTIL });
    assert.equal(getClimateHoldMode(hass, AC, HOLD), "Cool · hold until 3:42 PM");
  });

  it("ignores an active hold with a null or unparseable until", () => {
    assert.equal(
      getClimateHoldMode(climateHass("heat", { state: "active", until: null }), AC, HOLD),
      "Heat"
    );
    assert.equal(
      getClimateHoldMode(climateHass("heat", { state: "active", until: "not-a-date" }), AC, HOLD),
      "Heat"
    );
  });

  it("treats an unavailable, unknown, or missing hold sensor as no hold", () => {
    for (const state of ["unavailable", "unknown"]) {
      const hass = climateHass("cool", { state, until: UNTIL });
      assert.equal(getClimateHoldMode(hass, AC, HOLD), "Cool");
    }
    assert.equal(getClimateHoldMode(climateHass("cool"), AC, HOLD), "Cool");
  });

  it("stays — when the climate entity is missing", () => {
    const hold = { state: "active", until: UNTIL };
    assert.equal(getClimateHoldMode(climateHass(undefined, hold), AC, HOLD), "—");
  });
});

describe("isHoldActive", () => {
  const SENSOR = "sensor.hold";

  function holdHass(state?: string): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (state !== undefined) {
      states[SENSOR] = { entity_id: SENSOR, state, attributes: {} };
    }
    return { states } as HomeAssistant;
  }

  it("is false for an empty or undefined sensor id", () => {
    const hass = holdHass("active");
    assert.equal(isHoldActive(hass, ""), false);
    assert.equal(isHoldActive(hass, undefined), false);
    assert.equal(isHoldActive(hass), false);
  });

  it("is false when the sensor entity is missing", () => {
    assert.equal(isHoldActive(holdHass(), SENSOR), false);
  });

  it("is false when the sensor reads none", () => {
    assert.equal(isHoldActive(holdHass("none"), SENSOR), false);
  });

  it("is true for an active setpoint hold", () => {
    assert.equal(isHoldActive(holdHass("active"), SENSOR), true);
  });

  it("is true for a vent override reading on or off", () => {
    assert.equal(isHoldActive(holdHass("on"), SENSOR), true);
    assert.equal(isHoldActive(holdHass("off"), SENSOR), true);
  });

  it("is false when the sensor is unavailable", () => {
    assert.equal(isHoldActive(holdHass("unavailable"), SENSOR), false);
  });
});

describe("pressButton", () => {
  it("calls button.press on the given entity", () => {
    const calls: unknown[][] = [];
    const hass = {
      states: {},
      callService: (...args: unknown[]) => {
        calls.push(args);
        return Promise.resolve();
      },
    } as unknown as HomeAssistant;
    pressButton(hass, "button.living_room_resume_control");
    assert.deepEqual(calls, [
      ["button", "press", { entity_id: "button.living_room_resume_control" }],
    ]);
  });
});

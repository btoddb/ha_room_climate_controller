import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  deviceRowVisible,
  getDeviceStatus,
  getEffectiveTargetLimits,
  getFanMode,
  getHumidityTargetLimits,
  getNumberLimits,
  getHvacMode,
  getTargetTempValue,
  getVentFanMode,
  type TargetTempDevice,
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

describe("getDeviceStatus (UX-37)", () => {
  const FAN = "fan.pedestal";
  const AC = "climate.ac";

  function deviceHass(
    entityId: string,
    state: string,
    attributes: Record<string, unknown> = {}
  ): HomeAssistant {
    return {
      states: { [entityId]: { entity_id: entityId, state, attributes } },
    } as unknown as HomeAssistant;
  }

  for (const state of ["unavailable", "unknown"]) {
    it(`reads "Unavailable" for a fan in state ${state}, never a stale speed`, () => {
      const hass = deviceHass(FAN, state, { percentage: 50 });
      assert.equal(getDeviceStatus(hass, FAN, getFanMode), "Unavailable");
    });

    it(`reads "Unavailable" for a climate device in state ${state}`, () => {
      const hass = deviceHass(AC, state);
      assert.equal(getDeviceStatus(hass, AC, getHvacMode), "Unavailable");
    });

    it(`reads "Unavailable" for a vent fan in state ${state}`, () => {
      const hass = deviceHass("switch.vent", state);
      assert.equal(
        getDeviceStatus(hass, "switch.vent", (h, id) => getVentFanMode(h, id)),
        "Unavailable"
      );
    });
  }

  it("passes normal states through to the device's own status", () => {
    assert.equal(getDeviceStatus(deviceHass(FAN, "off"), FAN, getFanMode), "Off");
    assert.equal(
      getDeviceStatus(deviceHass(FAN, "on", { percentage: 50 }), FAN, getFanMode),
      "50%"
    );
    assert.equal(getDeviceStatus(deviceHass(AC, "cool"), AC, getHvacMode), "Cool");
    assert.equal(
      getDeviceStatus(deviceHass("switch.vent", "on"), "switch.vent", (h, id) =>
        getVentFanMode(h, id)
      ),
      "On"
    );
  });
});

describe("deviceRowVisible (UX-37)", () => {
  const FAN = "fan.pedestal";
  const USE = "switch.use_fan";

  function rowHass(fanState?: string, useExists = true): HomeAssistant {
    const states: Record<string, unknown> = {};
    if (fanState !== undefined) {
      states[FAN] = { entity_id: FAN, state: fanState, attributes: {} };
    }
    if (useExists) states[USE] = { entity_id: USE, state: "on", attributes: {} };
    return { states } as unknown as HomeAssistant;
  }

  for (const state of ["on", "unavailable", "unknown"]) {
    it(`shows the row for a device in state ${state}`, () => {
      assert.equal(deviceRowVisible(rowHass(state), FAN, USE), true);
    });
  }

  it("hides the row for an unconfigured device entity", () => {
    assert.equal(deviceRowVisible(rowHass("on"), undefined, USE), false);
    assert.equal(deviceRowVisible(rowHass("on"), "  ", USE), false);
  });

  it("hides the row for a device entity with no state object", () => {
    assert.equal(deviceRowVisible(rowHass(undefined), FAN, USE), false);
  });

  it("hides the row when the Use switch has no state object", () => {
    assert.equal(deviceRowVisible(rowHass("on", false), FAN, USE), false);
    assert.equal(deviceRowVisible(rowHass("on"), FAN, undefined), false);
  });
});

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  getEffectiveTargetLimits,
  getNumberLimits,
  getTargetTempValue,
  type TargetTempDevice,
} from "../src/helpers.ts";
import type { HomeAssistant } from "../src/ha-types.ts";

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

import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { describe, it } from "node:test";

import type { RoomClimateControlConfig } from "../src/types.ts";

// `graph-configs.ts` has a runtime import of `./types` with no extension (fine
// for the rollup/tsc "bundler" resolution, but Node's own ESM loader requires
// one). Rather than change the source import (which would trigger a TS5097
// build warning), retry extensionless relative specifiers with `.ts` here.
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (err) {
      if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier)) {
        return nextResolve(`${specifier}.ts`, context);
      }
      throw err;
    }
  },
});

const { buildHistoryGraphConfig } = await import("../src/graph-configs.ts");

function makeConfig(overrides: Partial<RoomClimateControlConfig> = {}) {
  return {
    ac_entity: "climate.ac",
    heater_entity: "",
    fans: [
      { entity_id: "fan.one", label: "Fan One" },
      { entity_id: "fan.two", label: "Fan Two" },
    ],
    vent_fan_entity: "",
    ...overrides,
  } as unknown as RoomClimateControlConfig;
}

function traces(config: RoomClimateControlConfig, label?: string) {
  const graph = buildHistoryGraphConfig(config, 24, label);
  return graph.entities as Array<Record<string, unknown>>;
}

describe("buildHistoryGraphConfig vent fan trace (UX-35)", () => {
  it("adds no vent fan trace when none is configured", () => {
    const base = traces(makeConfig());
    assert.equal(base.filter((t) => t.entity === "switch.vent").length, 0);
    assert.equal(traces(makeConfig({ vent_fan_entity: "  " })).length, base.length);
  });

  it("adds exactly one On/Off trace after the fan traces, labeled 'Vent Fan' by default", () => {
    const base = traces(makeConfig());
    const withVent = traces(makeConfig({ vent_fan_entity: "switch.vent" }));
    assert.equal(withVent.length, base.length + 1);
    const vent = withVent.at(-1)!;
    assert.equal(vent.entity, "switch.vent");
    assert.equal(vent.yaxis, "y2");
    assert.match(String(vent.name), /"Vent Fan: "/);
    assert.equal(withVent.filter((t) => t.entity === "switch.vent").length, 1);
    // Fan traces precede it.
    assert.equal(withVent.at(-2)!.entity, "fan.two");
  });

  it("continues the fan color cycle (index = number of fans)", () => {
    const vent = traces(makeConfig({ vent_fan_entity: "switch.vent" })).at(-1)!;
    // FAN_TRACE_COLORS[2] with 2 fans configured.
    assert.equal((vent.line as { color: string }).color, "rgb(160,100,220)");
  });

  it("uses the supplied vent fan label", () => {
    const vent = traces(makeConfig({ vent_fan_entity: "light.bath" }), "Bath Vent").at(-1)!;
    assert.equal(vent.entity, "light.bath");
    assert.match(String(vent.name), /"Bath Vent: "/);
  });

  it("escapes the label so apostrophes still yield a valid name expression", () => {
    const vent = traces(makeConfig({ vent_fan_entity: "switch.vent" }), "Kid's Vent").at(-1)!;
    const expr = String(vent.name).replace(/^\$ex /, "");
    const evaluate = new Function("ys", `return ${expr}`) as (ys: unknown[]) => string;
    assert.equal(evaluate([1]), "Kid's Vent: On");
    assert.equal(evaluate([0]), "Kid's Vent: Off");
    assert.equal(evaluate(["unavailable"]), "Kid's Vent: —");
  });
});

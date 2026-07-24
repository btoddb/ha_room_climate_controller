import type { RoomClimateControlConfig } from "./types";
import { DEFAULT_OUTDOOR_SENSOR } from "./types";

export function buildEnergyGraphConfig(
  config: RoomClimateControlConfig,
  hours: number
): Record<string, unknown> {
  return {
    type: "custom:plotly-graph",
    hours_to_show: hours,
    refresh_interval: 60,
    config: {
      displayModeBar: false,
      scrollZoom: false,
    },
    entities: [
      {
        entity: config.power_sensor,
        filters: ["force_numeric"],
        name: "$ex 'Power: ' + (ys.at(-1) != null ? Math.round(ys.at(-1)) + ' W' : '—')",
        hovertemplate: "%{x|%H:%M}: %{y:.0f} W<extra></extra>",
        line: { color: "rgb(255,165,0)", width: 2 },
      },
    ],
    layout: {
      dragmode: false,
      height: 400,
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "center",
        x: 0.5,
      },
      margin: { t: 40, r: 20 },
      yaxis: {
        title: "Watts",
        showgrid: false,
        zeroline: false,
        rangemode: "tozero",
        fixedrange: true,
      },
      xaxis: { showgrid: false, fixedrange: true },
    },
  };
}

type DeviceTraceKind = "cooling" | "heating" | "fan";

/** Distinct hues cycled across a room's fan traces (starts at the old single-fan
teal, then greens/purples that read apart from cooling/heating). */
const FAN_TRACE_COLORS = [
  "rgb(0,180,160)",
  "rgb(120,200,60)",
  "rgb(160,100,220)",
  "rgb(230,160,40)",
];

/** plotly-graph: y = state.state for climate; use map_y with state + y fallback. */
function climateMapY(kind: "cooling" | "heating"): string {
  const activeMode = kind === "cooling" ? "cool" : "heat";
  return `(
    (state && state.attributes && state.attributes.hvac_mode) ||
    (state && state.state) ||
    y
  ) === "${activeMode}" ? 1 : 0`;
}

function fanMapY(): string {
  return `(
    (state && state.state) ||
    y
  ) === "off" ? 0 : 1`;
}

function deviceTrace(
  entity: string | undefined,
  label: string,
  color: string,
  kind: DeviceTraceKind
): Record<string, unknown> | null {
  if (!entity) return null;
  const mapY = kind === "fan" ? fanMapY() : climateMapY(kind);
  return {
    entity,
    filters: [{ map_y: mapY }],
    name: `$ex '${label}: ' + (Number(ys.at(-1)) === 1 ? 'On' : Number(ys.at(-1)) === 0 ? 'Off' : '—')`,
    hovertemplate: "%{x|%H:%M}: %{y}<extra></extra>",
    yaxis: "y2",
    visible: `$ex hass.states['${entity}'] !== undefined`,
    line: { shape: "hv", color, width: 1.5 },
  };
}

export function buildHistoryGraphConfig(
  config: RoomClimateControlConfig,
  hours: number
): Record<string, unknown> {
  const outdoor = config.outdoor_sensor || DEFAULT_OUTDOOR_SENSOR;
  const entities: Record<string, unknown>[] = [
    {
      entity: config.temp_sensor,
      filters: [
        "force_numeric",
        {
          fn: `({ xs, ys, vars }) => {
            vars._roomTempVals = ys.map(Number).filter((n) => !isNaN(n));
            return { xs, ys };
          }`,
        },
      ],
      name: "$ex 'Room: ' + (ys.at(-1) != null ? ys.at(-1).toFixed(1) + ' °F' : '—')",
      hovertemplate: "%{x|%H:%M}: %{y:.1f} °F<extra></extra>",
      yaxis: "y",
      line: { color: "rgb(255,165,0)", width: 2 },
    },
    {
      entity: outdoor,
      filters: [
        "force_numeric",
        {
          fn: `({ xs, ys, vars }) => {
            const outdoor = ys.map(Number).filter((n) => !isNaN(n));
            const all = [...(vars._roomTempVals || []), ...outdoor];
            if (all.length) {
              const dmin = Math.min(...all);
              const dmax = Math.max(...all);
              vars.tempYRange = [Math.min(20, dmin) - 1, Math.max(100, dmax) + 1];
            } else {
              vars.tempYRange = [20, 100];
            }
            return { xs, ys };
          }`,
        },
      ],
      name: "$ex 'Outdoor: ' + (ys.at(-1) != null ? ys.at(-1).toFixed(1) + ' °F' : '—')",
      hovertemplate: "%{x|%H:%M}: %{y:.1f} °F<extra></extra>",
      yaxis: "y",
      line: { color: "rgb(100,180,255)", width: 2 },
    },
  ];

  const ac = deviceTrace(config.ac_entity, "Cooling", "rgb(30,144,255)", "cooling");
  const heat = deviceTrace(config.heater_entity, "Heating", "rgb(220,60,60)", "heating");
  if (ac) entities.push(ac);
  if (heat) entities.push(heat);
  // One trace per fan, each in a distinct teal/green/purple hue so multiple
  // fans stay visually separable on the shared State axis.
  config.fans.forEach((fan, i) => {
    const trace = deviceTrace(
      fan.entity_id,
      fan.label,
      FAN_TRACE_COLORS[i % FAN_TRACE_COLORS.length],
      "fan"
    );
    if (trace) entities.push(trace);
  });

  return {
    type: "custom:plotly-graph",
    hours_to_show: hours,
    refresh_interval: 60,
    config: {
      displayModeBar: false,
      scrollZoom: false,
    },
    entities,
    layout: {
      dragmode: false,
      height: 400,
      legend: {
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "center",
        x: 0.5,
      },
      margin: { t: 0, r: 60 },
      yaxis: {
        title: "°F",
        showgrid: false,
        zeroline: false,
        range: "$ex vars.tempYRange || [20, 100]",
        autorange: false,
        fixedrange: true,
      },
      yaxis2: {
        title: "State",
        showgrid: false,
        zeroline: false,
        overlaying: "y",
        side: "right",
        range: [0, 1.2],
        autorange: false,
        fixedrange: true,
        tickvals: [0, 1],
        ticktext: ["Off", "On"],
      },
      xaxis: { showgrid: false, fixedrange: true },
    },
  };
}

export function buildTimeRangeConfig(timeRange: string): Record<string, unknown> {
  return {
    type: "entities",
    entities: [{ entity: timeRange, name: "Time range (hours)" }],
  };
}

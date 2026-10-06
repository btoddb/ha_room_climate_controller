# Spec: Lovelace card & UX

The integration ships a companion Lit/TypeScript card (`custom:room-climate-control`)
and **auto-registers** it (UX-27). The card discovers a room's
sensors, devices, and control entities from the integration; the user only picks
a room in the visual editor. Source lives in
[`card/src/`](../../custom_components/btoddb_room_climate_controller/card/src/) — never
hand-edit the generated `www/` bundle.

## General UX rules

- **UX-1** Consistent styling across all cards and dialogs — buttons look and behave the same everywhere.
- **UX-2** Devices a room lacks are never shown (no empty sections).
- **UX-3** Avoid `browser_mod`. If it's the only way to implement something, ask first.
- **UX-4** Temperatures display with tenths; comparisons are whole-degree (CC-5). Show units.
- **UX-5** A fan that's **off** shows status "Off", not a 0%/speed percentage.
- **UX-29** While a fan that reports a direction is **running**, the Fan section's mode text appends the direction in parentheses — e.g. `50% (Reverse)` or `50% (Forward)`. "Off" stays plain (UX-5); fans without a direction attribute render unchanged.
- **UX-6** **No layout shift on button press.** After a press, indicate success by changing the button's *appearance* (not its size) and don't inject text that reflows the dialog.
- **UX-27** **Zero-setup, cache-proof card registration.** The integration serves the card bundle itself and registers it as a **Lovelace resource** whose URL carries a content-hash `?v=` param (when resources are YAML-managed it falls back to an extra frontend module). It must never rely on URLs baked into `index.html`: the frontend's service worker caches dashboard pages, so a page cached during HA startup would permanently miss the card ("custom element doesn't exist"). A bundle change must change the resource URL so stale HTTP/service-worker caches are bypassed.

## Main card

- **UX-7** Centered room-name heading, larger than body text (`2.25rem`).
- **UX-8** First section: room **temperature** and **humidity** — values + units only.
- **UX-9** One section per device type the room has (Cooling / Heating / Fan / Vent Fan), each showing:
  - **Name** — clicking opens the underlying device's more-info dialog.
  - **Mode** — the secondary line under the name shows the device's running mode only (e.g. "Cool", "50% (Reverse)", "Off"); it no longer carries a "(view only)" target readout.
  - **Target temp** — rendered as a **value + unit** (the word "target" is dropped) in a fixed-height line immediately above its up/down arrow pair (a `.target-stack`; the value never reflows the row whether or not a device is running — UX-6).
  - **Target temp arrows** — stacked triangle buttons immediately below the value, in the column described above. Up raises that device's target by 1°F; down lowers it by 1°F. Buttons clamp to the device number entity's min/max range, disable at the corresponding bound without layout shift, write the target number only, and leave Use / Fan Ovr toggles unchanged. The card reflects the target value Home Assistant reports after any server-side cross-device target constraint is applied.
  - **Fan Ovr** toggle (label "Fan Ovr") — only for fan-capable heating/cooling devices; editable (CC-12).
  - **Use** toggle (editable).
  - **Column order**, left to right: the humidity value+arrows stack (UX-33, only on humidity-triggered rows), the temp value+arrows stack above, **Fan Ovr**, then **Use**.

  When a room has **multiple standalone fans**, the **Fan** section renders **one row per fan** — each with its own Name (more-info link), target stack, (when the room has humidity control) its own humidity stack per UX-33, and Use toggle, laid out identically to the single-fan row. Fans are independent (CC-13), so one fan's row may show a running speed while another shows "Off". The room's **vent fan** (when configured) renders as its own row per UX-34. A configured device whose entity is unavailable/unknown keeps its row (UX-37).
- **UX-37** A configured device (Cooling, Heating, any standalone fan, or the vent fan) whose entity reads **unavailable** or **unknown** is **not hidden**: its row still renders, laid out identically to a normal row (Name/more-info link, target stack, humidity stack where applicable, Fan Ovr column, Use toggle — UX-6/UX-30), with the secondary-line status reading **"Unavailable"** instead of a speed, mode, or "On". A device whose entity does not exist (no state object), or whose Use switch does not exist, is still not shown. The History graph treats an unavailable/unknown device as **not on** (plotted as Off), never as On.
- **UX-30** All arrow columns — both the temperature stack and, when present, the humidity stack (UX-33) — align vertically across every device row. A row lacking a given column (Manual Mode has neither; Cooling/Heating never have a humidity stack) reserves a matching fixed-width spacer (`.target-stack-spacer`) so the Fan Ovr/Use toggles stay aligned with every other row.
- **UX-33** When a room has humidity control, every **humidity-triggered** device row — each standalone fan row and the vent fan row (UX-34) — shows a humidity value+arrows stack (identical shape to the temp stack: that row's own humidity target as value + `%` above an up/down arrow pair) in a fixed-width column immediately **left of** the temp arrow stack. Arrows step 1% and clamp to that device's humidity-target number's own min/max, and — for a standalone fan — further to `100 − that fan's humidity high offset` (mirroring CC-18; the vent fan has no offset, so its arrows clamp to its plain 30–90 range). The column (and its spacer on non-humidity rows, e.g. Cooling/Heating — UX-30) renders at all only when **at least one** row in the room actually has a configured humidity target; a room with no humidity control (no humidity sensor) renders neither the column nor its spacer on any row.
- **UX-34** The **vent fan** (when configured) renders as a device row after the standalone fan rows and before Manual Mode: **label** (the entity's friendly name, falling back to "Vent Fan"), **On/Off** status in the secondary line (extended per UX-36 while a manual override is active), the humidity stack (UX-33) and temp stack, and its **Use** toggle — it has no Fan Ovr or Reverse control, so a spacer is reserved in the Fan Ovr column to keep the Use toggle aligned with every other row (UX-30).
- **UX-36** While the vent fan's manual override (CC-38) is active, the vent row's status reads `<On|Off> · override until <h:mm AM/PM>` (browser-local time); it reverts to plain On/Off when the override ends.
- **UX-10** A **Manual Mode** row aligned so its toggle lines up vertically with the Use toggles. Since manual mode has no device, it's labeled "Manual Mode" on the left.
- **UX-26** When any of the room's **window sensors** reads open (CC-20), the **Cooling** and **Heating** Use toggles are visibly disabled (dimmed, non-interactive) — their displayed state is preserved, not cleared. A status banner between the temperature/humidity row and the Cooling row always shows the window state when at least one sensor is configured: **"A window is open"** (warning color) while any window is open, **"Windows are closed"** (secondary color) when all are closed. The settings-dialog target/offset inputs, the Fan section, Fan Ovr toggles, the vent row's Use toggle (UX-34), Manual Mode, and all Profiles actions stay interactive while a window is open.
- **UX-11** Below the device sections: **Settings**, **Energy**, and **History** buttons.

## Settings dialog

- **UX-12** Shows room temp + humidity, and per device a section with **target temp**, **medium offset**, and **high offset** (target as an input field; offsets as sliders). In the **Fan** section there is one **target temp** input **per fan** (each with its own Reverse — UX-28, and — when the room has a humidity sensor — its own humidity target + Medium/High offset rows, UX-31), but a **single shared** speed Medium/High offset slider pair for all of the room's fans (CC-14). A room with a configured **vent fan** also gets a **Vent Fan** section (UX-31).
- **UX-28** In the Fan section, **each fan** shows a **Reverse** toggle **only when that fan is reversible** (CC-22 auto-detection, surfaced per fan as `fan_reversible` in `rooms/list`). It behaves like every other toggle (standard entity toggle, immediate service call).
- **UX-31** There is no room-level Humidity settings section. Instead, inside the **Fan** section, **each fan** that has a humidity target configured shows its own humidity target input plus its own Medium/High offset sliders (same layout as the temperature rows above), each with the same computed "→ N%" threshold preview as the temperature sections — a fan without a humidity sensor-gated target shows none of these rows. A room with a configured **vent fan** additionally gets a **Vent Fan** settings section (after the Fan section) with a plain **target temp** input and — only when the room has a humidity sensor — a **Humidity target** input; the vent fan has no offsets to show (CC-37).
## Energy dialog

- **UX-14** A graph of the room's energy use (watts).

## History dialog

- **UX-15** A graph of when devices were actually **on**, plus current room temperature and outdoor temperature. "On" means the A/C/heater is actually heating/cooling — **fan-only counts as off** for this graph, as does an unavailable/unknown device (UX-37). No top margin.
- **UX-32** When a room has a humidity sensor, the History graph adds a **humidity trace** (legend `Humidity: N %`, whole-percent hover) plotted against the temperature axis, whose title becomes `°F / %`; the axis range grows to include the humidity values (UX-18). Rooms without a humidity sensor render the graph unchanged.
- **UX-35** When a room has a **vent fan** configured, the History graph adds one On/Off trace for it, **after** the per-fan traces, labeled with the vent fan's friendly name (falling back to "Vent Fan", consistent with UX-34) and styled like the fan traces (State axis, `y2`). Rooms without a vent fan render the graph unchanged.

## Graphs (Energy & History)

- **UX-16** Graphs use **lovelace-plotly-graph-card** (install via HACS) and the integration's own **graph time-range selector** (`select.*` graph_time_range, options 6/12/24/48/168 h, default 24). *(Older docs referenced an `input_select.time_range` helper; the integration now owns this selector so a fresh install works without a hand-made helper.)*
- **UX-17** Graph styling: no grid; don't fill below lines; mostly static except legend items toggle their series on/off; legend at top including current values + units; refresh interval 60 s; when the time range changes, the graph updates. Where a graph plots per-fan data, it renders **one trace per fan**.
- **UX-18** Temperature axis uses a fixed 20–100 range that grows if values fall outside it. Group series sensibly to the left/right axes.

## Profiles section (on the card)

A **Profiles** section sits below the Settings/Energy/History buttons and expands
on click. See `profiles.md` for behavior.

- **UX-19** Section header uses a bigger font than body text (smaller than the room name) to mark it as a distinct section.
- **UX-20** Expanded, it shows a **Copy room** button and an **Add** button, then the room's profiles ordered by time. Each row: **time** (AM/PM, right-justified in its column) then a short **name** (vertically aligned), slightly larger than body text.
- **UX-21** Clicking a profile expands it to show editable target temps, Use toggles, Fan-override toggles, and — for the room's fans — **one Fan row per fan**, each with its own target temp, Use toggle, and (when that fan is reversible) a **Reverse** toggle. Each device/fan row mirrors the main card's device-row layout: the label on the left, the temp input centered in the middle of the row, and the toggles grouped on the right as vertical stacks (label above toggle).
- **UX-22** **Add** swaps the dialog to a name + time entry with **Create**/**Cancel**. Time defaults to the next 15-minute interval; focus starts in the Name field.
- **UX-23** Each profile has **Apply now**, plus **copy/paste** buttons (PR-8–PR-10).
- **UX-24** If editing a profile's time **reorders the list**, the cursor/focus moves *with* that profile (it must not stay at the old index on a different profile).
- **UX-25** When a profile action fails (apply, rename, delete, time change, copy, paste), show a **user-readable inline error message** in that profile's row, in addition to the button's error flash. Success stays flash-only (UX-6).

## Sample dashboard

- A ready sample lives in [`examples/dashboard.yaml`](../../custom_components/btoddb_room_climate_controller/examples/dashboard.yaml).

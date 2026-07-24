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
- **UX-9** One section per device type the room has (Cooling / Heating / Fan), each showing:
  - **Name** — clicking opens the underlying device's more-info dialog.
  - **Target temp** (view only) and **Mode** (view only).
  - **Target temp arrows** — stacked triangle buttons immediately left of the Fan Ovr / Use toggle columns. Up raises that device's target by 1°F; down lowers it by 1°F. Buttons clamp to the device number entity's min/max range, disable at the corresponding bound without layout shift, write the target number only, and leave Use / Fan Ovr toggles unchanged. The card reflects the target value Home Assistant reports after any server-side cross-device target constraint is applied.
  - **Fan Ovr** toggle (label "Fan Ovr") — only for fan-capable heating/cooling devices; placed left of the Use toggle; editable (CC-12).
  - **Use** toggle (editable).

  When a room has **multiple standalone fans**, the **Fan** section renders **one row per fan** — each with its own Name (more-info link), Target temp + target arrows, and Use toggle, laid out identically to the single-fan row. Fans are independent (CC-13), so one fan's row may show a running speed while another shows "Off".
- **UX-30** Device-section target temp arrows align vertically across all device rows. Rows without a target, such as Manual Mode, reserve matching horizontal space so their toggles stay aligned with device rows.
- **UX-10** A **Manual Mode** row aligned so its toggle lines up vertically with the Use toggles. Since manual mode has no device, it's labeled "Manual Mode" on the left.
- **UX-26** When any of the room's **window sensors** reads open (CC-20), the **Cooling** and **Heating** Use toggles are visibly disabled (dimmed, non-interactive) — their displayed state is preserved, not cleared. A status banner between the temperature/humidity row and the Cooling row always shows the window state when at least one sensor is configured: **"A window is open"** (warning color) while any window is open, **"Windows are closed"** (secondary color) when all are closed. The settings-dialog target/offset inputs, the Fan section, Fan Ovr toggles, Manual Mode, and all Profiles actions stay interactive while a window is open.
- **UX-11** Below the device sections: **Settings**, **Energy**, and **History** buttons.

## Settings dialog

- **UX-12** Shows room temp + humidity, and per device a section with **target temp**, **medium offset**, and **high offset** (target as an input field; offsets as sliders). In the **Fan** section there is one **target temp** input **per fan** (each with its own Reverse — UX-28), but a **single shared** Medium/High offset slider pair for all of the room's fans (CC-14).
- **UX-28** In the Fan section, **each fan** shows a **Reverse** toggle **only when that fan is reversible** (CC-22 auto-detection, surfaced per fan as `fan_reversible` in `rooms/list`). It behaves like every other toggle (standard entity toggle, immediate service call).
## Energy dialog

- **UX-14** A graph of the room's energy use (watts).

## History dialog

- **UX-15** A graph of when devices were actually **on**, plus current room temperature and outdoor temperature. "On" means the A/C/heater is actually heating/cooling — **fan-only counts as off** for this graph. No top margin.

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

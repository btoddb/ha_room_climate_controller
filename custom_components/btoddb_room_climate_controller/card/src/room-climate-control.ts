import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type { HomeAssistant, LovelaceCardConfig } from "./ha-types";
import {
  buildEnergyGraphConfig,
  buildHistoryGraphConfig,
  buildTimeRangeConfig,
} from "./graph-configs";
import {
  buildDeviceSettingsFields,
  buildFanSettingsFields,
  buildHumiditySettingsFields,
  renderDeviceSettingsSection,
  renderFanSettingsSection,
  renderHumiditySettingsSection,
  renderRoomSettingsSection,
} from "./settings-ui";
import { GraphOverlay } from "./graph-overlay";
import {
  createLovelaceCard,
  createPlotlyGraphCard,
  entityAvailable,
  entityConfigured,
  fireMoreInfo,
  formatPowerNow,
  formatSensorValue,
  getEffectiveTargetLimits,
  getFanMode,
  getHvacMode,
  getNumberLimits,
  getStateObj,
  getTargetTemp,
  getTargetTempValue,
  setInputNumber,
  type TargetTempDevice,
  windowOpen,
} from "./helpers";
import { cardStyles } from "./styles";
import { getRoomsSync, refreshProfiles, subscribeProfiles } from "./profiles/store";
import { resolveProfileRoomKey } from "./profiles/live-room";
import {
  resolveRoomConfig,
  resolveRoomKey,
  roomKnown,
  roomsAvailable,
} from "./resolve-room";
import {
  DEFAULT_TIME_RANGE,
  type RoomClimateControlConfig,
  type RoomClimateUserConfig,
} from "./types";

@customElement("room-climate-control")
export class RoomClimateControl extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;

  @state() private _config?: RoomClimateControlConfig;
  @state() private _configError?: string;
  @state() private _settingsOpen = false;
  @state() private _graphDialog: "energy" | "history" | null = null;
  private _userConfig!: RoomClimateUserConfig;
  private _unsubRooms?: () => void;
  private _graphOverlay = new GraphOverlay();
  private _lastGraphHours?: string;
  private _overlayGraphEl?: HTMLElement & { hass?: HomeAssistant };
  private _overlayPowerNowEl?: HTMLElement;

  public setConfig(config: RoomClimateUserConfig): void {
    this._userConfig = config;
    if (!resolveRoomKey(config)) {
      this._config = undefined;
      this._configError = "Select a room in the card editor.";
      return;
    }
    this._configError = undefined;
    this._resolveRoom();
  }

  /** Populate the render config from the rooms/list cache for the chosen room.
  Leaves a loading state (no error) while the cache is still being fetched. */
  private _resolveRoom(): void {
    const resolved = resolveRoomConfig(this._userConfig);
    if (resolved) {
      this._config = resolved;
      this._configError = undefined;
      return;
    }
    const key = resolveRoomKey(this._userConfig);
    if (roomsAvailable() && key && !roomKnown(key)) {
      this._config = undefined;
      this._configError = `Unknown room "${key}". Add it in the Room Climate integration.`;
    }
  }

  public getCardSize(): number {
    return 6;
  }

  public static getConfigElement(): HTMLElement {
    return document.createElement("room-climate-control-editor");
  }

  public static getStubConfig(): RoomClimateUserConfig {
    const first = getRoomsSync()[0]?.key;
    return { type: "custom:room-climate-control", room: first ?? "" };
  }

  connectedCallback(): void {
    super.connectedCallback();
    void refreshProfiles(this.hass);
    // Re-resolve when the rooms/list cache loads or changes.
    this._unsubRooms ??= subscribeProfiles(() => {
      this._resolveRoom();
      this.requestUpdate();
    });
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this._graphOverlay.close();
    this._unsubRooms?.();
    this._unsubRooms = undefined;
  }

  protected updated(changed: Map<string, unknown>): void {
    if (!changed.has("hass") || !this._graphDialog || !this.hass || !this._config)
      return;
    if (this._overlayGraphEl) {
      this._overlayGraphEl.hass = this.hass;
    }
    if (this._overlayPowerNowEl && entityConfigured(this._config.power_sensor)) {
      this._overlayPowerNowEl.textContent = formatPowerNow(
        this.hass,
        this._config.power_sensor
      );
    }
    const timeRange = this._config.time_range || DEFAULT_TIME_RANGE;
    const hours = this.hass.states[timeRange]?.state;
    if (hours && hours !== this._lastGraphHours) {
      this._lastGraphHours = hours;
      void this._mountGraphOverlay();
    }
  }

  protected render() {
    if (!this.hass) return html``;
    if (this._configError) {
      return html`
        <ha-card>
          <div class="config-error">
            <ha-icon icon="mdi:alert-circle"></ha-icon>
            <span>${this._configError}</span>
          </div>
        </ha-card>
      `;
    }
    if (!this._config) {
      return html`
        <ha-card>
          <div class="config-error">
            <ha-icon icon="mdi:progress-clock"></ha-icon>
            <span>Loading room…</span>
          </div>
        </ha-card>
      `;
    }
    const c = this._config;

    return html`
      <ha-card>
        <div class="header">${c.room_name}</div>
        <div class="sensor-row">
          <div class="sensor-item">
            <span class="sensor-value">${formatSensorValue(this.hass, c.temp_sensor)}</span>
          </div>
          ${entityConfigured(c.humidity_sensor)
            ? html`
                <div class="sensor-item">
                  <span class="sensor-value"
                    >${formatSensorValue(this.hass, c.humidity_sensor)}</span
                  >
                </div>
              `
            : nothing}
        </div>

        ${this._renderDevicesPanel(c)}

        <div class="footer">
          <button class="rcc-btn footer-btn" @click=${() => this._openSettings()}>
            <ha-icon icon="mdi:cog"></ha-icon>
            <span>Settings</span>
          </button>
          <button
            class="rcc-btn footer-btn"
            ?disabled=${!entityConfigured(c.power_sensor)}
            @click=${() => this._openGraphDialog("energy")}
          >
            <ha-icon icon="mdi:flash"></ha-icon>
            <span>Energy Use</span>
            <span class="footer-secondary"
              >${formatPowerNow(this.hass, c.power_sensor)}</span
            >
          </button>
          <button class="rcc-btn footer-btn" @click=${() => this._openGraphDialog("history")}>
            <ha-icon icon="mdi:chart-line"></ha-icon>
            <span>History</span>
          </button>
        </div>

        ${this._renderProfilesPanel(c)}
      </ha-card>
      ${this._settingsOpen ? this._renderSettingsDialog() : nothing}
    `;
  }

  private _renderProfilesPanel(c: RoomClimateControlConfig) {
    const roomKey = resolveProfileRoomKey(c);
    if (!roomKey) return nothing;
    return html`
      <room-climate-profiles-panel
        .hass=${this.hass}
        .config=${c}
        .roomKey=${roomKey}
      ></room-climate-profiles-panel>
    `;
  }

  private _renderDevicesPanel(c: RoomClimateControlConfig) {
    const rows: TemplateResult[] = [];
    // Open window suppresses cooling/heating (CC-20); the Use toggles for those
    // devices are disabled while open (UX-26). Fan-only override stays live.
    const winOpen = windowOpen(this.hass, c.window_sensors);

    const addDevice = (
      label: string,
      targetDevice: TargetTempDevice,
      deviceEntity: string | undefined,
      useToggle: string,
      targetHelper: string,
      highOffsetHelper: string,
      modeFn: (hass: HomeAssistant, id: string) => string,
      fanOnlyOverrideToggle?: string,
      suppressed = false
    ) => {
      if (!entityConfigured(deviceEntity) || !entityAvailable(this.hass, deviceEntity)) {
        return;
      }
      const useState = getStateObj(this.hass, useToggle);
      if (!useState) return;

      const entity = deviceEntity!;
      const targetTemp = getTargetTemp(this.hass, targetHelper);
      const siblingTarget =
        targetDevice === "cooling" && entityConfigured(c.heater_entity)
          ? getTargetTempValue(this.hass, c.target_heating)
          : targetDevice === "heating" && entityConfigured(c.ac_entity)
            ? getTargetTempValue(this.hass, c.target_cooling)
            : undefined;
      const { min, max } = getEffectiveTargetLimits(
        targetDevice,
        getNumberLimits(this.hass, targetHelper),
        siblingTarget,
        getTargetTempValue(this.hass, highOffsetHelper)
      );
      const mode = modeFn(this.hass, entity);
      const adjustTarget = (delta: number) => {
        const next = Math.min(max, Math.max(min, targetTemp + delta));
        if (next !== targetTemp) setInputNumber(this.hass, targetHelper, next);
      };
      // Trust the backend: it only exposes the fan-only override entity when the
      // room is configured with `has_ac && ac_fan_only`. Don't re-gate on the
      // climate entity advertising `fan_only`, since portable ACs deliver
      // fan-only circulation through a separate fan entity + power switch.
      const showFanOvrCol = entityConfigured(fanOnlyOverrideToggle);
      const fanOvrState = showFanOvrCol
        ? getStateObj(this.hass, fanOnlyOverrideToggle!)
        : undefined;

      rows.push(html`
        <div class="device-row">
          <div
            class="device-info"
            role="button"
            tabindex="0"
            @click=${() => fireMoreInfo(this, entity)}
            @keydown=${(ev: KeyboardEvent) => {
              if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                fireMoreInfo(this, entity);
              }
            }}
          >
            <div class="device-label">${label}</div>
            <div class="device-secondary">
              <span>${targetTemp}°F target</span>
              <span class="device-meta-sep">·</span>
              <span>${mode}</span>
            </div>
          </div>
          <div class="device-toggles">
            <div class="temp-arrows">
              <button
                class="rcc-btn temp-arrow-btn"
                aria-label=${`Raise ${label} target`}
                .disabled=${targetTemp >= max}
                @click=${() => adjustTarget(1)}
              >
                <ha-icon icon="mdi:menu-up"></ha-icon>
              </button>
              <button
                class="rcc-btn temp-arrow-btn"
                aria-label=${`Lower ${label} target`}
                .disabled=${targetTemp <= min}
                @click=${() => adjustTarget(-1)}
              >
                <ha-icon icon="mdi:menu-down"></ha-icon>
              </button>
            </div>
            ${showFanOvrCol && fanOvrState
              ? html`
                  <div class="use-toggle">
                    <span class="use-label">Fan Ovr</span>
                    <ha-entity-toggle
                      .hass=${this.hass}
                      .stateObj=${fanOvrState}
                    ></ha-entity-toggle>
                  </div>
                `
              : html`<div class="toggle-spacer" aria-hidden="true"></div>`}
            <div class="use-toggle">
              <span class="use-label">Use</span>
              <div
                class=${suppressed ? "rcc-suppressed" : ""}
                aria-disabled=${suppressed ? "true" : nothing}
                title=${suppressed ? "Disabled while the window is open" : nothing}
              >
                <ha-entity-toggle .hass=${this.hass} .stateObj=${useState}></ha-entity-toggle>
              </div>
            </div>
          </div>
        </div>
      `);
    };

    const sharedClimateDevice =
      entityConfigured(c.ac_entity) &&
      entityConfigured(c.heater_entity) &&
      c.ac_entity === c.heater_entity;

    addDevice(
      "Cooling",
      "cooling",
      c.ac_entity,
      c.use_ac,
      c.target_cooling,
      c.cooling_high_offset,
      getHvacMode,
      c.ac_fan_only_override,
      winOpen
    );
    addDevice(
      "Heating",
      "heating",
      c.heater_entity,
      c.use_heater,
      c.target_heating,
      c.heating_high_offset,
      getHvacMode,
      sharedClimateDevice ? undefined : c.heater_fan_only_override,
      winOpen
    );
    // One row per fan. All fans share the room's single high offset (used only
    // for the effective-limit math), but each has its own use/target/reverse.
    for (const fan of c.fans) {
      addDevice(
        fan.label,
        "fan",
        fan.entity_id,
        fan.use,
        fan.target,
        c.fan_high_offset,
        (hass, id) => getFanMode(hass, id, fan.reversible)
      );
    }

    if (rows.length === 0) return nothing;

    if ((c.window_sensors ?? []).length > 0) {
      rows.unshift(html`
        <div class="device-row window-status-row ${winOpen ? "window-status-open" : ""}">
          <span>🪟</span>
          <span class="device-label"
            >${winOpen ? "A window is open" : "Windows are closed"}</span
          >
        </div>
      `);
    }

    const manualState = getStateObj(this.hass, c.manual_mode);
    if (manualState) {
      rows.push(html`
        <div class="device-row manual-row">
          <div class="device-info">
            <div class="device-label">Manual Mode</div>
          </div>
          <div class="device-toggles">
            <div class="temp-arrows-spacer" aria-hidden="true"></div>
            <div class="toggle-spacer" aria-hidden="true"></div>
            <div class="use-toggle">
              <span class="use-label">Use</span>
              <ha-entity-toggle .hass=${this.hass} .stateObj=${manualState}></ha-entity-toggle>
            </div>
          </div>
        </div>
      `);
    }

    return html`<div class="devices-section">${rows}</div>`;
  }

  private _renderSettingsDialog() {
    if (!this._config) return nothing;
    const deviceSections = buildDeviceSettingsFields(this._config);
    const fanFields = buildFanSettingsFields(this._config);
    const humidityFields = buildHumiditySettingsFields(this._config);
    return html`
      <ha-dialog
        open
        .heading=${`${this._config.room_name} · Settings`}
        @closed=${this._closeSettings}
        hideActions
      >
        <div class="dialog-body">
          ${renderRoomSettingsSection(this.hass, this._config)}
          ${deviceSections.map((fields) =>
            renderDeviceSettingsSection(this.hass, fields)
          )}
          ${fanFields ? renderFanSettingsSection(this.hass, fanFields) : nothing}
          ${humidityFields
            ? renderHumiditySettingsSection(this.hass, humidityFields)
            : nothing}
        </div>
      </ha-dialog>
    `;
  }

  private _openSettings(): void {
    this._closeGraphDialog();
    this._settingsOpen = true;
  }

  private _closeSettings(): void {
    this._settingsOpen = false;
  }

  private _openGraphDialog(dialog: "energy" | "history"): void {
    if (!this._config) return;
    this._closeSettings();
    this._graphDialog = dialog;
    this._lastGraphHours =
      this.hass.states[this._config.time_range || DEFAULT_TIME_RANGE]?.state;
    void this._mountGraphOverlay();
  }

  private _closeGraphDialog(): void {
    if (this._graphOverlay.isOpen) {
      this._graphOverlay.close();
      return;
    }
    this._clearGraphDialogState();
  }

  private _clearGraphDialogState(): void {
    this._graphDialog = null;
    this._lastGraphHours = undefined;
    this._overlayGraphEl = undefined;
    this._overlayPowerNowEl = undefined;
  }

  private async _mountGraphOverlay(): Promise<void> {
    if (!this._graphDialog || !this.hass || !this._config) return;
    const c = this._config;

    const titles = {
      energy: `${c.room_name} · Energy Use`,
      history: `${c.room_name} · History`,
    };

    const host = this._graphOverlay.open(titles[this._graphDialog], () => {
      this._clearGraphDialogState();
    });
    host.innerHTML = "";
    this._overlayGraphEl = undefined;
    this._overlayPowerNowEl = undefined;

    const timeRange = c.time_range || DEFAULT_TIME_RANGE;
    const hours = parseInt(this.hass.states[timeRange]?.state || "24", 10) || 24;

    // Only show the range selector when a time-range entity is known (the
    // integration provides one; falls back to a fixed window otherwise).
    if (timeRange) {
      const rangeCard = await createLovelaceCard(
        this.hass,
        buildTimeRangeConfig(timeRange) as LovelaceCardConfig
      );
      if (rangeCard) host.appendChild(rangeCard);
    }

    if (this._graphDialog === "energy") {
      if (entityConfigured(c.power_sensor)) {
        const line = document.createElement("div");
        line.className = "rcc-overlay-power-now";
        line.textContent = formatPowerNow(this.hass, c.power_sensor);
        host.appendChild(line);
        this._overlayPowerNowEl = line;
      }
      const graphHost = document.createElement("div");
      graphHost.className = "rcc-overlay-graph-host";
      host.appendChild(graphHost);
      const graph = await createPlotlyGraphCard(
        this.hass,
        buildEnergyGraphConfig(c, hours)
      );
      if (graph) {
        graphHost.appendChild(graph);
        this._overlayGraphEl = graph;
      } else {
        graphHost.textContent = "Unable to load energy graph (plotly-graph unavailable).";
      }
      this._lastGraphHours = this.hass.states[timeRange]?.state;
      return;
    }

    const graphHost = document.createElement("div");
    graphHost.className = "rcc-overlay-graph-host";
    host.appendChild(graphHost);
    const graph = await createPlotlyGraphCard(
      this.hass,
      buildHistoryGraphConfig(c, hours)
    );
    if (graph) {
      graphHost.appendChild(graph);
      this._overlayGraphEl = graph;
    } else {
      graphHost.textContent = "Unable to load history graph (plotly-graph unavailable).";
    }

    this._lastGraphHours = this.hass.states[timeRange]?.state;
  }

  static get styles() {
    return cardStyles;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "room-climate-control": RoomClimateControl;
  }
}

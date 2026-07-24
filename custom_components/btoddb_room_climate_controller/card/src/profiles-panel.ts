import { LitElement, html, nothing, type PropertyValues, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { repeat } from "lit/directives/repeat.js";
import type { HomeAssistant } from "./ha-types";
import {
  applyClipboardPayload,
  buildClipboardPayload,
  parseClipboardPayload,
  readRoutineClipboard,
  writeRoutineClipboard,
} from "./profiles/clipboard";
import { ButtonFeedbackController, type ButtonFeedback } from "./profiles/button-feedback";
import { editableProfileName, routineIsConfigured } from "./profiles/discover";
import { copyRoomSettings } from "./profiles/copy-room-settings";
import {
  refreshProfiles,
  routinesForRoom,
  subscribeProfiles,
} from "./profiles/store";
import {
  wsApplyProfile,
  wsCreateProfile,
  wsDeleteProfile,
  wsErrorMessage,
  wsRenameProfile,
} from "./profiles/api";
import {
  findTimeConflict,
  normalizeProfileTime,
  profileHasTimeConflict,
  suggestDefaultProfileTime,
} from "./profiles/time-conflict";
import type { RoutineConfig, RoomPresetConfig } from "./profiles/types";
import {
  entityConfigured,
  formatTime12h,
  formatTimeState,
  getStateObj,
  setInputDateTimeTime,
  setInputNumber,
  setToggle,
} from "./helpers";
import { profileSectionStyles } from "./profile-styles";
import type { RoomClimateControlConfig } from "./types";

@customElement("room-climate-profiles-panel")
export class RoomClimateProfilesPanel extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public config!: RoomClimateControlConfig;
  @property({ attribute: false }) public roomKey!: string;

  @state() private _profilesOpen = false;
  @state() private _busy = false;
  @state() private _newProfileName = "";
  @state() private _newProfileTime = "06:00";
  @state() private _showAddForm = false;
  @state() private _addError = "";
  @state() private _actionError: Record<string, string> = {};
  @state() private _renameDrafts: Record<string, string> = {};
  @state() private _feedbackVersion = 0;
  @state() private _fieldFeedback: Record<string, ButtonFeedback> = {};
  @state() private _openProfileIds = new Set<string>();
  @state() private _focusTarget?: { profileId: string; selector: string };
  /** Set when the Add form opens — focus the Name field once it renders. */
  @state() private _focusAddName = false;
  /** Set when Copy room is pressed during Add — paste into the new profile after create. */
  @state() private _pasteRoomSettingsOnCreate = false;

  private _buttonFeedback = new ButtonFeedbackController(() => {
    this._feedbackVersion += 1;
  });
  private _unsubStore?: () => void;

  connectedCallback(): void {
    super.connectedCallback();
    this._unsubStore = subscribeProfiles(() => this.requestUpdate());
    void refreshProfiles(this.hass);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this._unsubStore?.();
    this._unsubStore = undefined;
    this._buttonFeedback.dispose();
  }

  private _routines(): RoutineConfig[] {
    return routinesForRoom(this.roomKey);
  }

  private _flashField(key: string, result: ButtonFeedback): void {
    this._fieldFeedback = { ...this._fieldFeedback, [key]: result };
    window.setTimeout(() => {
      if (this._fieldFeedback[key] !== result) return;
      const next = { ...this._fieldFeedback };
      delete next[key];
      this._fieldFeedback = next;
    }, 2000);
  }

  private _btnFeedback(key: string): ButtonFeedback | undefined {
    void this._feedbackVersion;
    return this._buttonFeedback.get(key);
  }

  protected updated(changedProperties: PropertyValues): void {
    super.updated(changedProperties);
    if (this._focusAddName && this.shadowRoot) {
      this._focusAddName = false;
      requestAnimationFrame(() => {
        const nameInput = this.shadowRoot?.querySelector(
          ".profile-add-name"
        ) as HTMLElement | null;
        nameInput?.focus();
      });
    }
    if (!this._focusTarget || !this.shadowRoot) return;
    const { profileId, selector } = this._focusTarget;
    const nextOpen = new Set(this._openProfileIds);
    nextOpen.add(profileId);
    this._openProfileIds = nextOpen;
    requestAnimationFrame(() => {
      const el = this.shadowRoot?.querySelector(selector) as HTMLElement | null;
      el?.focus();
      this._focusTarget = undefined;
    });
  }

  private _renderActionButton(
    feedbackKey: string,
    label: string,
    icon: string,
    onClick: () => void | Promise<void>,
    opts: { primary?: boolean; danger?: boolean } = {}
  ): TemplateResult {
    const fb = this._btnFeedback(feedbackKey);
    const displayIcon =
      fb === "success" ? "mdi:check" : fb === "error" ? "mdi:alert-circle-outline" : icon;
    return html`
      <button
        type="button"
        class="rcc-btn profile-action-btn ${opts.primary ? "primary" : ""} ${opts.danger
          ? "danger"
          : ""} ${fb ?? ""}"
        ?disabled=${this._busy}
        @click=${() => void onClick()}
      >
        <ha-icon icon=${displayIcon}></ha-icon>
        <span>${label}</span>
      </button>
    `;
  }

  private _renderDeviceRow(
    label: string,
    useEntityId: string | undefined,
    tempEntityId: string | undefined,
    extraToggle?: { label: string; entityId?: string }
  ): TemplateResult | typeof nothing {
    const tempObj = entityConfigured(tempEntityId)
      ? getStateObj(this.hass, tempEntityId!)
      : undefined;
    const useObj = entityConfigured(useEntityId)
      ? getStateObj(this.hass, useEntityId!)
      : undefined;
    const extraObj = entityConfigured(extraToggle?.entityId)
      ? getStateObj(this.hass, extraToggle!.entityId!)
      : undefined;
    if (!tempObj && !useObj && !extraObj) return nothing;

    const min = Number(tempObj?.attributes.min ?? 0);
    const max = Number(tempObj?.attributes.max ?? 100);
    const step = Number(tempObj?.attributes.step ?? 1);
    const val = tempObj ? parseFloat(tempObj.state) : NaN;
    const display = Number.isNaN(val) ? "" : String(Math.round(val));

    return html`
      <div class="profile-device-row">
        <span class="profile-device-label">${label}</span>
        <div class="profile-device-temp">
          ${tempObj
            ? html`
                <div class="profile-temp">
                  <input
                    type="number"
                    class="profile-temp-input"
                    min=${min}
                    max=${max}
                    step=${step}
                    .value=${display}
                    @change=${(ev: Event) => {
                      const n = parseFloat((ev.target as HTMLInputElement).value);
                      if (!Number.isNaN(n)) setInputNumber(this.hass, tempEntityId!, n);
                    }}
                  />
                  <span class="profile-temp-unit">°F</span>
                </div>
              `
            : nothing}
        </div>
        <div class="profile-device-toggles">
          ${extraObj
            ? html`
                <div class="profile-use">
                  <span class="profile-use-label">${extraToggle!.label}</span>
                  <ha-entity-toggle .hass=${this.hass} .stateObj=${extraObj}></ha-entity-toggle>
                </div>
              `
            : nothing}
          ${useObj
            ? html`
                <div class="profile-use">
                  <span class="profile-use-label">Use</span>
                  <ha-entity-toggle .hass=${this.hass} .stateObj=${useObj}></ha-entity-toggle>
                </div>
              `
            : nothing}
        </div>
      </div>
    `;
  }

  private _renderRoom(room: RoomPresetConfig): TemplateResult {
    if (!room.roomKey) {
      return html`<div class="profile-hint">Preset entities loading… refresh in a moment.</div>`;
    }
    return html`
      <div class="profile-room-block">
        ${this._renderDeviceRow("Cooling", room.useCooling, room.cooling, {
          label: "Fan Ovr",
          entityId: room.fanOverride,
        })}
        ${room.has_heating !== false
          ? this._renderDeviceRow("Heating", room.useHeating, room.heating)
          : nothing}
        ${room.fans.map((fan) =>
          this._renderDeviceRow(
            fan.label,
            fan.useEntity,
            fan.tempEntity,
            fan.reversible
              ? { label: "Reverse", entityId: fan.reverseEntity }
              : undefined
          )
        )}
      </div>
    `;
  }

  private async _copyProfile(routine: RoutineConfig): Promise<void> {
    const key = `copy-${routine.profileId}`;
    this._clearActionError(routine.profileId);
    try {
      const payload = buildClipboardPayload(this.hass, [routine.room]);
      await writeRoutineClipboard(payload);
      this._buttonFeedback.flash(key, "success", 1500);
    } catch (err) {
      this._surfaceActionError(routine.profileId, err, "copy the profile");
      this._buttonFeedback.flash(key, "error");
    }
  }

  private async _pasteRoutine(routine: RoutineConfig): Promise<void> {
    const key = `paste-${routine.profileId}`;
    this._clearActionError(routine.profileId);
    const text = await readRoutineClipboard();
    if (!text) {
      this._setActionError(
        routine.profileId,
        "Nothing to paste — copy a profile first."
      );
      this._buttonFeedback.flash(key, "error");
      return;
    }
    const payload = parseClipboardPayload(text);
    if (!payload) {
      this._setActionError(
        routine.profileId,
        "The clipboard doesn't contain profile settings."
      );
      this._buttonFeedback.flash(key, "error");
      return;
    }
    const applied = applyClipboardPayload(
      this.hass,
      [routine.room],
      payload,
      (id, v) => setInputNumber(this.hass, id, v),
      (id, on) => setToggle(this.hass, id, on)
    );
    if (applied === 0) {
      this._setActionError(
        routine.profileId,
        "Nothing in the clipboard matched this room's devices."
      );
      this._buttonFeedback.flash(key, "error");
      return;
    }
    this._buttonFeedback.flash(key, "success");
  }

  private async _copyCurrentRoom(): Promise<void> {
    const key = "copy-room";
    try {
      const ok = await copyRoomSettings(this.hass, this.config, this.roomKey);
      if (ok && this._showAddForm) {
        this._pasteRoomSettingsOnCreate = true;
      }
      this._buttonFeedback.flash(key, ok ? "success" : "error", 1500);
    } catch {
      this._buttonFeedback.flash(key, "error");
    }
  }

  private _setActionError(profileId: string, message: string): void {
    this._actionError = { ...this._actionError, [profileId]: message };
  }

  private _clearActionError(profileId: string): void {
    if (this._actionError[profileId] === undefined) return;
    const next = { ...this._actionError };
    delete next[profileId];
    this._actionError = next;
  }

  /** Store a user-readable failure message in the profile's inline error area. */
  private _surfaceActionError(profileId: string, err: unknown, what: string): void {
    this._setActionError(
      profileId,
      wsErrorMessage(err) || `Could not ${what}. Check Settings → System → Logs.`
    );
  }

  private async _applyNow(routine: RoutineConfig): Promise<void> {
    const key = `apply-${routine.profileId}`;
    this._busy = true;
    this._clearActionError(routine.profileId);
    try {
      await wsApplyProfile(this.hass, routine.profileId);
      this._buttonFeedback.flash(key, "success");
    } catch (err) {
      this._surfaceActionError(routine.profileId, err, "apply the profile");
      this._buttonFeedback.flash(key, "error");
    } finally {
      this._busy = false;
    }
  }

  private _renameValue(routine: RoutineConfig): string {
    if (this._renameDrafts[routine.profileId] !== undefined) {
      return this._renameDrafts[routine.profileId];
    }
    return editableProfileName(routine.name, routine.profileId);
  }

  private _savedProfileName(routine: RoutineConfig): string {
    return editableProfileName(routine.name, routine.profileId);
  }

  private async _commitProfileName(
    routine: RoutineConfig,
    rawName: string
  ): Promise<void> {
    const name = rawName.trim();
    const saved = this._savedProfileName(routine);
    const fieldKey = `name-${routine.profileId}`;

    if (name === saved) {
      const next = { ...this._renameDrafts };
      delete next[routine.profileId];
      this._renameDrafts = next;
      return;
    }

    if (!name) {
      const next = { ...this._renameDrafts };
      delete next[routine.profileId];
      this._renameDrafts = next;
      this._setActionError(routine.profileId, "Enter a profile name.");
      this._flashField(fieldKey, "error");
      return;
    }

    this._busy = true;
    this._clearActionError(routine.profileId);
    try {
      await wsRenameProfile(this.hass, routine.profileId, name);
      const next = { ...this._renameDrafts };
      delete next[routine.profileId];
      this._renameDrafts = next;
      await refreshProfiles(this.hass);
      this._flashField(fieldKey, "success");
    } catch (err) {
      this._surfaceActionError(routine.profileId, err, "rename the profile");
      this._flashField(fieldKey, "error");
    } finally {
      this._busy = false;
    }
  }

  private async _setProfileTime(routine: RoutineConfig, newTime: string): Promise<void> {
    const fieldKey = `time-${routine.profileId}`;
    this._clearActionError(routine.profileId);
    if (findTimeConflict(this.hass, this.roomKey, routine.profileId, newTime)) {
      this._setActionError(
        routine.profileId,
        this._timeConflictMessage(newTime, routine.profileId)
      );
      this._flashField(fieldKey, "error");
      return;
    }
    if (!entityConfigured(routine.time)) return;
    try {
      await setInputDateTimeTime(this.hass, routine.time, `${newTime}:00`);
      await refreshProfiles(this.hass);
      this._flashField(fieldKey, "success");
    } catch (err) {
      this._surfaceActionError(routine.profileId, err, "change the profile time");
      this._flashField(fieldKey, "error");
    }
  }

  private _timeConflictMessage(time: string, excludeProfileId = ""): string {
    const conflict = findTimeConflict(this.hass, this.roomKey, excludeProfileId, time);
    if (!conflict) return "Another profile in this room already uses that time.";
    const label = editableProfileName(conflict.name, conflict.profileId);
    return `“${label}” already runs at ${time} in this room.`;
  }

  private async _addProfile(): Promise<void> {
    const key = "add-profile";
    const timeKey = "add-profile-time";
    const name = this._newProfileName.trim();
    const time = normalizeProfileTime(this._newProfileTime);
    this._addError = "";
    if (!name) {
      this._addError = "Enter a profile name.";
      this._buttonFeedback.flash(key, "error");
      return;
    }
    if (!time) {
      this._addError = "Choose a valid time.";
      this._flashField(timeKey, "error");
      return;
    }
    if (findTimeConflict(this.hass, this.roomKey, "", time)) {
      this._addError = this._timeConflictMessage(time);
      this._flashField(timeKey, "error");
      return;
    }
    this._busy = true;
    try {
      const result = await wsCreateProfile(this.hass, {
        name,
        room: this.roomKey,
        time,
        copy_room_settings: this._pasteRoomSettingsOnCreate,
      });
      await refreshProfiles(this.hass);
      this._pasteRoomSettingsOnCreate = false;

      const newId = result.profile?.id;
      if (newId) {
        this._openProfileIds = new Set([...this._openProfileIds, newId]);
      }
      this._newProfileName = "";
      this._newProfileTime = suggestDefaultProfileTime(this.hass, this.roomKey);
      this._showAddForm = false;
      this._addError = "";
      this._buttonFeedback.flash(key, "success");
    } catch (err) {
      this._addError =
        wsErrorMessage(err) ||
        "Could not create profile. Check Settings → System → Logs.";
      this._buttonFeedback.flash(key, "error");
    } finally {
      this._busy = false;
    }
  }

  private async _deleteProfile(routine: RoutineConfig): Promise<void> {
    const key = `delete-${routine.profileId}`;
    const label = editableProfileName(routine.name, routine.profileId);
    if (!window.confirm(`Delete profile “${label}”?`)) {
      return;
    }
    this._busy = true;
    this._clearActionError(routine.profileId);
    try {
      await wsDeleteProfile(this.hass, routine.profileId);
      const nextOpen = new Set(this._openProfileIds);
      nextOpen.delete(routine.profileId);
      this._openProfileIds = nextOpen;
      await refreshProfiles(this.hass);
      this._buttonFeedback.flash(key, "success", 800);
    } catch (err) {
      this._surfaceActionError(routine.profileId, err, "delete the profile");
      this._buttonFeedback.flash(key, "error");
    } finally {
      this._busy = false;
    }
  }

  private _renderProfile(routine: RoutineConfig): TemplateResult {
    const timeValue = entityConfigured(routine.time)
      ? formatTimeState(this.hass.states[routine.time]?.state)
      : "";
    const shortName = editableProfileName(routine.name, routine.profileId);
    const timeConflict = profileHasTimeConflict(this.hass, routine);
    const timeFieldKey = `time-${routine.profileId}`;
    const nameFieldKey = `name-${routine.profileId}`;

    return html`
      <details
        class="profile-item"
        .open=${this._openProfileIds.has(routine.profileId)}
        @toggle=${(ev: Event) => {
          const details = ev.target as HTMLDetailsElement;
          const next = new Set(this._openProfileIds);
          if (details.open) next.add(routine.profileId);
          else next.delete(routine.profileId);
          this._openProfileIds = next;
        }}
      >
        <summary class="profile-item-summary">
          <span class="profile-time ${timeConflict ? "duplicate" : ""}"
            >${timeValue ? formatTime12h(timeValue) : "—:—"}</span
          >
          <span class="profile-short-name">${shortName}</span>
          <span class="profile-chevron">▼</span>
        </summary>
        <div class="profile-item-body">
          <div class="profile-name-row">
            <label class="profile-field-label profile-name-field">
              Name
              <input
                type="text"
                class="profile-name-input ${this._fieldFeedback[nameFieldKey] === "error"
                  ? "field-error"
                  : ""}"
                .value=${this._renameValue(routine)}
                ?disabled=${this._busy}
                @input=${(ev: Event) => {
                  this._renameDrafts = {
                    ...this._renameDrafts,
                    [routine.profileId]: (ev.target as HTMLInputElement).value,
                  };
                }}
                @change=${(ev: Event) => {
                  void this._commitProfileName(
                    routine,
                    (ev.target as HTMLInputElement).value
                  );
                }}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter") {
                    ev.preventDefault();
                    void this._commitProfileName(
                      routine,
                      (ev.target as HTMLInputElement).value
                    );
                  }
                }}
              />
            </label>
            <div class="profile-enable">
              <span class="profile-field-label">Enabled</span>
              ${entityConfigured(routine.enabled) &&
              getStateObj(this.hass, routine.enabled)
                ? html`
                    <ha-entity-toggle
                      .hass=${this.hass}
                      .stateObj=${getStateObj(this.hass, routine.enabled)!}
                    ></ha-entity-toggle>
                  `
                : nothing}
            </div>
          </div>
          <div class="profile-schedule">
            <label class="profile-field-label">
              Time
              <input
                id="profile-time-${routine.profileId}"
                type="time"
                class="profile-time-input ${this._fieldFeedback[timeFieldKey] === "error"
                  ? "conflict"
                  : ""}"
                .value=${timeValue}
                ?disabled=${this._busy}
                @blur=${(ev: Event) => {
                  const v = (ev.target as HTMLInputElement).value;
                  if (v) void this._setProfileTime(routine, v);
                }}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter") {
                    ev.preventDefault();
                    const v = (ev.target as HTMLInputElement).value;
                    if (v) void this._setProfileTime(routine, v);
                  }
                }}
              />
            </label>
          </div>
          ${this._renderRoom(routine.room)}
          <div class="profile-actions">
            ${this._renderActionButton(
              `apply-${routine.profileId}`,
              "Apply now",
              "mdi:check-circle-outline",
              () => this._applyNow(routine),
              { primary: true }
            )}
            ${this._renderActionButton(
              `copy-${routine.profileId}`,
              "Copy",
              "mdi:content-copy",
              () => this._copyProfile(routine)
            )}
            ${this._renderActionButton(
              `paste-${routine.profileId}`,
              "Paste",
              "mdi:content-paste",
              () => this._pasteRoutine(routine)
            )}
            ${this._renderActionButton(
              `delete-${routine.profileId}`,
              "Delete",
              "mdi:delete-outline",
              () => this._deleteProfile(routine),
              { danger: true }
            )}
          </div>
          ${this._actionError[routine.profileId]
            ? html`<div class="profile-add-error" role="alert">
                ${this._actionError[routine.profileId]}
              </div>`
            : nothing}
        </div>
      </details>
    `;
  }

  private _renderToolbar(): TemplateResult {
    return html`
      <div class="profiles-toolbar">
        ${this._renderActionButton(
          "copy-room",
          this._showAddForm ? "Use room settings" : "Copy room",
          "mdi:clipboard-arrow-up-outline",
          () => this._copyCurrentRoom()
        )}
        ${this._showAddForm
          ? html`
              <input
                type="text"
                class="profile-name-input profile-add-name"
                placeholder="Profile name"
                .value=${this._newProfileName}
                ?disabled=${this._busy}
                @input=${(ev: Event) => {
                  this._newProfileName = (ev.target as HTMLInputElement).value;
                  this._addError = "";
                }}
                @keydown=${(ev: KeyboardEvent) => {
                  if (ev.key === "Enter") void this._addProfile();
                }}
              />
              <label class="profile-field-label profile-add-time">
                Time
                <input
                  type="time"
                  class="profile-time-input ${this._fieldFeedback["add-profile-time"] === "error"
                    ? "conflict"
                    : ""}"
                  .value=${this._newProfileTime}
                  ?disabled=${this._busy}
                  @input=${(ev: Event) => {
                    this._newProfileTime = (ev.target as HTMLInputElement).value;
                    this._addError = "";
                  }}
                />
              </label>
              ${this._renderActionButton(
                "add-profile",
                "Create",
                "mdi:plus",
                () => this._addProfile(),
                { primary: true }
              )}
              <button
                type="button"
                class="rcc-btn profile-action-btn"
                ?disabled=${this._busy}
                @click=${() => {
                  this._showAddForm = false;
                  this._newProfileName = "";
                  this._newProfileTime = suggestDefaultProfileTime(this.hass, this.roomKey);
                  this._addError = "";
                  this._pasteRoomSettingsOnCreate = false;
                }}
              >
                <span>Cancel</span>
              </button>
              ${this._addError
                ? html`<div class="profile-add-error" role="alert">${this._addError}</div>`
                : nothing}
            `
          : html`
              <button
                type="button"
                class="rcc-btn profile-action-btn"
                ?disabled=${this._busy}
                @click=${() => {
                  this._newProfileName = "";
                  this._newProfileTime = suggestDefaultProfileTime(this.hass, this.roomKey);
                  this._addError = "";
                  this._pasteRoomSettingsOnCreate = false;
                  this._showAddForm = true;
                  this._focusAddName = true;
                }}
              >
                <ha-icon icon="mdi:plus"></ha-icon>
                <span>Add</span>
              </button>
            `}
      </div>
    `;
  }

  protected render() {
    const routines = this._routines();
    const configured = routines.filter(routineIsConfigured);

    return html`
      <details
        class="profiles-section"
        ?open=${this._profilesOpen}
        @toggle=${(ev: Event) => {
          this._profilesOpen = (ev.target as HTMLDetailsElement).open;
        }}
      >
        <summary class="profiles-section-summary">
          <span>Profiles</span>
          <span class="profiles-count">${routines.length}</span>
          <span class="profile-chevron">▼</span>
        </summary>
        <div class="profiles-section-body">
          ${this._renderToolbar()}
          ${configured.length < routines.length
            ? html`<div class="profiles-loading-hint">
                Some profiles are still loading after a reload.
              </div>`
            : nothing}
          ${routines.length === 0
            ? html`<div class="profile-hint">No profiles for this room yet.</div>`
            : repeat(
                routines,
                (r) => r.profileId,
                (r) => this._renderProfile(r)
              )}
        </div>
      </details>
    `;
  }

  static get styles() {
    return profileSectionStyles;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "room-climate-profiles-panel": RoomClimateProfilesPanel;
  }
}

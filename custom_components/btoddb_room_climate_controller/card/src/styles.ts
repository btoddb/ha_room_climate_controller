import { css } from "lit";

export const cardStyles = css`
  :host {
    display: block;
  }

  ha-card {
    overflow: hidden;
  }

  .header {
    text-align: center;
    padding: 16px 16px 8px;
    font-size: 2.25rem;
    font-weight: 700;
    line-height: 1.1;
  }

  .sensor-row {
    display: flex;
    gap: 8px;
    padding: 0 16px 8px;
  }

  .sensor-item {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.05));
  }

  .sensor-value {
    font-size: 1.1rem;
    font-weight: 500;
  }

  .devices-section {
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
  }

  .device-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
  }

  .devices-section .device-row:first-child {
    border-top: none;
  }

  .device-toggles {
    flex-shrink: 0;
    display: flex;
    flex-direction: row;
    align-items: flex-start;
    gap: 4px;
  }

  .toggle-spacer {
    flex-shrink: 0;
    width: 48px;
  }

  .temp-arrows {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    width: 36px;
    flex-shrink: 0;
  }

  .temp-arrow-btn {
    width: 32px;
    height: 20px;
    padding: 0;
    --mdc-icon-size: 18px;
  }

  /* A value+arrows column (temp or humidity, UX-9): the target value sits above
     the arrow pair in a fixed line-height so rows never shift whether a row's
     humidity stack is present or replaced by a spacer (UX-6). */
  .target-stack {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    width: 36px;
    flex-shrink: 0;
  }

  .target-stack-spacer {
    width: 36px;
    flex-shrink: 0;
  }

  .target-value {
    font-size: 0.7rem;
    line-height: 14px;
    color: var(--secondary-text-color);
    white-space: nowrap;
  }

  .device-info {
    flex: 1;
    min-width: 0;
    cursor: pointer;
  }

  .device-label {
    font-weight: 500;
  }

  .device-secondary {
    font-size: 0.85rem;
    color: var(--secondary-text-color);
  }

  .device-meta-sep {
    margin: 0 4px;
    color: var(--secondary-text-color);
  }

  .use-toggle {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    width: 48px;
  }

  .use-label {
    font-size: 0.75rem;
    color: var(--secondary-text-color);
  }

  /* Use toggle disabled while the window is open (UX-26). pointer-events: none
     because ha-entity-toggle has no disabled prop; value stays displayed. */
  .rcc-suppressed {
    opacity: 0.5;
    pointer-events: none;
  }

  /* Window status banner shown above Manual Mode (UX-26).
     Layout/padding/border come from .device-row; only color varies. */
  .window-status-row {
    color: var(--secondary-text-color);
  }

  .window-status-row.window-status-open {
    color: var(--warning-color, var(--secondary-text-color));
  }

  .footer {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.12));
  }

  /* Shared button style for card footer and settings dialogs */
  .rcc-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    padding: 10px 8px;
    border: none;
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.05));
    color: var(--primary-text-color);
    cursor: pointer;
    font: inherit;
  }

  .rcc-btn:hover:not(:disabled) {
    filter: brightness(1.04);
  }

  .rcc-btn:active:not(:disabled) {
    filter: brightness(0.96);
  }

  .rcc-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    filter: none;
  }

  .footer-btn {
    flex: 1;
  }

  .rcc-btn--block {
    width: 100%;
    flex-direction: row;
    font-weight: 500;
  }

  .footer-secondary {
    font-size: 0.7rem;
    color: var(--secondary-text-color);
  }

  ha-dialog {
    --dialog-content-padding: 0;
    --mdc-dialog-max-width: min(920px, 95vw);
  }

  .dialog-body {
    padding: 0 16px 16px;
    max-height: 75vh;
    overflow-y: auto;
  }

  .settings-section {
    margin-bottom: 12px;
    border-radius: var(--ha-card-border-radius, 12px);
    background: var(--card-background-color);
    overflow: hidden;
    box-shadow: var(
      --ha-card-box-shadow,
      0px 2px 1px -1px rgba(0, 0, 0, 0.2),
      0px 1px 1px 0px rgba(0, 0, 0, 0.14),
      0px 1px 3px 0px rgba(0, 0, 0, 0.12)
    );
  }

  .settings-section-title {
    padding: 12px 16px 4px;
    font-size: 1rem;
    font-weight: 500;
    color: var(--primary-text-color);
  }

  .settings-row,
  .settings-readout-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 16px;
    min-height: 48px;
    box-sizing: border-box;
  }

  .settings-readout-row {
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
  }

  .settings-row + .settings-row {
    border-top: 1px solid var(--divider-color, rgba(0, 0, 0, 0.08));
  }

  .settings-row-label-block {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .settings-row-label {
    color: var(--primary-text-color);
    font-size: 0.95rem;
  }

  .settings-computed {
    color: var(--secondary-text-color);
    font-size: 0.8rem;
  }

  .settings-readout-value {
    color: var(--primary-text-color);
    font-weight: 500;
  }

  .settings-row-control {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .settings-target-control {
    gap: 4px;
  }

  .settings-target-input {
    width: 4rem;
    text-align: right;
    font: inherit;
    color: inherit;
    background: var(--card-background-color);
    border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.2));
    border-radius: 4px;
    padding: 4px 6px;
  }

  .settings-unit,
  .settings-offset-value {
    color: var(--secondary-text-color);
    font-size: 0.9rem;
    min-width: 2.5rem;
    text-align: right;
  }

  .settings-slider-control {
    min-width: 10rem;
  }

  .settings-slider {
    flex: 1;
    min-width: 6rem;
    accent-color: var(--primary-color, #03a9f4);
  }

  .dialog-graph-host {
    min-height: 400px;
  }

  .power-now {
    padding: 8px 16px;
    color: var(--secondary-text-color);
  }

  .config-error {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 16px;
    color: var(--error-color, #b00020);
  }
`;

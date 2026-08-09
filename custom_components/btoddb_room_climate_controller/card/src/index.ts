import "./room-climate-control";
import "./room-climate-control-editor";
import "./profiles-panel";

window.customCards = window.customCards || [];
window.customCards.push({
  type: "room-climate-control",
  name: "Room Climate Control",
  description:
    "Per-room climate dashboard card wired to a room's backend helpers and devices.",
  preview: true,
});

console.info(
  "%c ROOM-CLIMATE-CONTROL %c v1.4.104 ",
  "color: white; background: #0288d1; font-weight: 700;",
  "color: #0288d1; background: white; font-weight: 700;"
);

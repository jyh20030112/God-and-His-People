const canvas = document.getElementById("worldCanvas");
const ctx = canvas.getContext("2d");
const tileTip = document.getElementById("tileTip");
const worldMeta = document.getElementById("worldMeta");
const clockState = document.getElementById("clockState");
const pauseBanner = document.getElementById("pauseBanner");
const playerStats = document.getElementById("playerStats");
const inventoryEl = document.getElementById("inventory");
const knownFactionsEl = document.getElementById("knownFactions");
const eventsEl = document.getElementById("events");
const tradeFaction = document.getElementById("tradeFaction");
const riskLevel = document.getElementById("riskLevel");
const offerResource = document.getElementById("offerResource");
const offerAmount = document.getElementById("offerAmount");
const requestKind = document.getElementById("requestKind");
const requestResource = document.getElementById("requestResource");
const requestAmount = document.getElementById("requestAmount");
const tradeButton = document.getElementById("tradeButton");
const tradeHint = document.getElementById("tradeHint");
const helpKind = document.getElementById("helpKind");
const helpFaction = document.getElementById("helpFaction");
const helpResource = document.getElementById("helpResource");
const helpAmount = document.getElementById("helpAmount");
const helpWeather = document.getElementById("helpWeather");
const helpDuration = document.getElementById("helpDuration");
const helpButton = document.getElementById("helpButton");
const selectedTileText = document.getElementById("selectedTileText");

let state = null;
let requestBusy = false;
let selectedTile = null;
let tileSize = 32;
let offsetX = 0;
let offsetY = 0;
let mapMinX = 0;
let mapMinY = 0;

const terrainColors = {
  plain: "#536a47",
  forest: "#286146",
  hill: "#716745",
  water: "#2b607d",
  mountain: "#696e72",
};

const factionColors = {
  human: "#d6ae58",
  elf: "#77b97a",
  orc: "#c86d5a",
};

const factionNames = {
  human: "人类",
  elf: "精灵",
  orc: "兽人",
};

const resourceNames = {
  food: "食物",
  wood: "木材",
  stone: "石料",
};

const weatherNames = {
  clear: "晴朗",
  rain: "降雨",
  drought: "干旱",
  storm: "风暴",
};

const terrainNames = {
  plain: "平原",
  forest: "森林",
  hill: "丘陵",
  water: "水域",
  mountain: "山地",
};

const directionByKey = {
  w: "north",
  ArrowUp: "north",
  s: "south",
  ArrowDown: "south",
  a: "west",
  ArrowLeft: "west",
  d: "east",
  ArrowRight: "east",
};

async function main() {
  wireControls();
  await refreshState();
  window.setInterval(refreshState, 1200);
}

function wireControls() {
  document.querySelectorAll("[data-direction]").forEach((button) => {
    button.addEventListener("click", () => move(button.dataset.direction));
  });
  window.addEventListener("keydown", (event) => {
    if (event.target && ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
    const direction = directionByKey[event.key];
    if (!direction) return;
    event.preventDefault();
    move(direction);
  });
  canvas.addEventListener("mousemove", onCanvasMove);
  canvas.addEventListener("mouseleave", () => {
    tileTip.hidden = true;
  });
  canvas.addEventListener("click", onCanvasClick);
  window.addEventListener("resize", drawMap);
  tradeButton.addEventListener("click", submitTrade);
  helpButton.addEventListener("click", submitHelp);
}

async function refreshState() {
  if (requestBusy) return;
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(response.statusText);
    state = await response.json();
    hydrateControls();
    render();
    clockState.textContent = "自动时钟运行中";
  } catch (error) {
    clockState.textContent = `连接失败：${error.message}`;
  }
}

async function move(direction) {
  await mutate("/api/player/move", { direction });
}

async function submitTrade() {
  if (!tradeFaction.value) return;
  await mutate("/api/player/trade", {
    faction_id: tradeFaction.value,
    risk_level: riskLevel.value,
    offer: {
      resource: offerResource.value,
      amount: Number(offerAmount.value),
    },
    request: {
      kind: requestKind.value,
      resource: requestResource.value,
      amount: Number(requestAmount.value),
    },
  });
}

async function submitHelp() {
  const target = selectedTile || { x: state.player.x, y: state.player.y };
  const payload = {
    kind: helpKind.value,
    faction_id: helpFaction.value || undefined,
    target: { x: target.x, y: target.y },
    resource: helpResource.value,
    amount: Number(helpAmount.value),
    weather: helpWeather.value,
    duration: Number(helpDuration.value),
  };
  await mutate("/api/player/help", payload);
}

async function mutate(path, body) {
  if (requestBusy) return;
  requestBusy = true;
  setButtonsDisabled(true);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      showPause(`行动失败：${payload.error || response.statusText}`);
      return;
    }
    state = payload;
    hydrateControls();
    render();
  } finally {
    requestBusy = false;
    setButtonsDisabled(false);
  }
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("button").forEach((button) => {
    if (!button.classList.contains("center")) button.disabled = disabled;
  });
}

function hydrateControls() {
  if (!state) return;
  syncOptions(offerResource, state.resources, resourceNames);
  syncOptions(requestResource, state.resources, resourceNames);
  syncOptions(helpResource, state.resources, resourceNames);
  syncOptions(helpWeather, state.weather_types, weatherNames);

  const tradeable = (state.nearby_interactions || []).filter((item) => item.can_trade);
  syncOptions(
    tradeFaction,
    tradeable.map((item) => item.faction_id),
    factionNames,
  );
  syncOptions(
    helpFaction,
    tradeable.map((item) => item.faction_id),
    factionNames,
  );
  const canTrade = tradeable.length > 0;
  tradeButton.disabled = !canTrade || requestBusy;
  helpButton.disabled = requestBusy;
  tradeHint.textContent = canTrade
    ? "交易会消耗携带资源；风险越高，神性回报越高，也更可能失败。"
    : "靠近文明领地后才能交易。";
}

function syncOptions(select, values, labels = {}) {
  const current = select.value;
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels[value] || value;
    select.appendChild(option);
  });
  if (values.includes(current)) select.value = current;
}

function render() {
  if (!state) return;
  worldMeta.textContent = `第 ${state.tick} 刻 · 位置 (${state.player.x}, ${state.player.y}) · 视野 ${state.player.vision_radius}`;
  if (state.paused) {
    showPause(`世界暂停：${state.pause_reason}`);
  } else {
    pauseBanner.hidden = true;
  }
  renderPlayer();
  renderKnownFactions();
  renderEvents();
  renderSelectedTile();
  drawMap();
}

function renderPlayer() {
  const player = state.player;
  playerStats.innerHTML = `
    <div><span>神力</span><strong>${player.divine_power}</strong></div>
    <div><span>神性</span><strong>${player.godhood_progress}/100</strong></div>
    <div><span>发现</span><strong>${player.discovered_tiles_count}</strong></div>
    <div><span>接触</span><strong>${player.contacted_factions.length}</strong></div>
  `;
  inventoryEl.innerHTML = Object.entries(player.inventory)
    .map(([resource, amount]) => `
      <div class="inventory-item">
        <span>${displayResource(resource)}</span>
        <strong>${amount}</strong>
      </div>
    `)
    .join("");
}

function renderKnownFactions() {
  const factions = state.known_factions || [];
  if (!factions.length) {
    knownFactionsEl.innerHTML = `<div class="empty">附近没有可见文明。继续探索。</div>`;
    return;
  }
  knownFactionsEl.innerHTML = factions.map((faction) => `
    <article class="faction-row">
      <div class="row-title">
        <strong>${displayFaction(faction.faction_id)}</strong>
        <span>${faction.contacted ? "已接触" : "可见"}</span>
      </div>
      <div class="metric-line">首领：${escapeHtml(faction.leader_name)}</div>
      <div class="metric-line">可见领土 ${faction.visible_territory_count} · 人口 ${faction.visible_population} · 士兵 ${faction.visible_soldiers}</div>
      <div class="metric-line">近况：${escapeHtml(faction.last_plan_summary || "尚无战略情报")}</div>
    </article>
  `).join("");
}

function renderEvents() {
  const events = state.events || [];
  if (!events.length) {
    eventsEl.innerHTML = `<div class="empty">旅途中尚无事件。</div>`;
    return;
  }
  eventsEl.innerHTML = events.slice().reverse().map((event) => `
    <div class="event-row">
      <strong>第 ${event.tick} 刻</strong>
      <span>${displayEvent(event)}</span>
    </div>
  `).join("");
}

function drawMap() {
  if (!state) return;
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  const bounds = state.visible_bounds || { min_x: 0, max_x: 0, min_y: 0, max_y: 0 };
  mapMinX = bounds.min_x;
  mapMinY = bounds.min_y;
  const cols = Math.max(1, bounds.max_x - bounds.min_x + 1);
  const rows = Math.max(1, bounds.max_y - bounds.min_y + 1);
  tileSize = Math.floor(Math.max(18, Math.min((rect.width - 24) / cols, (rect.height - 24) / rows)));
  offsetX = Math.floor((rect.width - cols * tileSize) / 2);
  offsetY = Math.floor((rect.height - rows * tileSize) / 2);

  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#0e1114";
  ctx.fillRect(0, 0, rect.width, rect.height);

  const tileMap = new Map(state.tiles.map((tile) => [`${tile.x},${tile.y}`, tile]));
  for (let y = bounds.min_y; y <= bounds.max_y; y += 1) {
    for (let x = bounds.min_x; x <= bounds.max_x; x += 1) {
      const tile = tileMap.get(`${x},${y}`);
      drawTile(tile, x, y);
    }
  }
  drawPlayer();
}

function drawTile(tile, worldX, worldY) {
  const x = offsetX + (worldX - mapMinX) * tileSize;
  const y = offsetY + (worldY - mapMinY) * tileSize;
  if (!tile) {
    ctx.fillStyle = "#151b20";
    ctx.fillRect(x, y, tileSize, tileSize);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    ctx.strokeRect(x, y, tileSize, tileSize);
    return;
  }

  ctx.fillStyle = tile.owner
    ? factionColors[tile.owner] || "#a8a8a8"
    : terrainColors[tile.terrain] || "#555";
  ctx.fillRect(x, y, tileSize, tileSize);

  if (tile.weather === "rain") overlayTile(x, y, "rgba(75, 144, 184, 0.28)");
  if (tile.weather === "drought") overlayTile(x, y, "rgba(204, 145, 72, 0.34)");
  if (tile.weather === "storm") overlayTile(x, y, "rgba(31, 34, 42, 0.68)");

  if (tile.protected) {
    ctx.strokeStyle = "#f5df86";
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 3, y + 3, tileSize - 6, tileSize - 6);
  }

  if (tile.home_of) {
    ctx.fillStyle = "#111";
    ctx.beginPath();
    ctx.arc(x + tileSize * 0.25, y + tileSize * 0.25, Math.max(3, tileSize * 0.12), 0, Math.PI * 2);
    ctx.fill();
  }

  if (selectedTile && selectedTile.x === tile.x && selectedTile.y === tile.y) {
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 1, y + 1, tileSize - 2, tileSize - 2);
  }

  ctx.strokeStyle = "rgba(0, 0, 0, 0.24)";
  ctx.lineWidth = 1;
  ctx.strokeRect(x, y, tileSize, tileSize);
}

function overlayTile(x, y, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x, y, tileSize, tileSize);
}

function drawPlayer() {
  const x = offsetX + (state.player.x - mapMinX) * tileSize + tileSize / 2;
  const y = offsetY + (state.player.y - mapMinY) * tileSize + tileSize / 2;
  ctx.save();
  ctx.fillStyle = "#f2f5f6";
  ctx.strokeStyle = "#111417";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(7, tileSize * 0.24), 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#1d75a8";
  ctx.beginPath();
  ctx.arc(x, y, Math.max(3, tileSize * 0.1), 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function onCanvasMove(event) {
  const tile = tileFromEvent(event);
  if (!tile) {
    tileTip.hidden = true;
    return;
  }
  tileTip.innerHTML = tileDetails(tile);
  tileTip.style.left = `${event.clientX + 12}px`;
  tileTip.style.top = `${event.clientY + 12}px`;
  tileTip.hidden = false;
}

function onCanvasClick(event) {
  const tile = tileFromEvent(event);
  if (!tile) return;
  selectedTile = tile;
  renderSelectedTile();
  drawMap();
}

function tileFromEvent(event) {
  const rect = canvas.getBoundingClientRect();
  const localX = event.clientX - rect.left - offsetX;
  const localY = event.clientY - rect.top - offsetY;
  const x = mapMinX + Math.floor(localX / tileSize);
  const y = mapMinY + Math.floor(localY / tileSize);
  if (localX < 0 || localY < 0) return null;
  return (state.tiles || []).find((tile) => tile.x === x && tile.y === y) || null;
}

function renderSelectedTile() {
  const target = selectedTile || state?.player;
  if (!target) return;
  selectedTileText.textContent = selectedTile
    ? `当前选中地块：(${selectedTile.x}, ${selectedTile.y})`
    : `未选中地块；援助将作用于脚下 (${target.x}, ${target.y})。`;
}

function tileDetails(tile) {
  const owner = tile.owner ? displayFaction(tile.owner) : "无主";
  const population = Object.entries(tile.population || {})
    .map(([faction, amount]) => `${displayFaction(faction)} ${amount}`)
    .join("，") || "无";
  const soldiers = Object.entries(tile.soldiers || {})
    .map(([faction, amount]) => `${displayFaction(faction)} ${amount}`)
    .join("，") || "无";
  return `
    <strong>(${tile.x}, ${tile.y})</strong><br>
    地形：${displayTerrain(tile.terrain)}<br>
    归属：${owner}<br>
    天气：${displayWeather(tile.weather)}${tile.weather_duration ? ` · ${tile.weather_duration}刻` : ""}<br>
    房屋：${tile.houses || 0} · 容量：${tile.capacity || 0}<br>
    人口：${population}<br>
    士兵：${soldiers}
  `;
}

function showPause(text) {
  pauseBanner.textContent = text;
  pauseBanner.hidden = false;
}

function displayEvent(event) {
  const message = event.message || "";
  let match = message.match(/^Player discovered (\d+) new tiles near \((\d+), (\d+)\)$/);
  if (match) return `你在 (${match[2]}, ${match[3]}) 附近发现 ${match[1]} 个新地块`;
  match = message.match(/^Player contacted (\w+)$/);
  if (match) return `你接触了${displayFaction(match[1])}`;
  match = message.match(/^Player gave (\d+) (\w+) to (\w+)$/);
  if (match) return `你给予${displayFaction(match[3])} ${match[1]} ${displayResource(match[2])}`;
  match = message.match(/^Player changed weather at \((\d+), (\d+)\) to (\w+) for (\d+) ticks$/);
  if (match) return `你将 (${match[1]}, ${match[2]}) 改为${displayWeather(match[3])}，持续 ${match[4]} 刻`;
  match = message.match(/^Player protected tile \((\d+), (\d+)\)$/);
  if (match) return `你庇护了 (${match[1]}, ${match[2]})`;
  match = message.match(/^Player struck a (\w+) trade with (\w+); godhood \+(\d+)$/);
  if (match) return `你与${displayFaction(match[2])}完成${displayRisk(match[1])}交易，神性 +${match[3]}`;
  match = message.match(/^Player failed a (\w+) trade with (\w+); divine_power -(\d+)$/);
  if (match) return `你与${displayFaction(match[2])}的${displayRisk(match[1])}交易失败，神力 -${match[3]}`;
  match = message.match(/^Tick (\d+) completed$/);
  if (match) return `世界自行流动到第 ${match[1]} 刻`;
  return message;
}

function displayRisk(value) {
  if (value === "low") return "低风险";
  if (value === "medium") return "中风险";
  if (value === "high") return "高风险";
  return value;
}

function displayFaction(value) {
  return factionNames[value] || value;
}

function displayResource(value) {
  return resourceNames[value] || value;
}

function displayWeather(value) {
  return weatherNames[value] || value;
}

function displayTerrain(value) {
  return terrainNames[value] || value;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

main();

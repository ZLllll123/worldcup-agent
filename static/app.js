const state = {
  sessionId: localStorage.getItem("worldCupAgentSession") || null,
  teams: [],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.detail || `请求失败（${response.status}）`);
  return payload;
}

function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function addMessage(role, text, extraClass = "") {
  const container = document.querySelector("#chatMessages");
  const element = document.createElement("div");
  element.className = `message ${role} ${extraClass}`.trim();
  element.textContent = text;
  container.appendChild(element);
  container.scrollTop = container.scrollHeight;
  return element;
}

async function loadHealth() {
  const badge = document.querySelector("#healthBadge");
  try {
    const data = await api("/api/health");
    badge.textContent = `${data.model} · 数据在线`;
    badge.classList.add("online");
    document.querySelector("#snapshotText").textContent = `数据快照 ${data.snapshot_id}`;
  } catch (error) {
    badge.textContent = "数据连接失败";
  }
}

async function loadProbabilities() {
  const data = await api("/api/probabilities?limit=8");
  const winner = data.teams[0];
  document.querySelector("#championName").textContent = winner.team;
  document.querySelector("#championProbability").textContent = formatPercent(winner.champion_probability);
  const list = document.querySelector("#probabilityList");
  list.classList.remove("loading-card");
  list.textContent = "";
  const max = Number(winner.champion_probability);
  data.teams.forEach((team, index) => {
    const row = document.createElement("div");
    row.className = "probability-row";
    const width = Math.max(2, Number(team.champion_probability) / max * 100);
    row.innerHTML = `
      <span class="probability-rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="probability-team"></span>
      <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
      <span class="probability-value">${formatPercent(team.champion_probability)}</span>`;
    row.querySelector(".probability-team").textContent = team.team;
    list.appendChild(row);
  });
}

function stageForMatch(number) {
  if (number <= 100) return ["八强", "quarter"];
  if (number <= 102) return ["半决赛", "semi"];
  if (number === 103) return ["季军赛", "third"];
  return ["决赛", "final"];
}

async function loadBracket() {
  const data = await api("/api/bracket");
  const grid = document.querySelector("#bracketGrid");
  grid.textContent = "";
  const groups = new Map();
  data.matches.forEach(match => {
    const [label, key] = stageForMatch(Number(match.match_number));
    if (!groups.has(key)) groups.set(key, { label, matches: [] });
    groups.get(key).matches.push(match);
  });
  groups.forEach(group => {
    const column = document.createElement("div");
    column.className = "bracket-column";
    const heading = document.createElement("h3");
    heading.textContent = group.label;
    column.appendChild(heading);
    group.matches.forEach(match => {
      const card = document.createElement("article");
      card.className = "match-card";
      const likelyScore = match.top_scores?.[0]?.score || "—";
      card.innerHTML = `<div class="match-meta">MATCH ${match.match_number}</div>`;
      [match.team_a, match.team_b].forEach(team => {
        const line = document.createElement("div");
        line.className = `match-team ${team === match.predicted_winner ? "winner" : ""}`;
        const name = document.createElement("span");
        name.textContent = team;
        const marker = document.createElement("span");
        marker.textContent = team === match.predicted_winner ? "晋级" : "";
        line.append(name, marker);
        card.appendChild(line);
      });
      const score = document.createElement("div");
      score.className = "match-score";
      score.textContent = `最可能比分 ${likelyScore}`;
      card.appendChild(score);
      column.appendChild(card);
    });
    grid.appendChild(column);
  });
}

async function loadTeams() {
  const data = await api("/api/teams");
  state.teams = data.teams;
  ["#teamA", "#teamB"].forEach((selector, selectIndex) => {
    const select = document.querySelector(selector);
    select.textContent = "";
    state.teams.forEach((team, index) => {
      const option = document.createElement("option");
      option.value = team;
      option.textContent = team;
      if (index === selectIndex) option.selected = true;
      select.appendChild(option);
    });
  });
  document.querySelector("#teamA").value = state.teams.includes("France") ? "France" : state.teams[0];
  document.querySelector("#teamB").value = state.teams.includes("Morocco") ? "Morocco" : state.teams[1];
}

document.querySelector("#predictForm").addEventListener("submit", async event => {
  event.preventDefault();
  const result = document.querySelector("#predictResult");
  result.hidden = false;
  result.textContent = "模型计算中…";
  try {
    const data = await api("/api/predict-match", {
      method: "POST",
      body: JSON.stringify({ team_a: document.querySelector("#teamA").value, team_b: document.querySelector("#teamB").value }),
    });
    const a = data.team_a;
    const b = data.team_b;
    result.innerHTML = `<h3></h3><p>最可能比分 <strong>${data.most_likely_score}</strong> · 预测晋级 ${data.predicted_winner}</p><div class="prediction-stats"><span><strong>${formatPercent(data.probabilities_90_minutes[`${a}_win`])}</strong>${a} 90分钟胜</span><span><strong>${formatPercent(data.probabilities_90_minutes.draw)}</strong>平局</span><span><strong>${formatPercent(data.probabilities_90_minutes[`${b}_win`])}</strong>${b} 90分钟胜</span></div>`;
    result.querySelector("h3").textContent = `${a} vs ${b}`;
  } catch (error) {
    result.textContent = error.message;
  }
});

async function sendChat(message) {
  addMessage("user", message);
  const thinking = addMessage("assistant", "Agent 正在选择工具并分析…", "thinking");
  const button = document.querySelector("#sendButton");
  button.disabled = true;
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: state.sessionId }),
    });
    state.sessionId = data.session_id;
    localStorage.setItem("worldCupAgentSession", state.sessionId);
    thinking.remove();
    addMessage("assistant", data.answer);
  } catch (error) {
    thinking.textContent = `请求失败：${error.message}`;
    thinking.classList.remove("thinking");
  } finally {
    button.disabled = false;
  }
}

document.querySelector("#chatForm").addEventListener("submit", async event => {
  event.preventDefault();
  const input = document.querySelector("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendChat(message);
});

document.querySelectorAll("[data-question]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelector("#chat").scrollIntoView({ behavior: "smooth" });
    sendChat(button.dataset.question);
  });
});

Promise.allSettled([loadHealth(), loadProbabilities(), loadBracket(), loadTeams()]);

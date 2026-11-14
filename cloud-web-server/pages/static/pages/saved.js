const SAVED_COOKIE = "saved_playlists";
const SAVED_LIMIT = 5;
const SAVED_MAX_AGE = 60 * 60 * 24 * 365;

function readSaved() {
  const prefix = `${SAVED_COOKIE}=`;
  const entry = document.cookie.split("; ").find((row) => row.startsWith(prefix));
  if (!entry) return [];
  try {
    const parsed = JSON.parse(decodeURIComponent(entry.slice(prefix.length)));
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSavedItem).slice(0, SAVED_LIMIT);
  } catch (error) {
    return [];
  }
}

function writeSaved(items) {
  const value = encodeURIComponent(JSON.stringify(items.slice(0, SAVED_LIMIT)));
  document.cookie = `${SAVED_COOKIE}=${value}; Path=/; Max-Age=${SAVED_MAX_AGE}; SameSite=Lax`;
}

function isSavedItem(item) {
  return Boolean(
    item &&
      (item.service === "youtube" || item.service === "spotify") &&
      item.id &&
      item.name &&
      item.url
  );
}

function savedIndex(items, service, id) {
  return items.findIndex((item) => item.service === service && item.id === id);
}

function storedUrl(service, id, url) {
  if (url && url.length <= 500) return url;
  if (service === "spotify") return `https://open.spotify.com/playlist/${id}`;
  return `https://www.youtube.com/playlist?list=${id}`;
}

function openPath(item) {
  try {
    const parsed = new URL(item.url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
  } catch (error) {
    return "";
  }
  const path = item.service === "spotify" ? "/liftoff2/" : "/liftoff/";
  return `${path}?url=${encodeURIComponent(item.url)}`;
}

function initSaveButton() {
  const button = document.querySelector(".save-playlist");
  if (!button) return;
  const service = button.dataset.service;
  const id = button.dataset.playlistId;

  function paint() {
    const items = readSaved();
    const saved = savedIndex(items, service, id) >= 0;
    const blocked = !saved && items.length >= SAVED_LIMIT;
    button.classList.toggle("is-saved", saved);
    button.setAttribute("aria-pressed", saved ? "true" : "false");
    button.disabled = blocked;
    button.setAttribute(
      "aria-label",
      saved
        ? "Playlist saved"
        : blocked
          ? "Saved playlist limit of 5 reached"
          : "Save this playlist"
    );
  }

  button.addEventListener("click", () => {
    const items = readSaved();
    if (savedIndex(items, service, id) >= 0 || items.length >= SAVED_LIMIT) {
      paint();
      return;
    }
    const name = (button.dataset.playlistName || id).trim().slice(0, 120);
    items.unshift({
      service,
      id,
      name,
      url: storedUrl(service, id, button.dataset.playlistUrl || ""),
    });
    writeSaved(items);
    paint();
  });

  paint();
}

function initSavedList() {
  const list = document.querySelector(".saved-list");
  if (!list) return;

  function render() {
    list.replaceChildren();
    readSaved().forEach((item) => {
      const href = openPath(item);
      if (!href) return;
      const row = document.createElement("li");
      row.className = "saved-row";
      row.dataset.service = item.service;
      row.dataset.playlistId = item.id;
      const template = document.getElementById(
        item.service === "spotify" ? "saved-logo-spotify" : "saved-logo-youtube"
      );
      if (template) row.append(template.content.cloneNode(true));
      const link = document.createElement("a");
      link.className = "saved-open";
      link.href = href;
      link.textContent = item.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "saved-remove";
      remove.textContent = "X";
      remove.setAttribute("aria-label", `Remove ${item.name}`);
      row.append(link, remove);
      list.append(row);
    });
  }

  list.addEventListener("click", (event) => {
    const remove = event.target.closest(".saved-remove");
    if (!remove) return;
    const row = remove.closest(".saved-row");
    if (!row) return;
    writeSaved(
      readSaved().filter(
        (item) => item.service !== row.dataset.service || item.id !== row.dataset.playlistId
      )
    );
    render();
  });

  render();
}

initSaveButton();
initSavedList();

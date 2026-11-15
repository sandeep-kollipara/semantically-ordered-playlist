const vectorActions = document.querySelector(".vector-actions");
const vectorizeButton = document.querySelector(".vectorize");
const vibesortButton = document.querySelector(".vibesort");
const vectorStatus = document.querySelector(".vector-status");
const vectorProgress = document.querySelector(".vector-progress");

if (vectorActions && vectorizeButton && vibesortButton) {
  const playlistId = vectorActions.dataset.playlistId || "";
  const playlistUrl = vectorActions.dataset.playlistUrl || "";
  let adjacency = null;
  const dbName = "semantically-ordered-playlist";
  const storeName = "adjacency";

  function openDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(dbName, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(storeName)) {
          request.result.createObjectStore(storeName);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function storeRequest(mode, runner) {
    return openDb().then(
      (db) =>
        new Promise((resolve, reject) => {
          const transaction = db.transaction(storeName, mode);
          const request = runner(transaction.objectStore(storeName));
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        })
    );
  }

  function readMatrix() {
    if (!playlistId) return Promise.resolve(null);
    return storeRequest("readonly", (store) => store.get(playlistId));
  }

  function writeMatrix(matrix) {
    return storeRequest("readwrite", (store) => store.put(matrix, playlistId));
  }

  function hasMatrix() {
    return Boolean(adjacency && adjacency.distance);
  }

  function setVibesortActive(active) {
    vibesortButton.disabled = !active;
    vibesortButton.classList.toggle("is-rainbow", active);
  }

  function setVectorizeReady() {
    vectorizeButton.classList.remove("is-waiting");
    vectorizeButton.classList.add("is-rainbow");
    vectorizeButton.disabled = false;
    vectorizeButton.setAttribute("aria-busy", "false");
  }

  function setReady() {
    setVectorizeReady();
    setVibesortActive(true);
  }

  function progressText(progress) {
    const downloaded = progress.downloaded || 0;
    const downloadTotal = progress.download_total || 0;
    const encoded = progress.encoded || 0;
    const encodeTotal = progress.encode_total || 0;
    const downloads = downloadTotal
      ? `Downloaded ${downloaded} of ${downloadTotal}`
      : `Downloaded ${downloaded}`;
    const encoding = encodeTotal
      ? `Encoding ${encoded} of ${encodeTotal}`
      : `Encoding ${encoded}`;
    return `${downloads}. ${encoding}.`;
  }

  function showProgress(text, isError) {
    vectorProgress.hidden = false;
    vectorProgress.textContent = text;
    vectorProgress.classList.toggle("is-error", Boolean(isError));
  }

  function setWaiting() {
    vectorStatus.hidden = true;
    showProgress("Downloaded 0. Encoding 0.");
    vectorizeButton.classList.remove("is-rainbow");
    vectorizeButton.classList.add("is-waiting");
    vectorizeButton.disabled = true;
    vectorizeButton.setAttribute("aria-busy", "true");
  }

  function setDefault(message) {
    setVectorizeReady();
    if (!hasMatrix()) setVibesortActive(false);
    vectorStatus.hidden = true;
    showProgress(message || "Vectorize failed.", true);
  }

  function sleep(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });
  }

  async function poll(jobId) {
    for (;;) {
      const response = await fetch(`/vectorize/${jobId}/`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Vectorize failed.");
      if (body.progress) showProgress(progressText(body.progress));
      if (body.state === "done") return body.matrix;
      if (body.state === "error") throw new Error(body.error || "Vectorize failed.");
      await sleep(500);
    }
  }

  vectorizeButton.addEventListener("click", async () => {
    if (vectorizeButton.disabled) return;
    setWaiting();
    try {
      const response = await fetch("/vectorize/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: playlistUrl }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Vectorize failed.");
      const matrix = await poll(body.job);
      adjacency = matrix;
      await writeMatrix(matrix);
      setReady();
    } catch (error) {
      setDefault(error.message || "Vectorize failed.");
    }
  });

  function greedyPath(matrix, startIndex) {
    const indexes = matrix.indexes || [];
    const distance = matrix.distance || [];
    const position = new Map();
    indexes.forEach((videoIndex, slot) => position.set(Number(videoIndex), slot));
    if (!position.has(startIndex)) return null;
    const count = indexes.length;
    const visited = new Array(count).fill(false);
    const order = [];
    let current = position.get(startIndex);
    for (let step = 0; step < count; step += 1) {
      visited[current] = true;
      order.push(Number(indexes[current]));
      if (step === count - 1) break;
      const row = distance[current] || [];
      let next = -1;
      let best = Infinity;
      for (let slot = 0; slot < count; slot += 1) {
        if (visited[slot]) continue;
        const value = Number(row[slot]);
        if (value < best) {
          best = value;
          next = slot;
        }
      }
      if (next < 0) break;
      current = next;
    }
    return order;
  }

  vibesortButton.addEventListener("click", (event) => {
    event.preventDefault();
    if (vibesortButton.disabled || !adjacency) return;
    const playing = document.querySelector(".song.is-playing .song-name");
    const startIndex = Number(playing && playing.dataset.index);
    const order = Number.isNaN(startIndex) ? null : greedyPath(adjacency, startIndex);
    if (!order) {
      showProgress("This song was not vectorized.", true);
      return;
    }
    if (vectorProgress.classList.contains("is-error")) {
      vectorProgress.hidden = true;
      vectorProgress.textContent = "";
      vectorProgress.classList.remove("is-error");
    }
    if (typeof window.reorderPlaylist === "function") window.reorderPlaylist(order);
  });

  readMatrix()
    .then((matrix) => {
      if (matrix && matrix.distance) {
        adjacency = matrix;
        setReady();
      }
    })
    .catch(() => {});
}

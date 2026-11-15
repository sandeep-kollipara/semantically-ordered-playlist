  const playlist = document.querySelector(".playlist");
  if (playlist) {
    const pageSize = Number(playlist.dataset.pageSize) || 7;
    let page = Number(playlist.dataset.page) || 1;

    function renderPage() {
      const items = [...document.querySelectorAll(".song")];
      const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
      page = Math.min(Math.max(page, 1), pageCount);
      const start = (page - 1) * pageSize;
      items.forEach((song, index) => {
        song.classList.toggle("is-offpage", index < start || index >= start + pageSize);
      });
      document.querySelectorAll(".pager-button").forEach((button) => {
        const enabled = Number(button.dataset.pageStep) < 0 ? page > 1 : page < pageCount;
        button.disabled = !enabled;
        button.classList.toggle("is-disabled", !enabled);
      });
      renderPageNumbers(page, pageCount);
      const playing = document.querySelector(".song.is-playing:not(.is-offpage)");
      if (playing) tuneSlide(playing);
      const url = new URL(window.location.href);
      if (page > 1) url.searchParams.set("page", String(page));
      else url.searchParams.delete("page");
      if (url.href !== window.location.href) history.replaceState(null, "", url);
    }

    window.showPlaylistSong = function (song) {
      const items = [...document.querySelectorAll(".song")];
      const index = items.indexOf(song);
      if (index < 0) return;
      const nextPage = Math.floor(index / pageSize) + 1;
      if (nextPage !== page) {
        page = nextPage;
        renderPage();
      }
      song.scrollIntoView({ block: "nearest" });
    }

    function renderPageNumbers(current, pageCount) {
      const numbers = [];
      [1, current - 1, current, current + 1, pageCount].forEach((number) => {
        if (number >= 1 && number <= pageCount && !numbers.includes(number)) numbers.push(number);
      });
      numbers.sort((a, b) => a - b);
      document.querySelectorAll(".pager-pages").forEach((group) => {
        const track = document.createElement("div");
        track.className = "pager-pages-track";
        numbers.forEach((number, index) => {
          if (index > 0 && number - numbers[index - 1] > 1) {
            const mark = document.createElement("span");
            mark.className = "pager-ellipsis";
            mark.textContent = "...";
            mark.setAttribute("aria-hidden", "true");
            track.append(mark);
          }
          const pageButton = document.createElement("button");
          pageButton.type = "button";
          pageButton.className = "pager-page";
          pageButton.dataset.pageNumber = String(number);
          pageButton.textContent = String(number);
          pageButton.setAttribute("aria-label", `Page ${number}`);
          if (number === current) {
            pageButton.classList.add("is-current");
            pageButton.setAttribute("aria-current", "page");
          }
          track.append(pageButton);
        });
        group.replaceChildren(track);
      });
      centerPageNumbers();
    }

    function centerPageNumbers() {
      document.querySelectorAll(".pager-pages").forEach((group) => {
        const track = group.querySelector(".pager-pages-track");
        const currentButton = track && track.querySelector(".is-current");
        if (!currentButton) return;
        const available = group.clientWidth;
        const naturalWidth = track.scrollWidth;
        track.style.transformOrigin = "left center";
        if (naturalWidth > available && available > 0) {
          track.style.transform = `scale(${available / naturalWidth})`;
          return;
        }
        const currentCenter = currentButton.offsetLeft + currentButton.offsetWidth / 2;
        track.style.transform = `translateX(${available / 2 - currentCenter}px)`;
      });
    }

    playlist.addEventListener("click", (event) => {
      const pageButton = event.target.closest(".pager-page");
      if (pageButton) {
        const nextPage = Number(pageButton.dataset.pageNumber);
        if (nextPage && nextPage !== page) {
          page = nextPage;
          renderPage();
        }
        return;
      }
      const button = event.target.closest(".pager-button");
      if (!button || button.disabled) return;
      page += Number(button.dataset.pageStep);
      renderPage();
    });

    const searchForm = playlist.querySelector(".song-search");
    const searchInput = playlist.querySelector(".search-input");
    const searchResults = playlist.querySelector(".search-results");
    if (searchForm && searchInput && searchResults && document.querySelector(".player")) {
      let options = [];
      let activeIndex = -1;

      function songRank(title, query) {
        if (title.startsWith(query)) return 0;
        if (title.split(/\s+/).some((word) => word.startsWith(query))) return 1;
        return 2;
      }

      function matchingSongs(query) {
        const needle = query.trim().toLowerCase();
        if (!needle) return [];
        return [...playlist.querySelectorAll(".song")]
          .map((song, index) => {
            const link = song.querySelector(".song-name");
            const title = songTitle(link);
            return { song, link, title, index, rank: songRank(title.toLowerCase(), needle) };
          })
          .filter((item) => item.title.toLowerCase().includes(needle))
          .sort((a, b) => a.rank - b.rank || a.index - b.index)
          .slice(0, 3);
      }

      function hideResults() {
        options = [];
        activeIndex = -1;
        searchResults.hidden = true;
        searchResults.innerHTML = "";
        searchInput.setAttribute("aria-expanded", "false");
        searchInput.removeAttribute("aria-activedescendant");
      }

      function showResults() {
        options = matchingSongs(searchInput.value);
        searchResults.innerHTML = "";
        if (!options.length) {
          hideResults();
          return;
        }
        options.forEach((item, index) => {
          const option = document.createElement("li");
          option.id = `song-search-option-${index}`;
          option.className = "search-option";
          option.setAttribute("role", "option");
          const image = document.createElement("img");
          image.className = "search-thumb";
          image.alt = "";
          image.width = 72;
          image.height = 72;
          const thumb = item.song.querySelector(".thumb");
          if (thumb) image.src = thumb.src;
          const label = document.createElement("span");
          label.className = "search-option-title";
          label.textContent = item.title;
          option.append(image, label);
          searchResults.append(option);
        });
        activeIndex = -1;
        searchResults.hidden = false;
        searchInput.setAttribute("aria-expanded", "true");
      }

      function setActive(index) {
        activeIndex = index;
        [...searchResults.children].forEach((option, optionIndex) => {
          option.classList.toggle("is-active", optionIndex === index);
          if (optionIndex === index) searchInput.setAttribute("aria-activedescendant", option.id);
        });
      }

      function choose(index) {
        const item = options[index];
        if (!item) return;
        playSong(item.link);
        page = Math.floor(item.index / pageSize) + 1;
        renderPage();
        hideResults();
      }

      searchForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!options.length) showResults();
        choose(activeIndex >= 0 ? activeIndex : 0);
      });
      searchInput.addEventListener("input", showResults);
      searchInput.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          if (searchResults.hidden) showResults();
          if (options.length) setActive((activeIndex + 1) % options.length);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          if (options.length) setActive((activeIndex - 1 + options.length) % options.length);
        } else if (event.key === "Escape") {
          hideResults();
        }
      });
      searchResults.addEventListener("mousedown", (event) => {
        if (event.target.closest(".search-option")) event.preventDefault();
      });
      searchResults.addEventListener("click", (event) => {
        const option = event.target.closest(".search-option");
        if (!option) return;
        choose([...searchResults.children].indexOf(option));
      });
      document.addEventListener("click", (event) => {
        if (!searchForm.contains(event.target)) hideResults();
      });
    }

    window.reorderPlaylist = function (leadingIndexes) {
      const list = playlist.querySelector(".song-list");
      if (!list) return;
      const songs = [...list.querySelectorAll(".song")];
      const byIndex = new Map();
      songs.forEach((song) => {
        const link = song.querySelector(".song-name");
        const index = Number(link && link.dataset.index);
        if (!Number.isNaN(index)) byIndex.set(index, song);
      });
      const used = new Set();
      const ordered = [];
      leadingIndexes.forEach((index) => {
        const song = byIndex.get(Number(index));
        if (!song || used.has(song)) return;
        used.add(song);
        ordered.push(song);
      });
      songs
        .filter((song) => !used.has(song))
        .sort((a, b) => songIndex(a) - songIndex(b))
        .forEach((song) => ordered.push(song));
      ordered.forEach((song) => list.appendChild(song));
      rewriteQueues(ordered);
      page = 1;
      renderPage();
    };

    function songIndex(song) {
      const link = song.querySelector(".song-name");
      return Number(link && link.dataset.index);
    }

    function rewriteQueues(songs) {
      const ids = songs.map((song) => song.dataset.videoId);
      songs.forEach((song, index) => {
        const link = song.querySelector(".song-name");
        if (!link) return;
        let url;
        try {
          url = new URL(link.href);
        } catch (error) {
          return;
        }
        if (!url.searchParams.has("playlist")) return;
        url.searchParams.set("playlist", ids.slice(index, index + 200).join(","));
        link.href = url.toString();
      });
    }

    renderPage();
    if (window.ResizeObserver) {
      new ResizeObserver(() => centerPageNumbers()).observe(playlist);
    }
  }

  const leaveLink = document.querySelector(".leave-liftoff");
  if (leaveLink) {
    leaveLink.addEventListener("click", () => {
      fetch(leaveLink.dataset.leave, { method: "POST", cache: "no-store", keepalive: true });
      window.location.replace(leaveLink.dataset.landing || "/");
    });
  }

  const playerToggle = document.querySelector(".player-toggle");
  const liftoffBody = document.querySelector(".liftoff-body");
  const mobilePlayerQuery = window.matchMedia("(max-width: 800px)");
  if (playerToggle && liftoffBody) {
    const icon = playerToggle.querySelector(".player-toggle-icon");
    const syncPlayerToggle = () => {
      const open = liftoffBody.classList.contains("is-player-open");
      playerToggle.setAttribute("aria-expanded", open ? "true" : "false");
      playerToggle.setAttribute(
        "aria-label",
        open
          ? (playerToggle.dataset.hideLabel || "Hide the video player")
          : (playerToggle.dataset.showLabel || "Show the video player")
      );
      if (icon) {
        icon.textContent = mobilePlayerQuery.matches ? (open ? "↑" : "↓") : (open ? "←" : "→");
      }
    };
    playerToggle.addEventListener("click", (event) => {
      event.preventDefault();
      const open = liftoffBody.classList.contains("is-player-open");
      if (mobilePlayerQuery.matches && playerToggle.tagName === "A") {
        const playerFrame = document.getElementById("yt-player");
        if (open) {
          liftoffBody.classList.remove("is-player-open");
        } else if (typeof startMobileVideo === "function" && playerFrame && !playerFrame.getAttribute("src")) {
          startMobileVideo(playerFrame.dataset.pendingSrc || playerToggle.href);
        } else if (typeof revealPlayer === "function") {
          revealPlayer();
        } else {
          liftoffBody.classList.add("is-player-open");
        }
        syncPlayerToggle();
        return;
      }
      liftoffBody.classList.toggle("is-player-open");
      syncPlayerToggle();
    });
    mobilePlayerQuery.addEventListener("change", syncPlayerToggle);
    syncPlayerToggle();
  }

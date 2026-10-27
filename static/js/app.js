(function () {
  "use strict";

  // Filters: apply as soon as a dropdown or date changes.
  document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
    form.querySelectorAll("select, input[type=date]").forEach(function (el) {
      el.addEventListener("change", function () { form.submit(); });
    });
  });

  // Prevent double submits (double charges, duplicate listings).
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (btn) setTimeout(function () { btn.disabled = true; }, 0);
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Installable app: offline support plus a one-time "add to home screen" prompt.
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }
  (function () {
    var bar = document.querySelector("[data-install]");
    if (!bar) return;
    var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    var seen = false;
    try { seen = localStorage.getItem("ns-install-dismissed") === "1"; } catch (e) {}
    if (standalone || seen || window.innerWidth > 900) return;
    var deferred = null;
    function dismiss() {
      bar.hidden = true;
      try { localStorage.setItem("ns-install-dismissed", "1"); } catch (e) {}
    }
    window.addEventListener("beforeinstallprompt", function (e) {  // Android / Chrome
      e.preventDefault(); deferred = e; bar.hidden = false;
    });
    var ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
    if (ios) {  // iOS has no install prompt: explain the Share menu instead
      var hint = bar.querySelector("[data-install-hint]");
      hint.textContent = hint.dataset.ios;
      bar.querySelector("[data-install-go]").hidden = true;
      setTimeout(function () { bar.hidden = false; }, 4000);
    }
    bar.querySelector("[data-install-go]").addEventListener("click", function () {
      if (!deferred) return;
      deferred.prompt();
      deferred.userChoice.finally(dismiss);
    });
    bar.querySelector("[data-install-close]").addEventListener("click", dismiss);
  })();

  // Push notifications on/off for this device.
  (function () {
    var buttons = document.querySelectorAll("[data-push]");
    if (!buttons.length) return;
    var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || document.body.dataset.csrf || "";
    var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    var ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;

    function label(btn, state) {
      btn.hidden = false;
      btn.classList.toggle("is-on", state === "on");
      btn.textContent = btn.dataset[state] || btn.dataset.off;
      btn.disabled = state === "busy" || state === "denied";
    }
    function urlB64ToUint8Array(b64) {
      var pad = "=".repeat((4 - (b64.length % 4)) % 4), raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
      return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
    }
    function post(url, body) {
      return fetch(url, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body) });
    }

    if (!supported) {
      // iPhone only allows notifications for apps added to the home screen.
      if (ios && !standalone) buttons.forEach(function (b) { label(b, "ios"); b.disabled = true; });
      return;
    }
    navigator.serviceWorker.ready.then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        buttons.forEach(function (btn) {
          label(btn, Notification.permission === "denied" ? "denied" : (sub ? "on" : "off"));
          btn.addEventListener("click", function () {
            label(btn, "busy");
            reg.pushManager.getSubscription().then(function (current) {
              if (current) {  // turn off
                return post("/api/push/unsubscribe", { endpoint: current.endpoint })
                  .then(function () { return current.unsubscribe(); })
                  .then(function () { buttons.forEach(function (b) { label(b, "off"); }); });
              }
              return Notification.requestPermission().then(function (perm) {
                if (perm !== "granted") { buttons.forEach(function (b) { label(b, perm === "denied" ? "denied" : "off"); }); return; }
                return fetch("/api/push/key").then(function (r) { return r.json(); }).then(function (k) {
                  return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(k.key) });
                }).then(function (newSub) {
                  return post("/api/push/subscribe", newSub.toJSON());
                }).then(function (r) {
                  buttons.forEach(function (b) { label(b, r && r.ok ? "on" : "off"); });
                });
              });
            }).catch(function () { buttons.forEach(function (b) { label(b, "off"); }); });
          });
        });
      });
    });
  })();

  // Live chat: send without reloading, and pick up new messages every few seconds.
  (function () {
    var chat = document.querySelector("[data-chat]");
    if (!chat) return;
    var form = document.querySelector("[data-chat-form]");
    var warning = document.querySelector("[data-chat-warning]");
    var csrf = document.body.dataset.csrf || "";
    var busy = false;
    function append(html) {
      if (!html) return;
      var empty = chat.querySelector("[data-empty]");
      if (empty) empty.remove();
      chat.insertAdjacentHTML("beforeend", html);
      var end = document.getElementById("end");
      if (end) end.scrollIntoView({ block: "end", behavior: "smooth" });
    }
    function poll() {
      if (busy || document.hidden) return;
      busy = true;
      fetch(chat.dataset.updates + "?after=" + chat.dataset.last, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.last > +chat.dataset.last) { chat.dataset.last = d.last; append(d.html); } })
        .catch(function () {})
        .then(function () { busy = false; });
    }
    setInterval(poll, 4000);
    document.addEventListener("visibilitychange", poll);
    if (!form) return;
    // Tapping Send would take focus from the message box, so the iPhone keyboard closes and reopens
    // after every message and the page jumps. Keep focus in the box instead.
    form.querySelector("button[type=submit]").addEventListener("mousedown", function (e) { e.preventDefault(); });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var box = form.querySelector("textarea");
      var text = box.value.trim();
      if (!text) return;
      var data = new FormData(form);
      data.set("after", chat.dataset.last);
      var button = form.querySelector("button[type=submit]");
      button.disabled = true;
      busy = true;
      fetch(form.action, { method: "POST", body: data, credentials: "same-origin",
                           headers: { "X-Requested-With": "fetch", "X-CSRF-Token": csrf } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          warning.hidden = !(d.warning || d.error);
          warning.textContent = d.warning || d.error || "";
          if (d.error) return;
          box.value = ""; box.style.height = "";
          chat.dataset.last = d.last;
          append(d.html);
          if (navigator.vibrate) navigator.vibrate(8);
        })
        .catch(function () { form.submit(); })
        .then(function () { button.disabled = false; busy = false; box.focus(); });
    });
  })();

  // Chat: grow the message box as you type, send with Enter on a laptop, start at the newest message.
  document.querySelectorAll("textarea[data-autogrow]").forEach(function (ta) {
    function grow() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; }
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(hover: hover)").matches) {
        e.preventDefault();
        if (ta.value.trim()) ta.form.requestSubmit ? ta.form.requestSubmit() : ta.form.submit();
      }
    });
  });
  if (document.body.classList.contains("is-chat")) {
    var end = document.getElementById("end");
    if (end) end.scrollIntoView({ block: "end" });
  }

  // City picker: switch straight away.
  document.querySelectorAll("select[data-nav]").forEach(function (sel) {
    sel.addEventListener("change", function () { window.location = sel.value; });
  });

  // Countdown to the start time ("Starts in 2h 10m").
  var countdowns = document.querySelectorAll("[data-countdown]");
  function tickCountdowns() {
    countdowns.forEach(function (el) {
      var diff = new Date(el.dataset.countdown) - new Date();
      if (diff <= 0) { el.textContent = el.dataset.started; return; }
      var mins = Math.floor(diff / 60000), h = Math.floor(mins / 60), m = mins % 60;
      var text = h >= 24 ? Math.floor(h / 24) + "d " + (h % 24) + "h" : (h ? h + "h " : "") + m + "m";
      el.textContent = el.dataset.label.replace("{t}", text);
    });
  }
  if (countdowns.length) { tickCountdowns(); setInterval(tickCountdowns, 30000); }

  // A short buzz on Android when buying, making or accepting an offer.
  document.addEventListener("submit", function (e) {
    var action = e.target.getAttribute("action") || "";
    if (navigator.vibrate && /\/(buy|offer|accept)/.test(action)) navigator.vibrate(12);
  });

  // Swipe left/right on the ticket list to move between days.
  (function () {
    var area = document.querySelector("[data-swipe-days]");
    var tabs = Array.prototype.slice.call(document.querySelectorAll(".day-tab"));
    if (!area || tabs.length < 2) return;
    var x0 = null, y0 = null;
    area.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
    area.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
      x0 = null;
      if (Math.abs(dx) < 70 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
      var i = tabs.findIndex(function (t) { return t.classList.contains("is-on"); });
      var next = tabs[i + (dx < 0 ? 1 : -1)];
      if (next) { startLoading(); window.location = next.href; }
    });
  })();

  document.querySelectorAll("[data-show-more]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-more]").forEach(function (li) { li.hidden = false; });
      btn.remove();
    });
  });

  // Close the language menu when tapping elsewhere.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.lang-menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
  });

  // Day tabs: keep the selected day visible when the strip scrolls sideways.
  var onTab = document.querySelector(".day-tab.is-on");
  if (onTab && onTab.scrollIntoView) onTab.scrollIntoView({ block: "nearest", inline: "center" });

  // Loading feedback: a thin bar at the top while the next page loads (shows on slow mobile data).
  var loadbar = document.querySelector(".loadbar");
  function startLoading() {
    if (!loadbar) return;
    loadbar.classList.remove("done");
    void loadbar.offsetWidth;
    loadbar.classList.add("go");
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank" || a.hasAttribute("download")) return;
    var url = new URL(a.href, location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search && url.hash)) return;
    startLoading();
  });
  document.addEventListener("submit", function (e) { if (!e.defaultPrevented) startLoading(); });
  window.addEventListener("pageshow", function () { if (loadbar) { loadbar.classList.remove("go"); loadbar.classList.add("done"); } });

  // Pull to refresh in the installed app (iPhone home-screen apps have no refresh button).
  (function () {
    var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    var ptr = document.querySelector(".ptr");
    if (!standalone || !ptr || document.body.classList.contains("is-chat")) return;
    var startY = null, pull = 0, LIMIT = 80;
    window.addEventListener("touchstart", function (e) {
      startY = window.scrollY <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
    }, { passive: true });
    window.addEventListener("touchmove", function (e) {
      if (startY === null) return;
      pull = Math.max(0, Math.min(120, (e.touches[0].clientY - startY) * 0.5));
      ptr.style.transform = "translate(-50%, " + (pull - 50) + "px) rotate(" + pull * 3 + "deg)";
      ptr.classList.toggle("pulling", pull > 6);
      ptr.classList.toggle("ready", pull >= LIMIT);
    }, { passive: true });
    window.addEventListener("touchend", function () {
      if (startY === null) return;
      if (pull >= LIMIT) {
        ptr.style.transform = "";
        ptr.classList.add("spinning");
        startLoading();
        setTimeout(function () { location.reload(); }, 650);  // let the spinner turn a moment, like a native app
      } else {
        ptr.style.transform = "";
        ptr.classList.remove("pulling", "ready");
      }
      startY = null; pull = 0;
    });
  })();

  // Live search suggestions under the search bar.
  document.querySelectorAll("input[data-suggest]").forEach(function (input) {
    var box = document.getElementById(input.getAttribute("aria-controls"));
    var timer = null, items = [], active = -1, last = "";

    function esc(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function close() {
      box.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1;
    }
    function highlight(i) {
      items.forEach(function (el, j) { el.classList.toggle("is-active", j === i); el.setAttribute("aria-selected", j === i); });
      active = i;
    }
    function render(data, q) {
      var html = "";
      data.events.forEach(function (e) {
        html += '<a class="sg-item" role="option" href="' + esc(e.url) + '">' +
          '<span class="sg-date" style="--venue:' + esc(e.colour) + '"><small>' + esc(e.dow) + '</small>' + e.day + '</span>' +
          '<span class="sg-main"><strong>' + esc(e.name) + '</strong><span>' + esc(e.venue) + ' · ' + esc(e.date) + ' · ' + esc(e.count) + '</span></span>' +
          '<span class="sg-price">' + esc(e.price) + '</span></a>';
      });
      data.venues.forEach(function (v) {
        html += '<a class="sg-item sg-venue" role="option" href="' + esc(v.url) + '"><span class="sg-pin" aria-hidden="true"></span>' +
          '<span class="sg-main"><strong>' + esc(v.name) + '</strong><span>' + esc(input.dataset.all) + '</span></span></a>';
      });
      if (!html) html = '<div class="sg-empty">' + esc(input.dataset.none.replace("{q}", q)) + '</div>';
      box.innerHTML = html;
      items = Array.prototype.slice.call(box.querySelectorAll(".sg-item"));
      box.hidden = false; input.setAttribute("aria-expanded", "true"); active = -1;
    }
    function lookup() {
      var q = input.value.trim();
      if (q.length < 1) { close(); return; }
      if (q === last && !box.hidden) return;
      last = q;
      if (box.hidden) {
        box.innerHTML = '<div class="sg-skeleton"></div><div class="sg-skeleton"></div>';
        box.hidden = false;
      }
      fetch(input.dataset.suggest + "?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) { if (input.value.trim() === q) render(data, q); })
        .catch(close);
    }
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(lookup, 120); });
    input.addEventListener("focus", function () { if (input.value.trim()) { last = ""; lookup(); } });
    input.addEventListener("keydown", function (e) {
      if (box.hidden || !items.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight((active + 1) % items.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight((active - 1 + items.length) % items.length); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); window.location = items[active].href; }
      else if (e.key === "Escape") { close(); }
    });
    document.addEventListener("click", function (e) { if (!input.form.contains(e.target)) close(); });
    if (/[?&]focus=search/.test(location.search)) input.focus();
  });

  // File pickers: show the chosen file name.
  document.querySelectorAll("[data-file]").forEach(function (input) {
    input.addEventListener("change", function () {
      var count = input.files ? input.files.length : 0;
      var name = count > 1 && input.dataset.many ? input.dataset.many.replace("{n}", count)
        : (count ? input.files[0].name : "");
      var label = input.parentNode.querySelector("[data-file-label]");
      if (name && label) {
        label.textContent = name;
        input.parentNode.classList.add("has-file");
      }
    });
  });

  function formatMoney(c, lang) {
    var e = Math.floor(c / 100), r = c % 100;
    var amount = e + (r ? (lang === "en" ? "." : ",") + (r < 10 ? "0" : "") + r : "");
    return (lang === "en" || lang === "nl") ? "€" + amount : amount + " €";
  }

  // Listing page: update the total when the quantity changes.
  document.querySelectorAll("form[data-buy]").forEach(function (form) {
    var qty = form.querySelector("[data-qty]");
    if (!qty) return;
    var price = +form.dataset.price, fee = +form.dataset.fee, lang = form.dataset.lang;
    qty.addEventListener("change", function () {
      var n = +qty.value;
      form.querySelector("[data-n]").textContent = n;
      form.querySelector("[data-sub]").textContent = formatMoney(price * n, lang);
      form.querySelector("[data-feesum]").textContent = formatMoney(fee * n, lang);
      form.querySelector("[data-total]").textContent = formatMoney((price + fee) * n, lang);
      var bar = document.querySelector("[data-buybar-total]");
      if (bar) bar.textContent = formatMoney((price + fee) * n, lang);
    });
  });

  // Share: draw a story-sized flyer and hand it to the phone's share sheet (Instagram, WhatsApp...).
  function loadImage(src) {
    return new Promise(function (resolve) {
      if (!src) return resolve(null);
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = src;
    });
  }

  function wrapLines(ctx, text, maxWidth, maxLines) {
    var words = text.split(/\s+/), lines = [], line = "";
    words.forEach(function (w) {
      var test = line ? line + " " + w : w;
      if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = w; } else { line = test; }
    });
    if (line) lines.push(line);
    return lines.slice(0, maxLines);
  }

  function drawStory(d, poster, artwork) {
    var W = 1080, H = 1920, P = 1180, c = document.createElement("canvas");
    c.width = W; c.height = H;
    var ctx = c.getContext("2d");
    ctx.fillStyle = "#15132b"; ctx.fillRect(0, 0, W, H);

    // Poster area
    ctx.save();
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(60, 60, W - 120, P, 36) : ctx.rect(60, 60, W - 120, P); ctx.clip();
    if (poster) {
      var s = Math.max((W - 120) / poster.width, P / poster.height);
      ctx.drawImage(poster, 60 + ((W - 120) - poster.width * s) / 2, 60 + (P - poster.height * s) / 2, poster.width * s, poster.height * s);
    } else {
      ctx.fillStyle = d.colour; ctx.fillRect(60, 60, W - 120, P);
      if (artwork) {
        var a = Math.max((W - 120) / artwork.width, P / artwork.height);
        ctx.drawImage(artwork, 60 + ((W - 120) - artwork.width * a) / 2, 60 + (P - artwork.height * a) / 2, artwork.width * a, artwork.height * a);
      } else {
        ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.lineWidth = 6;
        for (var x = -P; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 60 + P); ctx.lineTo(x + P * 0.7, 60); ctx.stroke(); }
      }
      var grad = ctx.createLinearGradient(0, 60 + P * 0.35, 0, 60 + P);
      grad.addColorStop(0, "rgba(0,0,0,0)"); grad.addColorStop(1, "rgba(0,0,0,0.5)");
      ctx.fillStyle = grad; ctx.fillRect(60, 60, W - 120, P);
      ctx.fillStyle = "#fff"; ctx.textAlign = "center";
      ctx.font = '700 40px "Big Shoulders Display"'; ctx.fillText(d.dow.toUpperCase(), 190, 160);
      ctx.font = '900 130px "Big Shoulders Display"'; ctx.fillText(d.day, 190, 280);
      ctx.font = '700 40px "Big Shoulders Display"'; ctx.fillText(d.mon.toUpperCase(), 190, 330);
      ctx.textAlign = "left";
      ctx.font = '900 170px "Big Shoulders Display"';
      var lines = wrapLines(ctx, d.title.toUpperCase(), W - 240, 3);
      lines.forEach(function (ln, i) { ctx.fillText(ln, 120, 60 + P - 90 - (lines.length - 1 - i) * 150); });
    }
    ctx.restore();

    // Ticket-stub tear line
    ctx.fillStyle = "#1d1a37"; ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(60, 60 + P, W - 120, H - P - 220, [0, 0, 36, 36]) : ctx.rect(60, 60 + P, W - 120, H - P - 220);
    ctx.fill();
    ctx.fillStyle = "#15132b";
    [[60, 60 + P], [W - 60, 60 + P]].forEach(function (pt) { ctx.beginPath(); ctx.arc(pt[0], pt[1], 30, 0, Math.PI * 2); ctx.fill(); });
    ctx.setLineDash([22, 18]); ctx.strokeStyle = "#15132b"; ctx.lineWidth = 6;
    ctx.beginPath(); ctx.moveTo(110, 60 + P); ctx.lineTo(W - 110, 60 + P); ctx.stroke(); ctx.setLineDash([]);

    // Details
    ctx.fillStyle = "#f5f3ec"; ctx.font = '600 58px "Instrument Sans"';
    ctx.fillText(wrapLines(ctx, d.title, W - 240, 1)[0], 120, 60 + P + 120);
    ctx.fillStyle = "#a9a6c0"; ctx.font = '400 44px "Instrument Sans"';
    ctx.fillText(d.venue + " · " + d.date, 120, 60 + P + 190);
    ctx.fillStyle = "#d4ff3a"; ctx.font = '800 150px "Big Shoulders Display"';
    ctx.fillText(d.price, 120, 60 + P + 360);

    // Wordmark
    ctx.fillStyle = "#f5f3ec"; ctx.font = '900 64px "Inter Tight"';
    ctx.fillText("spare", 120, H - 90);
    ctx.fillStyle = "#a9a6c0"; ctx.font = '500 36px "Instrument Sans"'; ctx.textAlign = "right";
    ctx.fillText(d.url.replace(/^https?:\/\//, "").split("/")[0], W - 120, H - 96);
    return c;
  }

  document.querySelectorAll("[data-share]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var d = btn.dataset;
      btn.disabled = true;
      Promise.all([
        document.fonts ? document.fonts.load('900 150px "Big Shoulders Display"') : null,
        document.fonts ? document.fonts.load('900 64px "Inter Tight"') : null,
        document.fonts ? document.fonts.load('600 50px "Instrument Sans"') : null,
        loadImage(d.poster),
        d.poster ? null : loadImage(d.art),
      ]).then(function (res) {
        var canvas = drawStory(d, res[2], res[3]);
        canvas.toBlob(function (blob) {
          btn.disabled = false;
          var file = new File([blob], "spare.png", { type: "image/png" });
          if (navigator.canShare && navigator.canShare({ files: [file] })) {
            navigator.share({ files: [file], text: d.text + " " + d.url }).catch(function () {});
            return;
          }
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob); a.download = "spare.png"; a.click();
          if (navigator.clipboard) navigator.clipboard.writeText(d.url).catch(function () {});
          btn.textContent = d.done;
        }, "image/png");
      });
    });
  });

  // Settings saved on this device only (sounds, vibration, which pop-up was closed).
  function readPref(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function savePref(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  // Tap sounds for the bottom menu: off unless someone picks one in Settings. Made in code, no sound files.
  var audio = null;
  function audioCtx() {
    var C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    if (!audio) audio = new C();
    if (audio.state === "suspended") audio.resume();
    return audio;
  }
  function tone(c, type, from, to, dur, vol, delay) {
    var t0 = c.currentTime + (delay || 0), osc = c.createOscillator(), gain = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(from, t0);
    if (to) osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(vol, t0 + 0.005);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain); gain.connect(c.destination);
    osc.start(t0); osc.stop(t0 + dur + 0.02);
  }
  var SOUNDS = {
    pop: function (c) { tone(c, "sine", 520, 920, 0.08, 0.25); },
    click: function (c) { tone(c, "square", 1800, 900, 0.025, 0.06); },
    bubble: function (c) { tone(c, "sine", 300, 1200, 0.11, 0.22); },
    bass: function (c) { tone(c, "sine", 120, 50, 0.18, 0.55); },
    chime: function (c) { tone(c, "triangle", 880, 0, 0.22, 0.14); tone(c, "triangle", 1320, 0, 0.26, 0.1, 0.06); }
  };
  function tapFeedback(kind) {
    var k = kind || readPref("spare-sound");
    if (k && SOUNDS[k]) { var c = audioCtx(); if (c) SOUNDS[k](c); }
    if (readPref("spare-vibrate") === "1" && navigator.vibrate) navigator.vibrate(8);
  }
  document.addEventListener("pointerdown", function (e) {
    if (e.target.closest && e.target.closest(".tabbar a")) tapFeedback();
  }, { passive: true });

  var soundBox = document.querySelector("[data-sound-settings]");
  if (soundBox) {
    var current = readPref("spare-sound") || "off";
    soundBox.querySelectorAll("input[name=sound]").forEach(function (r) {
      r.checked = r.value === current;
      r.addEventListener("change", function () { savePref("spare-sound", r.value); tapFeedback(r.value); });
    });
    var vibRow = soundBox.querySelector("[data-vibrate-row]");
    if (vibRow && navigator.vibrate) {
      var vib = vibRow.querySelector("input");
      vibRow.hidden = false;
      vib.checked = readPref("spare-vibrate") === "1";
      vib.addEventListener("change", function () { savePref("spare-vibrate", vib.checked ? "1" : "0"); if (vib.checked) navigator.vibrate(15); });
    }
  }

  // "Tonight" pop-up: at most once a day, a moment after the page opens; one tap or a swipe down closes it.
  var pop = document.querySelector("[data-tonight-pop]");
  if (pop) {
    var popDay = pop.dataset.day, popLink = pop.querySelector("a");
    var here = popLink && new URL(popLink.href, location.href).href === location.href;
    var closePop = function () {
      savePref("spare-pop-day", popDay);
      pop.classList.remove("in");
      setTimeout(function () { pop.hidden = true; }, 250);
    };
    if (readPref("spare-pop-day") !== popDay && !here) {
      setTimeout(function () {
        var install = document.querySelector("[data-install]");
        if (install && !install.hidden) return;
        pop.hidden = false;
        requestAnimationFrame(function () { pop.classList.add("in"); });
      }, 2500);
    }
    pop.querySelector("[data-tonight-close]").addEventListener("click", closePop);
    if (popLink) popLink.addEventListener("click", function () { savePref("spare-pop-day", popDay); });
    var popY = null;
    pop.addEventListener("touchstart", function (e) { popY = e.touches[0].clientY; }, { passive: true });
    pop.addEventListener("touchmove", function (e) { if (popY !== null && e.touches[0].clientY - popY > 40) { popY = null; closePop(); } }, { passive: true });
  }

  // Sell form: "other venue" field, live payout summary.
  var sell = document.querySelector("form[data-sell-form]");
  if (!sell) return;

  var feePct = parseFloat(sell.dataset.feePct) || 0, feeMin = parseInt(sell.dataset.feeMin, 10) || 0;
  var feeMax = parseInt(sell.dataset.feeMax, 10) || 0;
  function feeFor(p) { var f = Math.max(feeMin, Math.round(p * feePct / 100)); return feeMax ? Math.min(f, feeMax) : f; }
  var cap = parseInt(sell.dataset.cap, 10) || 100;
  var sellLang = sell.dataset.lang;
  var venue = sell.querySelector("[data-venue]");
  var other = sell.querySelector("[data-other-venue]");
  var face = sell.querySelector("[data-face]");
  var price = sell.querySelector("[data-price]");
  var receive = document.querySelector("[data-receive]");
  var buyerPays = document.querySelector("[data-buyer-pays]");

  function cents(value) {
    var v = (value || "").replace(/[€\s]/g, "").replace(",", ".");
    if (!/^\d{1,4}(\.\d{1,2})?$/.test(v)) return null;
    return Math.round(parseFloat(v) * 100);
  }
  function money(c) { return formatMoney(c, sellLang); }
  function update() {
    var p = cents(price.value), f = cents(face.value);
    receive.textContent = p ? money(p) : "–";
    buyerPays.textContent = p ? money(p + feeFor(p)) : "–";
    var over = p && f && p > Math.floor(f * cap / 100);
    price.setAttribute("aria-invalid", over ? "true" : "false");
    price.style.borderColor = over ? "var(--bad)" : "";
  }
  // Pre-fill the price with the face value: the most common case is selling at cost.
  face.addEventListener("change", function () {
    if (!price.value && cents(face.value)) { price.value = face.value; update(); }
  });
  face.addEventListener("input", update);
  price.addEventListener("input", update);
  if (venue) venue.addEventListener("change", function () {
    other.hidden = venue.value !== "__other";
    if (!other.hidden) other.querySelector("input").focus();
  });

  // "Which event?": pick the date, then tap the event. Not listed? Add it.
  var picker = sell.querySelector("[data-picker]");
  if (picker) {
    var dateInput = picker.querySelector("[data-picker-date]");
    var list = picker.querySelector("[data-picker-list]");
    var addNew = picker.querySelector("[data-pick-new]");
    var manual = sell.querySelector("[data-manual]");
    var selected = picker.dataset.selected;
    function esc(v) { return String(v).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
    function syncManual() {
      var checked = sell.querySelector('input[name="event_id"]:checked');
      manual.hidden = !!(checked && checked.value !== "new");
    }
    function load() {
      if (!dateInput.value) { list.innerHTML = ""; addNew.hidden = true; manual.hidden = true; return; }
      fetch(picker.dataset.api + "?date=" + dateInput.value).then(function (r) { return r.json(); }).then(function (data) {
        list.innerHTML = data.events.map(function (e) {
          return '<label class="pick"><input type="radio" name="event_id" value="' + e.id + '"' + (String(e.id) === selected ? " checked" : "") + '>' +
            '<span><strong>' + esc(e.name) + '</strong><small>' + esc(e.venue) + (e.city !== "Rotterdam" ? ", " + esc(e.city) : "") +
            ' · ' + esc(e.category) + (e.time ? " · " + esc(e.time) : "") + '</small></span></label>';
        }).join("");
        addNew.hidden = false;
        var newRadio = addNew.querySelector("input");
        if (!data.events.length) newRadio.checked = true;
        else if (!sell.querySelector('input[name="event_id"]:checked')) list.querySelector("input").checked = false;
        syncManual();
        if (!sell.querySelector('input[name="event_id"]:checked')) manual.hidden = true;
      });
    }
    dateInput.addEventListener("change", function () { selected = ""; load(); });
    picker.addEventListener("change", function (e) { if (e.target.name === "event_id") syncManual(); });
    manual.hidden = true;
    load();
  }
  update();
})();

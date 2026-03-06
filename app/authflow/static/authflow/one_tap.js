"use strict";

(function () {
  function getCookie(name) {
    var cookieValue = "";
    if (!document.cookie) {
      return cookieValue;
    }
    var cookies = document.cookie.split(";");
    for (var i = 0; i < cookies.length; i += 1) {
      var cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === name + "=") {
        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
        break;
      }
    }
    return cookieValue;
  }

  function setStatus(text) {
    var statusEl = document.getElementById("google-onetap-status");
    if (!statusEl) {
      return;
    }
    statusEl.textContent = text || "";
  }

  function postCredential(endpoint, credential, nextPath) {
    return fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken")
      },
      body: JSON.stringify({
        credential: credential,
        next: nextPath
      })
    });
  }


  function waitForGsi(maxWaitMs) {
    var limit = typeof maxWaitMs === "number" ? maxWaitMs : 3000;
    var started = Date.now();
    return new Promise(function (resolve) {
      function check() {
        if (window.google && window.google.accounts && window.google.accounts.id) {
          resolve(true);
          return;
        }
        if (Date.now() - started >= limit) {
          resolve(false);
          return;
        }
        window.setTimeout(check, 100);
      }
      check();
    });
  }

  function initOneTap() {
    var configEl = document.getElementById("google-onetap-config");
    if (!configEl) {
      return;
    }
    var clientId = (configEl.dataset.clientId || "").trim();
    var endpoint = (configEl.dataset.endpoint || "").trim();
    var nextPath = (configEl.dataset.next || "/theses/new/").trim();
    if (!clientId || !endpoint) {
      return;
    }
    setStatus("");
    waitForGsi(3000).then(function (ready) {
      if (!ready) {
        setStatus("Google One Tap is unavailable right now.");
        return;
      }
      window.google.accounts.id.initialize({
      client_id: clientId,
      callback: function (response) {
        var credential = response && response.credential ? response.credential : "";
        if (!credential) {
          setStatus("Google credential was not provided.");
          return;
        }
        postCredential(endpoint, credential, nextPath)
          .then(function (res) {
            return res.json().then(function (data) {
              return { status: res.status, data: data };
            });
          })
          .then(function (result) {
            if (result.status >= 200 && result.status < 300 && result.data && result.data.ok) {
              window.location.assign(result.data.next || nextPath || "/theses/new/");
              return;
            }
            setStatus("Google sign-in failed. Please use email sign-in.");
          })
          .catch(function () {
            setStatus("Google sign-in failed. Please use email sign-in.");
          });
      }
    });

      window.google.accounts.id.prompt();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initOneTap);
  } else {
    initOneTap();
  }
})();

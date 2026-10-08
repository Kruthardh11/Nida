// Polling mechanism to sync with PyWebView Python API
let lastMode = "";
let lastUserText = "";
let lastResponse = "";

async function updateStatus() {
    if (window.pywebview && window.pywebview.api) {
        try {
            const data = await window.pywebview.api.get_status();
            
            // Update Mode & Orb
            if (data.mode !== lastMode) {
                lastMode = data.mode;
                const orb = document.getElementById("orb");
                const badge = document.getElementById("status-badge");
                const statusText = document.getElementById("status-text");

                // Reset classes
                orb.className = "orb";
                badge.className = "badge";

                if (data.mode === "sleeping") {
                    orb.classList.add("sleeping");
                    badge.classList.add("sleeping");
                    badge.innerText = "SLEEPING";
                    statusText.innerText = "Zzz...";
                } else if (data.mode === "listening" || data.mode === "awake") {
                    orb.classList.add("listening");
                    badge.classList.add("awake");
                    badge.innerText = "AWAKE";
                    statusText.innerText = "Listening...";
                } else if (data.mode === "reasoning" || data.mode === "acting") {
                    orb.classList.add("thinking");
                    badge.classList.add("awake");
                    badge.innerText = "AWAKE";
                    statusText.innerText = "Processing...";
                }
            }

            // Update Transcripts
            if (data.transcript && data.transcript !== lastUserText) {
                lastUserText = data.transcript;
                const userBubble = document.getElementById("user-transcript");
                userBubble.innerText = `"${data.transcript}"`;
                userBubble.style.opacity = 1;
            }

            if (data.response && data.response !== lastResponse) {
                lastResponse = data.response;
                const nidaBubble = document.getElementById("nida-response");
                nidaBubble.innerText = data.response;
                nidaBubble.style.opacity = 1;
            }

        } catch (err) {
            console.error("API error:", err);
        }
    }
}

// Poll every 500ms
setInterval(updateStatus, 500);

// Setup Buttons
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("btn-sleep").addEventListener("click", () => {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.sleep_nida();
        }
    });

    document.getElementById("btn-wake").addEventListener("click", () => {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.wake_nida();
        }
    });
});

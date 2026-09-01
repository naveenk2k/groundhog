const transcriptStatus = document.getElementById("status");
const transcriptText = document.getElementById("transcript");
const transcriptSource = document.getElementById("source");
const transcriptNotice = document.getElementById("notice");
const videoId = new URLSearchParams(location.search).get("video_id");

async function loadTranscript() {
  if (!videoId) {
    throw new Error("No video was selected.");
  }
  const { groundhogSecret } = await chrome.storage.local.get("groundhogSecret");
  if (!groundhogSecret) {
    throw new Error("Groundhog isn't set up yet. Add your shared secret in Settings.");
  }
  const response = await fetch("http://127.0.0.1:8787/transcript/" + encodeURIComponent(videoId), {
    headers: { "X-Groundhog-Secret": groundhogSecret },
  });
  if (!response.ok) {
    throw new Error("Couldn't reach the Groundhog companion.");
  }
  const data = await response.json();
  if (!data.transcript) {
    throw new Error("No transcript is available for this video.");
  }
  document.title = (data.title || "Video") + " — Groundhog transcript";
  transcriptSource.textContent = data.creator ? data.title + " — " + data.creator : data.title || videoId;
  if (data.source === "local_transcription") {
    transcriptNotice.textContent = "Generated locally from the video's audio. Long videos use short samples, not full captions.";
    transcriptNotice.hidden = false;
  }
  transcriptStatus.hidden = true;
  transcriptText.textContent = data.transcript;
  transcriptText.hidden = false;
}

loadTranscript().catch((error) => {
  transcriptStatus.className = "error";
  transcriptStatus.textContent = error.message;
  transcriptSource.textContent = videoId || "Groundhog";
});

const API_BASE = "http://localhost:8000";

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) {
    return;
  }

  const payload = await chrome.tabs.sendMessage(tab.id, { type: "MIRA_STYLIST_EXTRACT" });
  if (!payload) {
    return;
  }

  // TODO:
  // - gate by user auth/session
  // - prefer POST /garments/ingest/image-url when an image URL is available
  // - fall back to /garments/ingest/product-page-url only when needed
  // - deep link the user into mobile or web preview
  console.log("Would send garment ingest payload", {
    endpoint: payload.image_url
      ? `${API_BASE}/garments/ingest/image-url`
      : `${API_BASE}/garments/ingest/product-page-url`,
    payload,
  });
});

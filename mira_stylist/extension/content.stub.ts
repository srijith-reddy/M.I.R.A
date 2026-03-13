type ProductPayload = {
  image_url?: string;
  referring_page_url: string;
  title?: string;
  brand?: string;
};

function detectProductPayload(): ProductPayload | null {
  const title = document.querySelector("meta[property='og:title']")?.getAttribute("content")
    ?? document.title;
  const image = document.querySelector("meta[property='og:image']")?.getAttribute("content");

  if (!title) {
    return null;
  }

  return {
    image_url: image ?? undefined,
    referring_page_url: window.location.href,
    title,
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "MIRA_STYLIST_EXTRACT") {
    return;
  }

  // TODO:
  // - add selected-image extraction and not just OG-image fallback
  // - add retailer-aware parsing only as optional enrichment
  sendResponse(detectProductPayload());
});

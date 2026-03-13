# Browser Extension Plan

## Purpose

Enable a future "Try with MIRA Stylist" action on product pages without tightly coupling extension logic to the backend implementation.

## Likely Flow

1. Detect the currently selected or dominant product image on a page.
2. Extract lightweight page metadata:
   - current page URL
   - page title
   - selected image URL if obvious
3. Let the user click "Try with MIRA Stylist".
4. Prefer sending the image itself or direct image URL to the backend.
5. Treat the current page URL as optional metadata enrichment.
6. Open a mobile deep link or web preview flow once the garment is registered.

## Extension Design Rules

- keep retailer detection heuristic and modular
- do not claim universal parsing accuracy
- require explicit user action before sending page metadata
- keep auth/session handling separate from page-scraping logic

## Backend Contract Shape

The extension should eventually map to one of the image-first backend contracts:

```json
{
  "uploaded_by": "user_123",
  "image_url": "https://cdn.example.com/images/hero.jpg",
  "referring_page_url": "https://example.com/product/123",
  "title": "Relaxed Wool Coat"
}
```

## Future Enhancements

- retailer-specific parsers
- user profile awareness for saved avatars
- queue handoff to mobile app or web dashboard

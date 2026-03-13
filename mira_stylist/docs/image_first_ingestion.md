# Image-First Ingestion

## Product Decision

MIRA Stylist should treat image-first ingestion as the primary UX and system architecture.

Primary sources:

- uploaded image files
- pasted product images
- screenshots
- direct image URLs

Secondary source:

- product-page URLs for best-effort metadata enrichment

## Why This Matters

- image-first is universal across websites, marketplaces, social apps, and screenshots
- it reduces dependence on brittle retailer HTML parsing
- it matches the real user behavior of saving or sharing product images
- it keeps the MVP usable even when websites are blocked or inconsistent

## Canonical Flow

1. accept a `GarmentInput`
2. persist the raw asset or URL reference
3. normalize input metadata
4. create one or more `SourceImageRef` records
5. create `GarmentCandidateImage` records
6. let the user confirm a candidate if needed
7. create a canonical `GarmentItem`
8. trigger future segmentation, reconstruction, and preview hooks

## MVP Logic Implemented

- raw input persistence
- filename sanitization
- file hashing for binary inputs
- lightweight PNG/JPEG/GIF metadata extraction
- candidate SVG preview artifact generation
- auto-finalization when exactly one candidate is present and user confirmation is not required
- persisted ingestion request/result metadata for restart-safe candidate selection

Still deferred:

- real garment segmentation
- learned candidate extraction
- background removal
- retailer-specific parsing beyond generic metadata handling

## Expected Best Cases

- studio product photos with one clearly visible garment
- pasted hero images from commerce pages
- screenshots where one item is dominant
- image URLs pointing directly to product imagery

## Known Limitations

- cluttered screenshots may contain multiple garments or UI chrome
- product photos may show layered outfits instead of a single item
- mirror selfies are not reliable garment-isolation inputs
- background clutter can lower segmentation confidence
- a single image is not enough for exact drape or fit realism
- product-page scraping is unreliable without retailer-specific adapters

## API Implication

The API should expose input-type-specific routes, but all of them should feed the same normalized ingestion service.

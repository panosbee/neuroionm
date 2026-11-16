# Admin Panel Contract

The Admin panel is served from `/admin.html` and is intentionally separate from Live and Academy workflows.

## Admin Login

Admin accounts can log in through `/login.html` and receive an HTTP-only signed session cookie. The built-in system admin remains available, and DB-backed users with `access_level=admin` can also open the Admin page and admin APIs.

Normal users can log in to the protected platform surfaces, but they cannot open `/admin.html` and cannot call admin APIs.

## User Accounts

The Admin panel creates real login accounts. Required fields are:

- username
- password
- first name
- last name
- email
- phone

Optional profile fields are:

- professional title
- specialty
- organization
- department
- city
- country
- license number
- notes

Passwords are stored as PBKDF2 hashes. Admins can reset user passwords from the Users list. Created accounts are stored in `admin_users` with `login_enabled=true` when active.

## AI Knowledge Base

The Admin knowledge base accepts PDF uploads. Each upload is processed as follows:

1. Store the PDF under `IONM_ADMIN_UPLOAD_DIR`.
2. Extract embedded PDF text when available.
3. Run Tesseract OCR for pages without usable embedded text.
4. Split extracted text into `admin_knowledge_chunks`.
5. Rebuild the shared Jina `knowledge_embeddings` index.
6. Make the chunks available to `/api/ai/ask` through the existing RAG retrieval path.

The OCR layer uses:

- `TESSERACT_CMD` for the local/server binary path when Tesseract is not on PATH.
- `TESSDATA_PREFIX` when language data is installed in a non-default directory.
- `TESSERACT_LANGUAGES`, default `eng+ell`.

## Body Map Curation

The Admin panel includes a body-map coordinate editor for the high-resolution anatomical asset. The workflow is:

1. Select a muscle/electrode target.
2. Select the left, right or central pin.
3. Choose a focus view or full image view.
4. Click the anatomical image to move the selected pin.
5. Save the coordinate to MongoDB only after the pending position is correct.
6. Use `Discard unsaved` to throw away a pending click before it is saved.
7. Use `Reset target from seed` to restore the selected muscle from `ionm-coords.json` and clear curation metadata.
8. Export `ionm-coords-curated.json` when a reviewed set is ready for seed replacement.

Legacy seed coordinates from `ionm-coords.json` are normalized at runtime from the old prototype view bounds into the current `body.jpg` asset bounds. Coordinates saved from the Admin editor are stored directly as `percentage_of_full_image`, matching `bodymap-asset.json` and the Live/Academy projection. Saved records are marked `curation_status=reviewed` and `coordinate_source=admin_bodymap_curation`.

For a command-line export from MongoDB:

```bash
python -m backend.app.scripts.export_coords --output ionm-coords-curated.json
```

## API Surface

- `POST /api/admin/login`
- `POST /api/admin/logout`
- `GET /api/admin/session`
- `GET /api/admin/users`
- `POST /api/admin/users`
- `POST /api/admin/users/{user_id}/password`
- `GET /api/admin/knowledge/documents`
- `POST /api/admin/knowledge/documents`
- `GET /api/admin/bodymap/asset`
- `GET /api/admin/bodymap/coordinates`
- `PATCH /api/admin/bodymap/coordinates`
- `POST /api/admin/bodymap/coordinates/reset`
- `GET /api/admin/bodymap/export`

## Current Boundaries

- The high-resolution body-map asset is installed, but all coordinates must still be clinically reviewed before changing `bodymap-asset.json` to a production-curated status.
- Knowledge Base quality depends on the official PDFs uploaded by admins.

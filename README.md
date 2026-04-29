## Profiles search endpoint (`GET /api/profiles/search`)

## Authentication

This project includes GitHub OAuth authentication under `/auth`.

Required environment variables:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/insighta_labs
PUBLIC_BASE_URL=http://localhost:8000
JWT_SECRET_KEY=change-me
GITHUB_CLIENT_ID=your_web_github_client_id
GITHUB_CLIENT_SECRET=your_web_github_client_secret
```

GitHub OAuth app callback:

- `${PUBLIC_BASE_URL}/auth/github/callback`

Auth endpoints:

- `GET /auth/github` starts the OAuth flow.
- `GET /auth/github/callback` exchanges a GitHub `code` and signed `state`, creates or updates the user, and returns an access/refresh token pair.
- `POST /auth/refresh` accepts `{"refresh_token": "..."}`, deletes the old refresh token, and returns a new token pair.
- `POST /auth/logout` accepts `{"refresh_token": "..."}` and deletes it server-side.
- `GET /auth/me` returns the current bearer-token user.

Access tokens expire in 3 minutes. Refresh tokens expire in 5 minutes and are stored as SHA-256 hashes in the `refresh_tokens` table so they can be revoked without storing raw token values.

Use the reusable dependencies from `app.core.auth` to protect routes:

```python
from fastapi import Depends
from app.core.auth import get_current_user, require_admin, require_analyst


@router.get("/admin-only")
def admin_only(current_user=Depends(require_admin)):
    return {"status": "success"}
```

### How the parser works

- **Rule-based only**: The search endpoint uses a deterministic, hand-written parser. It does not call any AI or LLMs.
- **Input**: A natural language query string in the `q` parameter, plus optional `page` and `limit` for pagination.
- **Output**: The parser converts `q` into structured filters that are applied to the `profiles` table using indexed queries.

Supported keywords and mappings:

- **Age bands**
  - **"young"**: maps to `min_age = 16`, `max_age = 24`.
- **Gender**
  - **"male" / "males"**: sets `gender = "male"`.
  - **"female" / "females"**: sets `gender = "female"`.
- **Age comparisons**
  - **"above X" / "over X"**: sets `min_age = X`.
  - **"below X" / "under X"**: sets `max_age = X`.
  - Multiple occurrences keep the **most restrictive** range (highest `min_age`, lowest `max_age`).
- **Country**
  - **"from &lt;country name&gt;"**: sets `country_id` by mapping the country name to an ISO 2-letter code.
  - Built-in mappings include: US/USA/United States → `US`, United Kingdom / UK / England → `GB`, Nigeria → `NG`, Ghana → `GH`, Kenya → `KE`, India → `IN`, Canada → `CA`, Germany → `DE`, France → `FR`, Spain → `ES`, Italy → `IT`.
- **Age groups**
  - **"child" / "children"**: sets `age_group = "child"`.
  - **"teenager" / "teenagers"**: sets `age_group = "teenager"`.
  - **"adult" / "adults"**: sets `age_group = "adult"`.
  - **"senior" / "seniors"**: sets `age_group = "senior"`.
- **Combined phrase**
  - **"teenagers above 17"**: sets `age_group = "teenager"` and `min_age = 17`.

Pagination:

- **`page`**: 1-based page index; defaults to `1` if omitted.
- **`limit`**: page size; defaults to `10`, maximum `50`.
- Both `page` and `limit` are validated as integers and share the same semantics as `GET /api/profiles`.

If at least one of `gender`, `age_group`, `country_id`, `min_age`, or `max_age` is recognized, the endpoint runs an indexed query with those filters plus pagination and returns:

```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 123,
  "data": [{ "...profile fields..." }]
}
```

If the query cannot be interpreted into any filters, the endpoint returns:

```json
{
  "status": "error",
  "message": "Unable to interpret query"
}
```

### Limitations and edge cases

- **Limited vocabulary**: Only the specific keywords listed above are understood. Synonyms or more complex phrasing (e.g. "in their early twenties", "middle-aged") are not recognized.
- **Country coverage**: Only a fixed set of country names is mapped to ISO codes. Unrecognized country names are ignored (no `country_id` filter is applied).
- **Ambiguous phrases**: The parser does not resolve ambiguity (e.g. "young adults" is treated as containing both "young" and "adult", and simply applies their combined rules).
- **Numeric parsing**: Age values are parsed as simple integers from patterns like `"above 30"`, `"under 21"`. Textual numbers ("above twenty") are not supported.
- **Conflicting constraints**: If the resulting `min_age` is greater than `max_age`, the endpoint returns a 400 error (`min_age cannot be greater than max_age`) instead of silently relaxing constraints.
- **No free-text search**: The endpoint does not search `name` or other free-text fields. It only applies the structured demographic filters described above.

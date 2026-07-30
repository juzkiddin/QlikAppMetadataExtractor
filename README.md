# Qlik App Metadata Extractor — High-Level Design

**Component:** `QlikAppMetadataExtractor.py`
**Audience:** Engineers, integrators, and reviewers with working knowledge of Python, HTTP, and REST APIs
**Companion document:** `QlikAppMetadataExtractor_DOCUMENTATION.md` (reference-level, line-by-line)

---

## 1. Purpose and Scope

A lightweight batch collector that retrieves the **structural metadata** of one or more Qlik Sense applications over REST and persists each as a JSON document.

Metadata here means the app's data-model description — fields, tables, cardinalities, row counts, memory footprint, section-access flag — not the row values themselves.

### In scope

- Certificate-authenticated Qlik session lifecycle (create/delete)
- `GET /api/v1/apps/{guid}/data/metadata` for an arbitrary set of applications
- Normalisation into a self-describing document plus a run-level manifest
- Transient-failure resilience via connection reuse and bounded retries

### Explicitly out of scope

- Row-level data extraction (see `QlikAppDataExtractor.py`)
- App discovery/enumeration — GUIDs must be supplied; the QRS API is not called
- Diffing metadata between runs or over time
- Any write operation against Qlik

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       Process boundary                       │
│                                                              │
│  config.json ──► main()                                      │
│                    │                                         │
│                    ├─► build_http_session()  (pool + retry)  │
│                    ├─► generate_session()    ─── HTTPS ──┐   │
│                    ├─► build_headers()                   │   │
│                    ├─► extract_metadata_for_apps()       │   │
│                    │     └─ per app:                     │   │
│                    │          fetch_app_metadata() ──────┼─┐ │
│                    │          build_metadata_document()  │ │ │
│                    │          save_json()                │ │ │
│                    │          summarize_metadata()       │ │ │
│                    └─► delete_session()  ────────────────┘ │ │
│                    └─► http_session.close()                │ │
└────────────────────────────────────────────────────────────┼─┘
                                                             │
                      ┌──────────────────────────────────────┴──┐
                      │  Qlik Sense                             │
                      │   • QPS  /qps/session            (HTTPS)│
                      │   • REST /api/v1/apps/…/metadata (HTTPS)│
                      └─────────────────────────────────────────┘
```

Single transport throughout. Unlike the data extractor, there is **no WebSocket and no Engine session** — metadata is served directly by the REST layer, which is why a full app can be profiled in one round trip.

| Call | Endpoint | Auth |
|------|----------|------|
| Session create/delete | `{proxy_server}/qps/session[/{id}]` | Client certificate (mTLS) |
| Metadata read | `https://{url}/api/v1/apps/{guid}/data/metadata` | Session cookie + `X-Qlik-User`; certificate optional |

---

## 3. Authentication Model

```
uuid4() ──► POST /qps/session (mTLS)
              │
              └─► session_id
                    │
                    ├─► Cookie: {cookie_name}={session_id}
                    └─► X-Qlik-User: UserDirectory=…;UserId=…
                          │
                          └─► GET …/data/metadata   (× N apps)
```

Two properties worth noting:

- **One session amortised across all apps.** Session establishment is the expensive step; the metadata reads are cheap by comparison. Processing 50 apps costs one login, not 50.
- **Certificate use on the metadata call is opt-in.** `metadata_use_client_cert` defaults to `false`, matching the historically working behaviour where cookie auth alone is sufficient. Enable it only if the virtual proxy enforces mTLS on the REST surface.

`X-Qlik-User` determines the effective identity, so an app the user cannot access returns `403` rather than being silently omitted.

---

## 4. Resilience Layer

`build_http_session()` constructs the one `requests.Session` used for every call in the run, mounted with a configured `HTTPAdapter`:

```python
Retry(
    total=retries,                                  # default 3
    backoff_factor=0.5,                             # 0.5s, 1s, 2s …
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods={"GET", "POST", "DELETE"},
    raise_on_status=False,
)
HTTPAdapter(max_retries=…, pool_connections=10, pool_maxsize=10)
```

Design intent behind each choice:

| Setting | Rationale |
|---------|-----------|
| `status_forcelist` limited to 429/5xx | Retrying `401`/`403`/`404` is pointless — those are deterministic, and retrying `401` risks lockout policies |
| `allowed_methods` includes `POST`/`DELETE` | Session create/delete are idempotent in effect here: the client supplies the UUID, so a replayed create converges rather than duplicating |
| `backoff_factor=0.5` | Gives a saturated Engine room to recover without extending the run materially |
| `raise_on_status=False` | Keeps error surfacing in application code (`raise_for_status`) so messages stay uniform |

Connection pooling yields TLS-handshake reuse rather than parallelism — execution remains sequential. The pool size of 10 is headroom for a future concurrent variant, not a current throughput lever.

---

## 5. Control Flow

```
load_config ─► validate required keys ─► resolve_app_guids
                                              │
                        build_http_session ─► generate_session ─► build_headers
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        │  for each app GUID (sequential)           │
                        │    fetch_app_metadata()      → dict       │
                        │    build_metadata_document() → wrapped    │
                        │    save_json(<guid>_metadata)             │
                        │    [save_json(<guid>_metadata_raw)]       │
                        │    summarize_metadata()      → manifest   │
                        │    ── on exception: record, continue ──   │
                        └───────────────────────────────────────────┘
                                              │
                     save _metadata_summary.json ─► delete_session ─► close
```

Validation is front-loaded: configuration completeness and target resolution both occur **before** a session is created, so a misconfigured run never touches the server.

`resolve_app_guids()` implements the target precedence:

| Config | Result |
|--------|--------|
| `app_guids: [...]` | Used as-is, empty entries filtered |
| `app_guids: "guid"` | Coerced to a single-element list — tolerates a common config mistake |
| `app_guid: "guid"` | Single-element list |
| Neither | Empty list → `main()` exits `1` with a specific message |

---

## 6. Response Handling Contract

`fetch_app_metadata()` deliberately returns a **parsed dictionary**, not a `requests.Response`. This keeps HTTP concerns from leaking into the orchestration layer and makes the function trivially mockable in tests.

Two distinct failure classes are separated:

| Class | Trigger | Surfaced as |
|-------|---------|-------------|
| Transport/HTTP | Connection error, or non-2xx after retries | `requests.RequestException`, logged with app GUID and URL |
| Payload | Body is not parseable JSON | `RuntimeError` naming the app and HTTP status, chained via `from e` |

The payload case matters more than it first appears. A rejected or expired session commonly yields **HTTP 200 with an HTML login page**. Treating that as a soft warning would let a wholly failed run present as successful — so it is escalated to an error that lands in `failures`.

---

## 7. Output Contract

```
metadata_output/
├── {app_guid}_metadata.json       # normalised document, one per app
├── {app_guid}_metadata_raw.json   # optional passthrough (save_raw_response)
└── _metadata_summary.json         # run manifest
```

### Normalised document

```json
{
  "appGuid": "a1b2c3d4-…",
  "extractedAt": "2026-07-30T09:45:00+00:00",
  "fieldCount": 87,
  "tableCount": 12,
  "tableSummary": [
    { "name": "Sales", "rowCount": 50000, "fieldCount": 8,
      "keyFieldCount": 2, "byteSize": 4194304, "isSystem": false }
  ],
  "metadata": { "…": "verbatim Qlik response" }
}
```

The document is **additive, never lossy**: derived fields (`fieldCount`, `tableCount`, `tableSummary`) sit alongside the untouched upstream payload under `metadata`. Consumers can rely on the flattened view; auditors can diff the raw block against the API. `save_raw_response` exists for the narrower case of byte-comparable API verification.

### Run manifest

`_metadata_summary.json` carries `extractedAt`, `appCount`, a per-app block under `apps` (`fieldCount`, `tableCount`, `totalRowCount`, `staticByteSize`, `hasSectionAccess`, `usage`), and error strings under `failures`. It is written unconditionally — including when every app failed — so its presence is a reliable run-completion marker.

### Defensive parsing

`summarize_metadata()` and `build_metadata_document()` both use `data.get(key) or []`, which normalises **missing keys and explicit `null` alike**. `tableSummary` additionally filters on `isinstance(t, dict)`. A sparse or partially-populated response degrades to zeroes rather than raising `TypeError` — appropriate because an unusual-but-parseable response is still worth recording.

---

## 8. Failure Model

| Scope | Handling | Rationale |
|-------|----------|-----------|
| Config load / validation | Return `1` before any network call | Fail fast, no server-side footprint |
| No target GUIDs | Return `1` with a specific message | Distinguishes misconfiguration from an empty result |
| Session creation | Propagate, abort run | Nothing can proceed |
| Per-app metadata fetch | Catch, record in `failures`, continue | One inaccessible app should not forfeit the rest |
| Session deletion | Catch, log `WARNING` | Data already collected; a leaked session is operational, not data loss |

Cleanup is a two-step `finally`: `delete_session()` guarded by `if session_id`, then an unconditional `http_session.close()` to release pooled sockets.

Exit codes: `0` only when `failures` is empty; `1` for any failure mode, config or runtime.

---

## 9. Performance Characteristics

| Dimension | Behaviour |
|-----------|-----------|
| Concurrency | Sequential across apps |
| Round trips | `1 (login) + N (apps) + 1 (logout)` |
| TLS handshakes | 1 for the whole run (pooled session) |
| Memory | O(largest single metadata response) — bounded and small |
| Typical runtime | Seconds, even for tens of apps |
| Dominant cost | Server-side metadata assembly, not transfer |

This component is cheap by construction. The metadata endpoint returns a compact structural description regardless of how many rows the app holds, so runtime is effectively independent of data volume — the key asymmetry versus the data extractor.

If N grows into the hundreds, thread-pool parallelism is straightforward: the session is thread-safe for this usage, the pool is already sized at 10, and the per-app loop body has no shared mutable state beyond two dictionary writes. That change is deferred until a real need appears, in line with keeping failure attribution simple.

---

## 10. Security Posture

1. **TLS verification disabled** — `verify=False` on every call. Acceptable against internal hosts with self-signed certificates; it forfeits MITM protection. Pin a CA bundle where the environment permits.
2. **Credential paths in plaintext config** — `client_cert`/`client_key` are filesystem paths; file permissions are the only control. Exclude these files from version control.
3. **Output sensitivity is lower than the data extractor's, but non-zero** — field and table names, row counts, and the section-access flag constitute a schema disclosure. Treat `metadata_output/` as internal.

The browser-mimicking `User-Agent` and `Sec-Fetch-*` headers are proxy-compatibility shims, not security controls.

---

## 11. Relationship to the Data Extractor

| Aspect | `QlikAppMetadataExtractor.py` | `QlikAppDataExtractor.py` |
|--------|-------------------|------------------------------|
| Question answered | What is the shape of this app? | What are the values in this app? |
| Transport | REST/HTTPS | WebSocket JSON-RPC |
| Requests per app | 1 | 2 + one per 1000-row chunk per table |
| Runtime | Seconds | Minutes to hours |
| Memory ceiling | Bounded, small | O(largest table) |
| Multi-target axis | Multiple **apps** | Multiple **tables** in one app |
| Output root | `metadata_output/` | `output/` |
| Config file | `config.json` | `config.json` |

### Recommended composition

Use this component as a **planning pass** ahead of extraction:

```
QlikAppMetadataExtractor.py            → tableSummary: names, rowCount, byteSize
        │
        └─► select tables worth extracting
                │
                └─► config.json: "table_names": [ … ]
                        │
                        └─► QlikAppDataExtractor.py
```

One cheap REST call reveals which tables are large, empty, or system-generated, so the expensive WebSocket extraction is scoped deliberately rather than pulling every table by default.

Both scripts read `config.json` by default and share an identical connection block (`user_id`, `user_directory`, `proxy_server`, `client_cert`, `client_key`, `url`, `xrfkey`, `cookie_name`), so a single file can drive both. Only the target keys differ: `app_guid`/`app_guids` here, `table_name`/`table_names` there. Unknown keys are ignored by both, so the union is safe to keep in one file.

---

## 12. Configuration Surface

| Key | Required | Default | Notes |
|-----|----------|---------|-------|
| `user_id`, `user_directory` | yes | — | Asserted identity; governs app visibility |
| `proxy_server` | yes | — | Full URL including scheme |
| `client_cert`, `client_key` | yes | — | mTLS pair for QPS |
| `url` | yes | — | Hostname only, no scheme |
| `xrfkey` | yes | — | Must be exactly 16 characters |
| `cookie_name` | yes | — | Virtual-proxy specific |
| `app_guids` | one of | — | List of targets; precedence over `app_guid` |
| `app_guid` | one of | — | Single target |
| `output_dir` | no | `metadata_output` | Relative to working directory |
| `request_timeout` | no | `120` | Seconds per metadata call |
| `retries` | no | `3` | Retry budget for transient failures |
| `save_raw_response` | no | `false` | Also write the verbatim response |
| `metadata_use_client_cert` | no | `false` | Send mTLS on the metadata call |

Validation reports **all** missing required keys in a single pass.

---

## 13. Known Limitations and Extension Points

| Limitation | Impact | Direction |
|------------|--------|-----------|
| No app enumeration | GUIDs must be supplied manually | Add a QRS `/qrs/app` discovery pass |
| Sequential execution | Linear in N | `ThreadPoolExecutor` over the app loop |
| No historical diffing | Schema drift invisible | Compare successive `tableSummary` snapshots |
| Filename keyed on GUID | Not human-browsable | Include app name from QRS in the filename |
| Retry budget is global per request | Slow apps consume wall clock | Per-app timeout override |

The module is import-safe and its transformation functions are pure, so the natural seams are:

| Goal | Approach |
|------|----------|
| Alternative sink | Replace `save_json()`; `build_metadata_document()` is the stable interface |
| Schema-drift monitoring | Persist `summarize_metadata()` output per run and diff |
| Inventory reporting | Aggregate `_metadata_summary.json` across environments |
| Concurrency | Parallelise the loop in `extract_metadata_for_apps()`; guard the two summary dictionary writes |

---

*High-level design for `QlikAppMetadataExtractor.py` (357 lines) — July 2026.*

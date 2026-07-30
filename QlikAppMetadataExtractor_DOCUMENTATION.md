# Qlik Sense Metadata Extractor — End-to-End Documentation

This guide explains how the script `QlikAppMetadataExtractor.py` works from start to finish. It is written for someone with **basic coding experience** — you should be comfortable running Python scripts and editing JSON files, but you do not need to be an expert.

> **Companion script:** `QlikAppDataExtractor.py` extracts the *actual data rows* from a Qlik app. This script extracts the *metadata* — the description of fields, tables, sizes, and structure. See [Section 13](#13-how-this-differs-from-the-data-extractor) for a comparison.

---

## Table of Contents

1. [What Does This Script Do?](#1-what-does-this-script-do)
2. [How It Fits Together (Big Picture)](#2-how-it-fits-together-big-picture)
3. [Before You Run It](#3-before-you-run-it)
4. [Configuration File Explained](#4-configuration-file-explained)
5. [How to Run the Script](#5-how-to-run-the-script)
6. [Step-by-Step: What Happens When You Run It](#6-step-by-step-what-happens-when-you-run-it)
7. [Function-by-Function Breakdown](#7-function-by-function-breakdown)
8. [Output Files Explained](#8-output-files-explained)
9. [Extracting Multiple Applications](#9-extracting-multiple-applications)
10. [Logs and Troubleshooting](#10-logs-and-troubleshooting)
11. [Glossary](#11-glossary)
12. [Line-by-Line Explanation](#12-line-by-line-explanation)
13. [How This Differs From the Data Extractor](#13-how-this-differs-from-the-data-extractor)
14. [What Changed From the Original Script](#14-what-changed-from-the-original-script)

---

## 1. What Does This Script Do?

In simple terms, this script:

1. **Logs in** to a Qlik Sense server using certificates and a user identity.
2. **Requests metadata** for one or more Qlik applications over a REST API.
3. **Summarises** the metadata (how many fields, how many tables, total rows, sizes).
4. **Saves** each application's metadata as a JSON file on your computer.
5. **Writes a summary** file covering every application processed.
6. **Logs out** and closes the connection cleanly.

Think of it as asking Qlik for the *table of contents and index* of an application, rather than the contents of every page.

### What "metadata" means here

Metadata is information **about** the data, not the data itself:

| Metadata tells you | Metadata does NOT tell you |
|--------------------|----------------------------|
| Which tables exist | The values inside those tables |
| Which fields exist and their tags | Individual row values |
| How many rows each table has | What those rows contain |
| Memory footprint of each table | Customer names, amounts, dates |
| Whether section access is enabled | — |

---

## 2. How It Fits Together (Big Picture)

The script talks to two different parts of Qlik Sense, both over regular HTTPS:

| Part | What it is | What the script uses it for |
|------|------------|-----------------------------|
| **QPS (Qlik Proxy Service)** | Handles user sessions (login/logout) | Creating and deleting a session |
| **Qlik REST API** | Serves app information over HTTPS | Fetching the metadata document |

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR COMPUTER                            │
│                                                                 │
│   config.json  ──►  QlikAppMetadataExtractor.py                 │
│                          │                                      │
└──────────────────────────┼──────────────────────────────────────┘
                           │
                           ▼
                ┌──────────────────────────┐
                │     Qlik Sense Server    │
                │                          │
                │  1. Create session       │ ◄── HTTPS + certificates
                │  2. GET .../metadata     │ ◄── HTTPS + session cookie
                │     (once per app)       │
                │  3. Delete session       │ ◄── HTTPS + certificates
                └──────────────────────────┘
                           │
                           ▼
                ┌──────────────────────────────┐
                │   metadata_output/ folder    │
                │   - <guid>_metadata.json     │
                │   - _metadata_summary.json   │
                └──────────────────────────────┘
```

> **Note:** Unlike the data extractor, this script uses **no WebSocket connection**. Metadata comes from a single REST call per application, which makes it much faster and lighter.

---

## 3. Before You Run It

### 3.1 Required Software

- **Python 3.7 or newer** installed on your machine.
- Two Python packages (install with pip):

```bash
pip install requests urllib3
```

| Package | Purpose |
|---------|---------|
| `requests` | Sends all HTTPS requests (sessions and metadata) |
| `urllib3` | Provides the retry logic; warnings are silenced in the script |

### 3.2 Required Files

Place these in the **same folder** as the script (or adjust paths in config):

| File | Purpose |
|------|---------|
| `QlikAppMetadataExtractor.py` | The main script |
| `config.json` | Settings (server URL, app ID, certificates, etc.) |
| Client certificate (`.pem`) | Proves your identity to Qlik |
| Client key (`.pem` or similar) | Private key paired with the certificate |

### 3.3 Folders Created Automatically

When you run the script, it creates:

| Folder | Contents |
|--------|----------|
| `metadata_output/` | One JSON file per application, plus a summary file |
| `log/` | Timestamped log files for each run |

---

## 4. Configuration File Explained

The script reads settings from `config.json`. Here is a working example:

```json
{
  "user_id": "jdoe",
  "user_directory": "COMPANY",
  "proxy_server": "https://qlik-server.company.com",
  "client_cert": "/path/to/client.pem",
  "client_key": "/path/to/client_key.pem",
  "url": "qlik-server.company.com",
  "xrfkey": "1234567890123456",
  "cookie_name": "X-Qlik-Session-company",
  "app_guid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Required Settings

| Key | What it means | Example |
|-----|---------------|---------|
| `user_id` | Your Qlik username | `"jdoe"` |
| `user_directory` | The directory/realm your user belongs to | `"COMPANY"` |
| `proxy_server` | Full URL of the Qlik Proxy (QPS) | `"https://qlik.company.com"` |
| `client_cert` | Path to your client certificate file | `"/certs/client.pem"` |
| `client_key` | Path to your client private key file | `"/certs/client_key.pem"` |
| `url` | Qlik server hostname (no `https://`) | `"qlik.company.com"` |
| `xrfkey` | A 16-character security key used in Qlik API calls | `"1234567890123456"` |
| `cookie_name` | Name of the session cookie Qlik uses | `"X-Qlik-Session-company"` |

Plus **one of** `app_guid` or `app_guids` (see below).

### Application Selection

| Key | Type | What it does |
|-----|------|--------------|
| `app_guid` | string | Fetch metadata for a single application |
| `app_guids` | list of strings | Fetch metadata for several applications in one run |

If both are present, `app_guids` wins. If neither is set, the script stops with a clear error.

### Optional Settings

| Key | Default | What it does |
|-----|---------|--------------|
| `output_dir` | `"metadata_output"` | Folder where JSON files are saved |
| `request_timeout` | `120` | Seconds to wait for a metadata response |
| `retries` | `3` | How many times to retry a failed request |
| `save_raw_response` | `false` | If `true`, also saves Qlik's unmodified response |
| `metadata_use_client_cert` | `false` | If `true`, sends client certificates on the metadata call too |

> **Tip:** Leave `metadata_use_client_cert` off unless your server requires mutual TLS on the REST API. The session cookie is normally enough.

---

## 5. How to Run the Script

1. Open a terminal (Command Prompt, PowerShell, or Terminal on Mac/Linux).
2. Navigate to the folder containing the script:

```bash
cd /path/to/your/script/folder
```

3. Run the script:

```bash
python QlikAppMetadataExtractor.py
```

Or on some systems:

```bash
python3 QlikAppMetadataExtractor.py
```

4. Watch the terminal for progress messages. When finished, check the `metadata_output/` folder.

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | Success — every application's metadata was retrieved |
| `1` | Something failed — check the logs and summary file |

---

## 6. Step-by-Step: What Happens When You Run It

### Phase 1 — Setup

```
main()
  └── setup_logger()          → Creates log file in log/ folder
  └── load_config()           → Reads config.json
  └── Validates required keys → Stops if anything is missing
  └── resolve_app_guids()     → Builds the list of apps to process
```

### Phase 2 — Prepare Connection

```
build_http_session()
  └── Creates a pooled, retrying HTTPS session
  └── Reused for login, every metadata call, and logout
```

### Phase 3 — Authentication

```
generate_session()
  └── Creates a unique session ID (UUID)
  └── Sends POST request to Qlik Proxy with your certificate
  └── Qlik returns a valid session
```

### Phase 4 — Build Request Headers

```
build_headers()
  └── Combines session ID, user info, and security keys
  └── Produces HTTP headers the metadata call needs
```

### Phase 5 — Fetch Metadata (the main work)

```
extract_metadata_for_apps()
  │
  └── For each application:
        ├── fetch_app_metadata()      → GET the metadata endpoint
        ├── build_metadata_document() → Adds context and table summary
        ├── save_json()               → Saves <guid>_metadata.json
        └── summarize_metadata()      → Records counts for the summary
  │
  └── Writes _metadata_summary.json
```

If one application fails, the loop continues with the rest. Failures are recorded rather than crashing the run.

### Phase 6 — Cleanup

```
delete_session()
  └── Sends DELETE request to Qlik Proxy
  └── Frees the session on the server

http_session.close()
  └── Releases network connections
```

Cleanup happens inside a `finally` block, so it runs even if extraction failed.

---

## 7. Function-by-Function Breakdown

---

### `build_http_session(retries, pool_size)`

**Purpose:** Creates the single HTTPS session object used for every request in the run.

**Why it matters:**
- **Connection reuse** — the TLS handshake happens once instead of on every call
- **Automatic retries** — transient errors (429, 500, 502, 503, 504) are retried with increasing delays
- **Connection pooling** — keeps up to 10 connections ready

**Retry behavior:** Waits progressively longer between attempts (`backoff_factor=0.5`), so a busy server gets time to recover.

---

### `generate_session(session, xrfkey, user_id, user_directory, proxy_server, client_cert, client_key)`

**Purpose:** Logs into Qlik via the Proxy API and returns a session ID.

**How:** Generates a random UUID, then POSTs it to `/qps/session` with your client certificate.

**Why certificates?** Qlik Sense enterprise setups typically use mutual TLS — both sides prove their identity.

---

### `delete_session(session, xrfkey, proxy_server, client_cert, client_key, session_id)`

**Purpose:** Logs out and frees the session on the server.

**Why it matters:** Leaving sessions open consumes server resources and license slots.

---

### `fetch_app_metadata(session, host, xrfkey, app_guid, headers, cert, timeout)`

**Purpose:** Calls the metadata REST endpoint and returns the parsed JSON.

**Endpoint used:**

```
https://{host}/api/v1/apps/{app_guid}/data/metadata?xrfkey={xrfkey}
```

**Returns:** A Python dictionary (not a raw response object), so callers do not need to know about HTTP details.

**Error handling:**

| Situation | Result |
|-----------|--------|
| Network failure or HTTP error | Logs the URL and error, then raises |
| Response is not valid JSON | Raises a clear message including the HTTP status |

---

### `safe_filename(name)`

**Purpose:** Converts an app GUID or name into a filename that is valid on Windows, macOS, and Linux.

**How:** Replaces characters like `/`, `:`, `?`, and `*` with underscores. Falls back to `"unnamed_app"` if nothing usable remains.

---

### `save_json(name, payload, output_dir)`

**Purpose:** Writes any Python dictionary to a formatted JSON file.

**Details:** Creates the output folder if needed, uses UTF-8 encoding, and indents by 4 spaces for readability. `ensure_ascii=False` keeps non-English characters readable rather than escaped.

---

### `summarize_metadata(data)`

**Purpose:** Pulls headline numbers out of a metadata response for the run summary.

**Returns:**

| Field | Meaning |
|-------|---------|
| `status` | Always `"success"` (failures are recorded separately) |
| `fieldCount` | Number of fields in the app |
| `tableCount` | Number of tables in the app |
| `totalRowCount` | Sum of rows across all tables |
| `staticByteSize` | Approximate in-memory size of the app |
| `hasSectionAccess` | Whether section access (row-level security) is enabled |
| `usage` | App usage type reported by Qlik |

**Safety:** Handles missing or `null` keys without crashing — an empty response produces zeroes rather than an error.

---

### `build_metadata_document(app_guid, data)`

**Purpose:** Wraps Qlik's raw response with extra context so the saved file explains itself.

**Adds:**
- `appGuid` and `extractedAt` (UTC timestamp)
- `fieldCount` and `tableCount`
- `tableSummary` — a flat, readable list of tables with row counts and sizes
- `metadata` — the complete original response, unmodified

**Why keep the raw response?** So nothing is lost. The summary is for humans; the raw block is for tooling.

---

### `resolve_app_guids(config)`

**Purpose:** Decides which applications to process.

**Logic:**

| Config present | Result |
|----------------|--------|
| `app_guids: ["a", "b"]` | Process both |
| `app_guids: "a"` (single string by mistake) | Wrapped into a list automatically |
| `app_guid: "a"` | Process just that one |
| Neither | Returns an empty list → script exits with a clear error |

---

### `extract_metadata_for_apps(session, config, headers, app_guids)`

**Purpose:** The orchestrator — loops over the applications and produces all output files.

**Key behaviors:**
- One application failing does not stop the others
- Every result is recorded under `apps` or `failures`
- Always writes `_metadata_summary.json`, even if everything failed

---

### `setup_logger()`

**Purpose:** Configures logging to both the terminal and a timestamped file in `log/`.

**Example log file:** `log/20260730-094500Z-metadata.log`

> The `-metadata` suffix keeps these logs distinct from the data extractor's logs when both scripts share a folder.

---

### `load_config(file_path)`

**Purpose:** Reads and parses `config.json`.

**Error handling:** Clear, specific messages for a missing file, a permissions problem, or invalid JSON.

---

### `build_headers(config, session_id)`

**Purpose:** Builds the HTTP headers for the metadata call.

**Two headers matter most:**

| Header | Why it is critical |
|--------|--------------------|
| `Cookie` | Carries your session ID — proves you are logged in |
| `X-Qlik-User` | Tells Qlik which user identity to apply |

---

### `main()`

**Purpose:** The entry point — ties everything together in the correct order.

**Flow:** Setup → Load config → Validate → Resolve apps → Create session → Fetch metadata → Delete session → Exit with status code.

---

## 8. Output Files Explained

### Per-Application Metadata File

Each application is saved as `metadata_output/{app_guid}_metadata.json`:

```json
{
  "appGuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "extractedAt": "2026-07-30T09:45:00+00:00",
  "fieldCount": 87,
  "tableCount": 12,
  "tableSummary": [
    {
      "name": "Sales",
      "rowCount": 50000,
      "fieldCount": 8,
      "keyFieldCount": 2,
      "byteSize": 4194304,
      "isSystem": false
    },
    {
      "name": "Customers",
      "rowCount": 1200,
      "fieldCount": 6,
      "keyFieldCount": 1,
      "byteSize": 98304,
      "isSystem": false
    }
  ],
  "metadata": {
    "fields": [ "... full Qlik field list ..." ],
    "tables": [ "... full Qlik table list ..." ],
    "static_byte_size": 8388608,
    "has_section_access": false,
    "usage": "ANALYTICS"
  }
}
```

| Field | Meaning |
|-------|---------|
| `appGuid` | Which application this file describes |
| `extractedAt` | When the metadata was captured (UTC) |
| `fieldCount` | Total number of fields in the app |
| `tableCount` | Total number of tables in the app |
| `tableSummary` | Readable per-table overview |
| `metadata` | Qlik's complete unmodified response |

### Summary File

`metadata_output/_metadata_summary.json` covers the whole run:

```json
{
  "extractedAt": "2026-07-30T09:45:00+00:00",
  "appCount": 2,
  "apps": {
    "a1b2c3d4-...": {
      "status": "success",
      "fieldCount": 87,
      "tableCount": 12,
      "totalRowCount": 51200,
      "staticByteSize": 8388608,
      "hasSectionAccess": false,
      "usage": "ANALYTICS"
    }
  },
  "failures": {
    "b6a9abde-...": "404 Client Error: Not Found"
  }
}
```

Check `failures` first after any run — it names the application and the exact reason.

### Optional Raw File

If `save_raw_response` is `true`, you also get `{app_guid}_metadata_raw.json` containing Qlik's response with no wrapper at all. Useful when comparing against the API directly.

---

## 9. Extracting Multiple Applications

### One Application

```json
{
  "app_guid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### Several Applications

```json
{
  "app_guids": [
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "b6a9abde-d316-4f71-a566-c91136e99257",
    "c7d8e9f0-1234-5678-90ab-cdef12345678"
  ]
}
```

All applications share **one login session**, so processing ten apps costs one session instead of ten.

### Tuning for Slow Servers

```json
{
  "request_timeout": 300,
  "retries": 5
}
```

### Keeping Qlik's Raw Response

```json
{
  "save_raw_response": true
}
```

---

## 10. Logs and Troubleshooting

### Where to Look

| Location | What it tells you |
|----------|-------------------|
| Terminal output | Real-time progress |
| `log/YYYYMMDD-HHMMSSZ-metadata.log` | Full history of the run |
| `metadata_output/_metadata_summary.json` | Which apps succeeded or failed |

### Common Problems

| Problem | Likely Cause | What to Check |
|---------|--------------|---------------|
| `Configuration file 'config.json' not found` | Missing config file | File is in the folder you run the script from |
| `Missing required configuration keys` | A required field is empty | Review Section 4 |
| `No application specified` | Neither `app_guid` nor `app_guids` set | Add one of them to the config |
| `Failed to create session` | Wrong certificates, user, or proxy URL | Verify cert paths, `user_id`, `proxy_server` |
| `401` or `403` on metadata | Session rejected, or user lacks access | Check `cookie_name`, `xrfkey`, and app permissions |
| `404` on metadata | Wrong `app_guid`, or app not published | Confirm the GUID in the Qlik Management Console |
| `was not valid JSON` | Server returned HTML (often a login page) | Session likely expired or was rejected |
| Request times out | Very large app or slow server | Increase `request_timeout` |

### Tips

- Run the script from the folder where `config.json` lives, or pass a full path.
- GUIDs are exact — copy them from the Qlik Management Console rather than typing them.
- A failing app in a multi-app run does not stop the others; check `failures` in the summary.
- Retries are automatic for transient server errors, so a single blip will not fail the run.

---

## 11. Glossary

| Term | Simple Explanation |
|------|--------------------|
| **App GUID** | A unique ID that identifies one Qlik application (like a serial number) |
| **Metadata** | Information *about* the data — table names, field names, row counts, sizes |
| **REST API** | A way of requesting information over normal HTTPS URLs |
| **QPS (Qlik Proxy Service)** | The front door to Qlik — handles authentication and routing |
| **Session** | A temporary authenticated connection tied to your user identity |
| **XRF Key** | A 16-character key Qlik requires on API calls for security |
| **Client Certificate** | A digital ID file that proves who you are to the server |
| **Connection pooling** | Reusing open network connections instead of creating new ones |
| **Retry / backoff** | Automatically trying again after a failure, waiting longer each time |
| **Section access** | Qlik's row-level security feature |
| **Exit code** | A number a program returns to say whether it succeeded (`0`) or failed (`1`) |

---

## 12. Line-by-Line Explanation

This section walks through **every line** of `QlikAppMetadataExtractor.py` (357 lines). Read it alongside the script.

**How to read this section:**
- **Line number** — matches the line in the `.py` file
- **Code** — the actual line (shortened if very long)
- **Explanation** — what that line does in plain English

---

### Section A — Imports and Global Setup (Lines 1–19)

| Line | Code | Explanation |
|------|------|-------------|
| 1 | `import json` | Loads the JSON library — used to read config and write output files. |
| 2 | `import logging` | Loads Python's logging system for terminal and file messages. |
| 3 | `import re` | Loads regular expressions — used to clean up filenames. |
| 4 | `import uuid` | Used to generate a unique session ID when logging in. |
| 5 | `from datetime import datetime, timezone` | Tools for UTC timestamps in logs and output. |
| 6 | `from pathlib import Path` | Modern way to handle file and folder paths. |
| 7 | *(blank)* | Separates standard-library imports from third-party ones. |
| 8 | `import requests` | The library used for all HTTPS calls. |
| 9 | `import urllib3` | Used for retry configuration and warning suppression. |
| 10 | `from requests.adapters import HTTPAdapter` | Lets us attach retry and pooling settings to a session. |
| 11 | `from urllib3.util.retry import Retry` | Defines how failed requests should be retried. |
| 12 | *(blank)* | Blank line. |
| 13 | `urllib3.disable_warnings(...)` | Hides SSL warnings caused by `verify=False`. |
| 14 | *(blank)* | Blank line. |
| 15 | `LOGGER_NAME = "metadata_extractor"` | Constant name for this script's logger. |
| 16 | `DEFAULT_TIMEOUT = 120` | Default seconds to wait for a metadata response. |
| 17 | `DEFAULT_RETRIES = 3` | Default number of retry attempts. |
| 18 | `logger = logging.getLogger(LOGGER_NAME)` | Creates the logger object used throughout. |
| 19 | `logger.addHandler(logging.NullHandler())` | Prevents errors if something logs before `setup_logger()` runs. |

---

### Section B — `build_http_session` (Lines 22–35)

| Line | Code | Explanation |
|------|------|-------------|
| 22 | `def build_http_session(retries=DEFAULT_RETRIES, pool_size=10):` | Defines the function that creates the shared HTTPS session. |
| 23 | `"""Create a connection-pooled session..."""` | Docstring describing the purpose. |
| 24 | `session = requests.Session()` | Creates a session object that reuses connections. |
| 25 | `retry = Retry(` | Starts configuring retry behavior. |
| 26 | `total=retries,` | Maximum number of retry attempts. |
| 27 | `backoff_factor=0.5,` | Wait longer between each retry (0.5s, 1s, 2s, ...). |
| 28 | `status_forcelist=[429, 500, 502, 503, 504],` | Which HTTP errors are worth retrying (rate limits and server errors). |
| 29 | `allowed_methods=frozenset(["GET", "POST", "DELETE"]),` | Which HTTP methods may be retried. |
| 30 | `raise_on_status=False,` | Let our own code handle the final error, not the retry layer. |
| 31 | `)` | Closes the Retry configuration. |
| 32 | `adapter = HTTPAdapter(max_retries=retry, pool_connections=..., pool_maxsize=...)` | Bundles retries and connection pooling together. |
| 33 | `session.mount("https://", adapter)` | Applies the adapter to all HTTPS requests. |
| 34 | `session.mount("http://", adapter)` | Applies it to HTTP too, for completeness. |
| 35 | `return session` | Returns the ready-to-use session. |

---

### Section C — `generate_session` (Lines 38–65)

| Line | Code | Explanation |
|------|------|-------------|
| 38 | `def generate_session(session, xrfkey, user_id, ...):` | Defines the login function. Takes the shared session as its first argument. |
| 39 | `session_id = str(uuid.uuid4())` | Generates a random unique session ID. |
| 40 | `session_url = f"{proxy_server}/qps/session?xrfkey={xrfkey}"` | Builds the session-creation URL. |
| 41–44 | `session_headers = {...}` | Headers carrying the XRF key and content type. |
| 45–50 | `session_payload = {...}` | Request body identifying the user and session ID. |
| 51–58 | `resp = session.post(...)` | Sends the login request with client certificates, a 30-second timeout, and TLS verification disabled. |
| 59 | `try:` | Begin checking whether the request succeeded. |
| 60 | `resp.raise_for_status()` | Raises an error on HTTP 4xx or 5xx. |
| 61 | `except requests.RequestException as e:` | Catches HTTP and network failures. |
| 62 | `logger.error(...)` | Logs which URL failed and why. |
| 63 | `raise` | Re-raises — the script cannot continue without a session. |
| 64 | `logger.info("Session created: %s", session_id)` | Confirms success in the log. |
| 65 | `return session_id` | Returns the ID for use in headers and cleanup. |

---

### Section D — `delete_session` (Lines 68–86)

| Line | Code | Explanation |
|------|------|-------------|
| 68 | `def delete_session(session, xrfkey, proxy_server, ...):` | Defines the logout function. |
| 69 | `session_url = f"{proxy_server}/qps/session/{session_id}?xrfkey={xrfkey}"` | URL pointing at the specific session to remove. |
| 70–73 | `session_headers = {...}` | Same headers used at login. |
| 74–80 | `resp = session.delete(...)` | Sends the DELETE request with certificates and a timeout. |
| 81 | `try:` | Check the response. |
| 82 | `resp.raise_for_status()` | Raise on HTTP failure. |
| 83 | `except requests.RequestException as e:` | Catch failures. |
| 84 | `logger.error(...)` | Log which session could not be deleted. |
| 85 | `raise` | Re-raise so the caller can decide whether to warn. |
| 86 | `logger.info("Deleted session: %s", session_id)` | Log successful cleanup. |

---

### Section E — `fetch_app_metadata` (Lines 89–104)

This is the core API call.

| Line | Code | Explanation |
|------|------|-------------|
| 89 | `def fetch_app_metadata(session, host, xrfkey, app_guid, headers, cert=None, timeout=DEFAULT_TIMEOUT):` | Defines the metadata fetcher. `cert` and `timeout` have sensible defaults. |
| 90 | `"""Call the Qlik metadata REST endpoint..."""` | Docstring. |
| 91 | `url = f"https://{host}/api/v1/apps/{app_guid}/data/metadata?xrfkey={xrfkey}"` | Builds the metadata endpoint URL for this app. |
| 92 | `try:` | Begin the request attempt. |
| 93 | `resp = session.get(url, headers=headers, verify=False, timeout=timeout, cert=cert)` | Sends the GET request using the shared, retrying session. |
| 94 | `resp.raise_for_status()` | Raise on HTTP 4xx or 5xx. |
| 95 | `except requests.RequestException as e:` | Catch network and HTTP errors. |
| 96 | `logger.error("Metadata request failed for app %s at %s: %s", ...)` | Log the app, URL, and error. |
| 97 | `raise` | Pass the error up so the caller can record the failure. |
| 98 | *(blank)* | Blank line. |
| 99 | `try:` | Begin parsing the response body. |
| 100 | `return resp.json()` | Parse JSON and return it as a dictionary. |
| 101 | `except ValueError as e:` | The body was not valid JSON (often an HTML login page). |
| 102–104 | `raise RuntimeError(...) from e` | Raise a clear message including the HTTP status code. |

---

### Section F — `safe_filename` (Lines 107–108)

| Line | Code | Explanation |
|------|------|-------------|
| 107 | `def safe_filename(name):` | Defines the filename sanitiser. |
| 108 | `return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip() or "unnamed_app"` | Replaces illegal filename characters with `_`, trims spaces, and falls back to `"unnamed_app"` if nothing is left. |

---

### Section G — `save_json` (Lines 111–117)

| Line | Code | Explanation |
|------|------|-------------|
| 111 | `def save_json(name, payload, output_dir):` | Defines the file-writing helper. |
| 112 | `output_dir.mkdir(parents=True, exist_ok=True)` | Creates the output folder (and parents) if missing. |
| 113 | `file_path = output_dir / f"{safe_filename(name)}.json"` | Builds the full output path. |
| 114 | `with open(file_path, "w", encoding="utf-8") as f:` | Opens the file for writing; closes automatically. |
| 115 | `json.dump(payload, f, indent=4, ensure_ascii=False)` | Writes readable, indented JSON preserving non-English characters. |
| 116 | `logger.info("Saved %s", file_path)` | Logs the saved path. |
| 117 | `return file_path` | Returns the path to the caller. |

---

### Section H — `summarize_metadata` (Lines 120–133)

| Line | Code | Explanation |
|------|------|-------------|
| 120 | `def summarize_metadata(data):` | Defines the summary builder. |
| 121 | `"""Extract headline counts..."""` | Docstring. |
| 122 | `fields = data.get("fields") or []` | Gets the field list; empty list if missing **or** `null`. |
| 123 | `tables = data.get("tables") or []` | Same for tables. |
| 124 | *(blank)* | Blank line. |
| 125 | `return {` | Start building the summary. |
| 126 | `"status": "success",` | Marks this app as successful. |
| 127 | `"fieldCount": len(fields),` | How many fields exist. |
| 128 | `"tableCount": len(tables),` | How many tables exist. |
| 129 | `"totalRowCount": sum(t.get("no_of_rows", 0) for t in tables if isinstance(t, dict)),` | Adds up rows across tables, skipping malformed entries. |
| 130 | `"staticByteSize": data.get("static_byte_size"),` | Approximate memory size of the app. |
| 131 | `"hasSectionAccess": data.get("has_section_access"),` | Whether row-level security is enabled. |
| 132 | `"usage": data.get("usage"),` | Usage type reported by Qlik. |
| 133 | `}` | Close and return the summary. |

---

### Section I — `build_metadata_document` (Lines 136–159)

| Line | Code | Explanation |
|------|------|-------------|
| 136 | `def build_metadata_document(app_guid, data):` | Defines the output-document builder. |
| 137 | `"""Wrap the raw Qlik response..."""` | Docstring explaining the purpose. |
| 138 | `fields = data.get("fields") or []` | Safe field list. |
| 139 | `tables = data.get("tables") or []` | Safe table list. |
| 140 | *(blank)* | Blank line. |
| 141 | `return {` | Start building the document. |
| 142 | `"appGuid": app_guid,` | Records which app this describes. |
| 143 | `"extractedAt": datetime.now(timezone.utc).isoformat(),` | UTC timestamp of the extraction. |
| 144 | `"fieldCount": len(fields),` | Field total. |
| 145 | `"tableCount": len(tables),` | Table total. |
| 146 | `"tableSummary": [` | Begin a readable per-table overview. |
| 147–154 | `{ "name": ..., "rowCount": ..., ... }` | Pulls name, row count, field count, key fields, byte size, and system flag from each table. |
| 155–156 | `for t in tables if isinstance(t, dict)` | Loops through tables, skipping anything unexpected. |
| 157 | `],` | Close the table summary list. |
| 158 | `"metadata": data,` | Keeps Qlik's complete original response. |
| 159 | `}` | Close and return the document. |

---

### Section J — `resolve_app_guids` (Lines 162–172)

| Line | Code | Explanation |
|------|------|-------------|
| 162 | `def resolve_app_guids(config):` | Defines the app-selection resolver. |
| 163 | `"""Return the list of app GUIDs..."""` | Docstring. |
| 164 | `explicit_list = config.get("app_guids")` | Reads the optional multi-app list. |
| 165 | `single_app = config.get("app_guid")` | Reads the optional single app. |
| 166 | *(blank)* | Blank line. |
| 167 | `if explicit_list:` | If a list was provided... |
| 168 | `if isinstance(explicit_list, str):` | ...and it is actually a single string... |
| 169 | `explicit_list = [explicit_list]` | ...wrap it in a list so the rest of the code works. |
| 170 | `return [g for g in explicit_list if g]` | Return the list, dropping empty entries. |
| 171 | *(blank)* | Blank line. |
| 172 | `return [single_app] if single_app else []` | Otherwise return the single app, or an empty list. |

---

### Section K — `extract_metadata_for_apps` (Lines 175–219)

The orchestrator that produces all output.

| Line | Code | Explanation |
|------|------|-------------|
| 175 | `def extract_metadata_for_apps(session, config, headers, app_guids):` | Defines the main loop function. |
| 176–177 | `host = config["url"]` / `xrfkey = config["xrfkey"]` | Read connection details. |
| 178 | `output_dir = Path(config.get("output_dir", "metadata_output"))` | Output folder, with a default. |
| 179 | `timeout = config.get("request_timeout", DEFAULT_TIMEOUT)` | Per-request timeout. |
| 180 | `save_raw = config.get("save_raw_response", False)` | Whether to also save the unmodified response. |
| 181 | *(blank)* | Blank line. |
| 182 | `cert = None` | Default: do not send client certificates on the metadata call. |
| 183 | `if config.get("metadata_use_client_cert"):` | If the config opts in... |
| 184 | `cert = (config["client_cert"], config["client_key"])` | ...attach the certificate pair. |
| 185 | *(blank)* | Blank line. |
| 186–191 | `summary = {...}` | Build the run summary skeleton (timestamp, app count, results, failures). |
| 192 | *(blank)* | Blank line. |
| 193 | `for app_guid in app_guids:` | Loop over every requested application. |
| 194 | `logger.info("Fetching metadata for app %s", app_guid)` | Log which app is being processed. |
| 195 | `try:` | Isolate this app so a failure does not stop the run. |
| 196 | `data = fetch_app_metadata(...)` | Call the REST endpoint. |
| 197 | `document = build_metadata_document(app_guid, data)` | Wrap it with context and a table summary. |
| 198 | `save_json(f"{app_guid}_metadata", document, output_dir)` | Save the main output file. |
| 199 | `if save_raw:` | If raw output was requested... |
| 200 | `save_json(f"{app_guid}_metadata_raw", data, output_dir)` | ...save Qlik's untouched response too. |
| 201 | *(blank)* | Blank line. |
| 202 | `summary["apps"][app_guid] = summarize_metadata(data)` | Record success details in the summary. |
| 203–208 | `logger.info("App %s: %s field(s), %s table(s)", ...)` | Log the headline counts. |
| 209 | `except Exception as e:` | Catch any failure for this one app. |
| 210 | `logger.error("Failed to retrieve metadata for app %s: %s", ...)` | Log the reason. |
| 211 | `summary["failures"][app_guid] = str(e)` | Record it in the failures section. |
| 212 | *(blank)* | Blank line. |
| 213 | `save_json("_metadata_summary", summary, output_dir)` | Write the run summary file. |
| 214–218 | `logger.info("Metadata extraction complete: ...")` | Log succeeded and failed counts. |
| 219 | `return summary` | Return the summary so `main()` can set the exit code. |

---

### Section L — `setup_logger` (Lines 222–245)

| Line | Code | Explanation |
|------|------|-------------|
| 222 | `def setup_logger():` | Defines the logging configuration function. |
| 223 | `global logger` | Says we will replace the module-level logger from line 18. |
| 224 | `log_dir = Path(__file__).resolve().parent / "log"` | Path to a `log/` folder beside the script. |
| 225 | `log_dir.mkdir(parents=True, exist_ok=True)` | Create it if missing. |
| 226 | `timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")` | Build a timestamp like `20260730-094500Z`. |
| 227 | `log_file = log_dir / f"{timestamp}-metadata.log"` | Log filename, suffixed to distinguish it from other scripts. |
| 228 | *(blank)* | Blank line. |
| 229 | `formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")` | Log line format: time, level, message. |
| 230 | *(blank)* | Blank line. |
| 231–232 | File handler setup | Send log messages to the file. |
| 233 | *(blank)* | Blank line. |
| 234–235 | Stream handler setup | Send log messages to the terminal. |
| 236 | *(blank)* | Blank line. |
| 237 | `logger = logging.getLogger(LOGGER_NAME)` | Fetch the named logger. |
| 238 | `logger.setLevel(logging.INFO)` | Log INFO and above. |
| 239 | `logger.handlers.clear()` | Remove old handlers, including the NullHandler. |
| 240–241 | `logger.addHandler(...)` | Attach the file and terminal handlers. |
| 242 | `logger.propagate = False` | Prevent duplicate messages via the root logger. |
| 243 | *(blank)* | Blank line. |
| 244 | `logger.info("Logging initialized. Output file: %s", log_file)` | Record where logs are written. |
| 245 | `return logger` | Return the configured logger. |

---

### Section M — `load_config` (Lines 248–260)

| Line | Code | Explanation |
|------|------|-------------|
| 248 | `def load_config(file_path="config.json"):` | Reads the config file; defaults to `config.json`. |
| 249 | `try:` | Begin the read attempt. |
| 250–251 | `with open(...)` / `return json.load(f)` | Open the file and parse it into a dictionary. |
| 252–254 | `except FileNotFoundError:` | Log a clear message and re-raise if the file is missing. |
| 255–257 | `except PermissionError:` | Handle a permissions problem. |
| 258–260 | `except json.JSONDecodeError as e:` | Handle malformed JSON, including the parse error detail. |

---

### Section N — `build_headers` (Lines 263–288)

| Line | Code | Explanation |
|------|------|-------------|
| 263 | `def build_headers(config, session_id):` | Defines the header builder. |
| 264–268 | Read config values | Pull host, user directory, user ID, XRF key, and cookie name. |
| 269 | *(blank)* | Blank line. |
| 270 | `return {` | Start the headers dictionary. |
| 271–273 | `Accept` / `Accept-Encoding` / `Accept-Language` | Standard content negotiation headers. |
| 274 | `"Cookie": f"{cookie_name}={session_id}",` | **Critical:** proves you are logged in. |
| 275–276 | `Host` / `Origin` | Identify the target server and request origin. |
| 277–279 | `Sec-Fetch-*` | Browser-style security headers. |
| 280 | `"Connection": "keep-alive",` | Keep the TCP connection open for reuse. |
| 281–284 | `User-Agent` | Identifies the client as a Chrome browser. |
| 285 | `"X-Qlik-Xrfkey": xrfkey,` | Qlik's required security key header. |
| 286 | `"Content-Type": ...` | Declares JSON content. |
| 287 | `"X-Qlik-User": f"UserDirectory=...;UserId=...",` | **Critical:** tells Qlik which user identity to use. |
| 288 | `}` | Close the headers dictionary. |

---

### Section O — `main` (Lines 291–353)

| Line | Code | Explanation |
|------|------|-------------|
| 291 | `def main():` | The entry point function. |
| 292 | `setup_logger()` | Configure logging first, so everything after can log. |
| 293 | `logger.info("Starting Qlik application metadata extraction")` | Log the start of the run. |
| 294 | *(blank)* | Blank line. |
| 295–299 | Load config | Read `config.json`; exit with code 1 on failure. |
| 300 | *(blank)* | Blank line. |
| 301–311 | `required_keys = [...]` | List of configuration fields that must be present. |
| 312 | `missing = [k for k in required_keys if not config.get(k)]` | Find any missing or empty required values. |
| 313–315 | `if missing:` | Log the missing keys and exit with code 1. |
| 316 | *(blank)* | Blank line. |
| 317 | `app_guids = resolve_app_guids(config)` | Work out which applications to process. |
| 318–320 | `if not app_guids:` | Exit with a clear message if none were configured. |
| 321 | *(blank)* | Blank line. |
| 322 | `http_session = build_http_session(retries=config.get("retries", DEFAULT_RETRIES))` | Create the shared, retrying HTTPS session. |
| 323 | `session_id = None` | Track the Qlik session so cleanup knows whether to run. |
| 324 | *(blank)* | Blank line. |
| 325 | `try:` | Main work block. |
| 326–334 | `session_id = generate_session(...)` | Log into Qlik. |
| 335 | `headers = build_headers(config, session_id)` | Build request headers using the session. |
| 336 | `summary = extract_metadata_for_apps(...)` | Fetch and save metadata for every app. |
| 337 | `return 1 if summary["failures"] else 0` | Exit 1 if any app failed, otherwise 0. |
| 338–340 | `except Exception as e:` | Log any unexpected failure and exit with code 1. |
| 341 | `finally:` | Cleanup always runs, success or failure. |
| 342 | `if session_id:` | Only delete a session that was actually created. |
| 343–352 | `delete_session(...)` / `except` | Log out; warn rather than crash if logout fails. |
| 353 | `http_session.close()` | Release all pooled network connections. |

---

### Section P — Script Entry Point (Lines 356–357)

| Line | Code | Explanation |
|------|------|-------------|
| 356 | `if __name__ == "__main__":` | True only when the script is run directly, not imported. |
| 357 | `raise SystemExit(main())` | Run `main()` and exit with its return code (`0` or `1`). |

---

### Visual Flow Summary

```
Lines 356–357  →  Script starts
Lines 292–320  →  Setup logging, load config, validate, resolve apps
Line  322      →  Build pooled HTTPS session
Lines 326–334  →  Log into Qlik (generate_session)
Line  335      →  Build headers
Lines 175–219  →  Fetch metadata for each app (extract_metadata_for_apps)
  Lines 196     →  GET the metadata endpoint
  Lines 197–200 →  Build document and save JSON files
  Lines 202–211 →  Record success or failure
  Line  213     →  Write _metadata_summary.json
Lines 343–352  →  Log out (delete_session)
Line  353      →  Close network connections
Line  357      →  Exit with status code
```

---

## 13. How This Differs From the Data Extractor

Both scripts talk to the same Qlik server and share the same session logic, but they answer different questions.

| Aspect | `QlikAppMetadataExtractor.py` | `QlikAppDataExtractor.py` |
|--------|-------------------|------------------------------|
| **Question answered** | What is *in* this app? | What are the *values* in this app? |
| **Connection type** | REST over HTTPS | WebSocket to the Qlik Engine |
| **Qlik API used** | `/api/v1/apps/.../data/metadata` | `OpenDoc`, `GetTablesAndKeys`, `GetTableData` |
| **Requests per app** | 1 | 1 per table, plus pagination per 1000 rows |
| **Typical runtime** | Seconds | Minutes to hours for large apps |
| **Output folder** | `metadata_output/` | `output/` |
| **Output per app** | One file describing structure | One file per table containing rows |
| **Summary file** | `_metadata_summary.json` | `_extraction_summary.json` |
| **Config file** | `config.json` | `config.json` |
| **Multi-target support** | Multiple apps via `app_guids` | Multiple tables within one app |

### Using Them Together

A common workflow is to run the metadata script first as a quick, cheap survey:

1. Run `QlikAppMetadataExtractor.py` to see which tables exist and how large they are.
2. Review `tableSummary` to decide which tables are worth extracting.
3. Run `QlikAppDataExtractor.py` with `table_names` set to just those tables.

This avoids downloading millions of rows you do not need.

> **Config note:** Both scripts read the same `config.json` file and need the same connection details. You can keep one config file for both — each script simply ignores the keys it does not use.

---

## 14. What Changed From the Original Script

This section summarises the improvements made to the original version of `QlikAppMetadataExtractor.py`.

### New Capability: JSON Output

| Before | Now |
|--------|-----|
| Printed the entire metadata blob to the terminal with `print()` | Saves structured JSON files to `metadata_output/` |
| Nothing was kept after the terminal closed | Every run produces durable, timestamped output |
| No overview of results | Writes `_metadata_summary.json` with counts and failures |
| One app per run | Supports many apps in a single run and session |

### Bugs Fixed

| Issue | Impact | Fix |
|-------|--------|-----|
| `metadata()` accepted a `session_id` parameter it never used | Misleading signature — suggested the session was applied when it was not | Removed; authentication travels in the headers, which is now explicit |
| `metadata()` was always called without certificates, though it accepted them | The `client_cert` and `client_key` parameters were dead code | Replaced with an explicit `metadata_use_client_cert` config option |
| Returned a raw `requests` response object | Callers had to know about HTTP details and re-parse the body | Now returns a parsed dictionary |
| Non-JSON responses were only logged as a warning, then printed | A rejected session silently produced an HTML dump instead of an error | Raises a clear error naming the app and HTTP status |
| Logger was named `license_cleanup` | Log messages were attributed to an unrelated script | Renamed to `metadata_extractor` |
| Log filenames collided with other scripts sharing the folder | Runs could overwrite or interleave confusingly | Log files now end in `-metadata.log` |
| `timedelta` was imported but never used | Dead import | Removed |
| `main()` returned `None` on failure and `0` on success | Automation could not reliably detect failure | Returns `1` on failure, `0` on success, via `SystemExit` |
| A new `requests.Session()` was created inside each function | New TLS handshake per call; no connection reuse | One shared session is created once and passed in |
| No handling for a `null` `fields` or `tables` key | A malformed response would raise `TypeError` | Uses `or []` and skips non-dictionary entries |

### Performance Improvements

| Change | Benefit |
|--------|---------|
| Single pooled `requests.Session` for the whole run | One TLS handshake instead of one per request |
| `HTTPAdapter` with a connection pool of 10 | Connections are reused across apps |
| Automatic retries with exponential backoff | Transient 429/500/502/503/504 errors no longer fail the run |
| One login session shared across all apps | Ten apps cost one session instead of ten |
| Removed the large `print(json.dumps(...))` call | Terminal I/O no longer dominates runtime on big apps |
| Explicit `session.close()` in the `finally` block | Sockets are released promptly |

### Robustness Improvements

- One failing application no longer aborts the whole run; failures are recorded per app.
- Session cleanup runs in a `finally` block, so sessions are freed even after an error.
- Filenames are sanitised, so unusual GUIDs or names cannot produce invalid paths.
- Configuration validation reports **all** missing keys at once rather than failing on the first.
- Timeout and retry counts are configurable rather than hard-coded.

---

*Documentation version: matches `QlikAppMetadataExtractor.py` (357 lines) as of July 2026.*

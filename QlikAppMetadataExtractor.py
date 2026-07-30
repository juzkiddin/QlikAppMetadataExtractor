import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOGGER_NAME = "metadata_extractor"
DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 3
logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())


def build_http_session(retries=DEFAULT_RETRIES, pool_size=10):
    """Create a connection-pooled session that retries transient server errors."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "DELETE"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def generate_session(session, xrfkey, user_id, user_directory, proxy_server, client_cert, client_key):
    session_id = str(uuid.uuid4())
    session_url = f"{proxy_server}/qps/session?xrfkey={xrfkey}"
    session_headers = {
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json",
    }
    session_payload = {
        "UserDirectory": user_directory,
        "UserId": user_id,
        "Attributes": [],
        "SessionId": session_id,
    }
    resp = session.post(
        session_url,
        json=session_payload,
        headers=session_headers,
        cert=(client_cert, client_key),
        verify=False,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to create session at %s: %s", session_url, e)
        raise
    logger.info("Session created: %s", session_id)
    return session_id


def delete_session(session, xrfkey, proxy_server, client_cert, client_key, session_id):
    session_url = f"{proxy_server}/qps/session/{session_id}?xrfkey={xrfkey}"
    session_headers = {
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json",
    }
    resp = session.delete(
        session_url,
        headers=session_headers,
        cert=(client_cert, client_key),
        verify=False,
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to delete session %s: %s", session_id, e)
        raise
    logger.info("Deleted session: %s", session_id)


def fetch_app_metadata(session, host, xrfkey, app_guid, headers, cert=None, timeout=DEFAULT_TIMEOUT):
    """Call the Qlik metadata REST endpoint and return the parsed JSON body."""
    url = f"https://{host}/api/v1/apps/{app_guid}/data/metadata?xrfkey={xrfkey}"
    try:
        resp = session.get(url, headers=headers, verify=False, timeout=timeout, cert=cert)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Metadata request failed for app %s at %s: %s", app_guid, url, e)
        raise

    try:
        return resp.json()
    except ValueError as e:
        raise RuntimeError(
            f"Metadata response for app {app_guid} was not valid JSON (HTTP {resp.status_code})"
        ) from e


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip() or "unnamed_app"


def save_json(name, payload, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{safe_filename(name)}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
    logger.info("Saved %s", file_path)
    return file_path


def summarize_metadata(data):
    """Extract headline counts from a metadata response for the run summary."""
    fields = data.get("fields") or []
    tables = data.get("tables") or []

    return {
        "status": "success",
        "fieldCount": len(fields),
        "tableCount": len(tables),
        "totalRowCount": sum(t.get("no_of_rows", 0) for t in tables if isinstance(t, dict)),
        "staticByteSize": data.get("static_byte_size"),
        "hasSectionAccess": data.get("has_section_access"),
        "usage": data.get("usage"),
    }


def build_metadata_document(app_guid, data):
    """Wrap the raw Qlik response with extraction context so output files are self-describing."""
    fields = data.get("fields") or []
    tables = data.get("tables") or []

    return {
        "appGuid": app_guid,
        "extractedAt": datetime.now(timezone.utc).isoformat(),
        "fieldCount": len(fields),
        "tableCount": len(tables),
        "tableSummary": [
            {
                "name": t.get("name"),
                "rowCount": t.get("no_of_rows"),
                "fieldCount": t.get("no_of_fields"),
                "keyFieldCount": t.get("no_of_key_fields"),
                "byteSize": t.get("byte_size"),
                "isSystem": t.get("is_system"),
            }
            for t in tables
            if isinstance(t, dict)
        ],
        "metadata": data,
    }


def resolve_app_guids(config):
    """Return the list of app GUIDs to process (single app_guid or app_guids list)."""
    explicit_list = config.get("app_guids")
    single_app = config.get("app_guid")

    if explicit_list:
        if isinstance(explicit_list, str):
            explicit_list = [explicit_list]
        return [g for g in explicit_list if g]

    return [single_app] if single_app else []


def extract_metadata_for_apps(session, config, headers, app_guids):
    host = config["url"]
    xrfkey = config["xrfkey"]
    output_dir = Path(config.get("output_dir", "metadata_output"))
    timeout = config.get("request_timeout", DEFAULT_TIMEOUT)
    save_raw = config.get("save_raw_response", False)

    cert = None
    if config.get("metadata_use_client_cert"):
        cert = (config["client_cert"], config["client_key"])

    summary = {
        "extractedAt": datetime.now(timezone.utc).isoformat(),
        "appCount": len(app_guids),
        "apps": {},
        "failures": {},
    }

    for app_guid in app_guids:
        logger.info("Fetching metadata for app %s", app_guid)
        try:
            data = fetch_app_metadata(session, host, xrfkey, app_guid, headers, cert, timeout)
            document = build_metadata_document(app_guid, data)
            save_json(f"{app_guid}_metadata", document, output_dir)
            if save_raw:
                save_json(f"{app_guid}_metadata_raw", data, output_dir)

            summary["apps"][app_guid] = summarize_metadata(data)
            logger.info(
                "App %s: %s field(s), %s table(s)",
                app_guid,
                document["fieldCount"],
                document["tableCount"],
            )
        except Exception as e:
            logger.error("Failed to retrieve metadata for app %s: %s", app_guid, e)
            summary["failures"][app_guid] = str(e)

    save_json("_metadata_summary", summary, output_dir)
    logger.info(
        "Metadata extraction complete: %s succeeded, %s failed.",
        len(summary["apps"]),
        len(summary["failures"]),
    )
    return summary


def setup_logger():
    global logger
    log_dir = Path(__file__).resolve().parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    log_file = log_dir / f"{timestamp}-metadata.log"

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    logger.info("Logging initialized. Output file: %s", log_file)
    return logger


def load_config(file_path="config.json"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Configuration file '%s' not found.", file_path)
        raise
    except PermissionError:
        logger.error("Permission denied reading '%s'.", file_path)
        raise
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in '%s': %s", file_path, e)
        raise


def build_headers(config, session_id):
    host = config["url"]
    user_directory = config["user_directory"]
    user_id = config["user_id"]
    xrfkey = config["xrfkey"]
    cookie_name = config["cookie_name"]

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US",
        "Cookie": f"{cookie_name}={session_id}",
        "Host": host,
        "Origin": f"https://{host}",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        ),
        "X-Qlik-Xrfkey": xrfkey,
        "Content-Type": "application/json;charset=UTF-8",
        "X-Qlik-User": f"UserDirectory={user_directory};UserId={user_id}",
    }


def main():
    setup_logger()
    logger.info("Starting Qlik application metadata extraction")

    try:
        config = load_config()
    except Exception:
        logger.error("Cannot proceed without valid configuration. Exiting.")
        return 1

    required_keys = [
        "user_id",
        "user_directory",
        "proxy_server",
        "client_cert",
        "client_key",
        "url",
        "xrfkey",
        "cookie_name",
    ]
    missing = [k for k in required_keys if not config.get(k)]
    if missing:
        logger.error("Missing required configuration keys: %s", ", ".join(missing))
        return 1

    app_guids = resolve_app_guids(config)
    if not app_guids:
        logger.error("No application specified. Set 'app_guid' or 'app_guids' in the configuration.")
        return 1

    http_session = build_http_session(retries=config.get("retries", DEFAULT_RETRIES))
    session_id = None

    try:
        session_id = generate_session(
            http_session,
            config["xrfkey"],
            config["user_id"],
            config["user_directory"],
            config["proxy_server"],
            config["client_cert"],
            config["client_key"],
        )
        headers = build_headers(config, session_id)
        summary = extract_metadata_for_apps(http_session, config, headers, app_guids)
        return 1 if summary["failures"] else 0
    except Exception as e:
        logger.error("Metadata extraction failed: %s", e)
        return 1
    finally:
        if session_id:
            try:
                delete_session(
                    http_session,
                    config["xrfkey"],
                    config["proxy_server"],
                    config["client_cert"],
                    config["client_key"],
                    session_id,
                )
            except Exception:
                logger.warning("Failed to delete session %s", session_id)
        http_session.close()


if __name__ == "__main__":
    raise SystemExit(main())

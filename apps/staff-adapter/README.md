# staff-adapter

Maps a bridge `task.create` to a staff-bot wake and brings its `task.result` back.
Contract 0.2.0-mvp. Stdlib only. Imported by `apps/bridge-daemon`.

| Seam | Implementations | Select with |
|---|---|---|
| `Transport` (`transports.py`) | `webhook` (Grok Bot routine webhook per target), `fake` (smoke/tests: echo\|fail\|silent\|offline\|reject) | `BENCHLINK_ADAPTER_TRANSPORT` |
| `ResultSource` (`results.py`) | **`drive_folder`** (default; `<task_id>.json` in `BENCHLINK_RESULTS_DRIVE_FOLDER_ID`), `local_folder` (same layout on disk), `http_callback` (daemon endpoint + per-task token) | `BENCHLINK_RESULT_SOURCE` |

Routing (`targets.py`):
- The default target is `floyd` and the canary is `ethan`.
- The static S2 roster keys and the known Floyd/Ethan UUIDs resolve.
- Anything else returns `routing`.
- A roster target without a webhook URL or sender key fails closed with `offline`.

The sender-key header is **TBD**. It defaults to `Authorization: Bearer <BENCHLINK_TARGET_<KEY>_SENDER_KEY>` and is configurable via `BENCHLINK_WEBHOOK_SENDER_KEY_HEADER` / `_SCHEME`.

Full env table: `benchlink_adapter/config.py`. The dispatch payload and the staff routine prompt are in `docs/MVP_ROUNDTRIP.md`.

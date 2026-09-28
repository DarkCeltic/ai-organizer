# AI Organizer

**Organize your Nextcloud files with a local AI, without handing the final decision to the model.**

AI Organizer is a self-hosted [Nextcloud](https://nextcloud.com/) external app (ExApp) that uses [Ollama](https://ollama.com/) to read supported files and propose meaningful filenames, destination folders, tags, and optional routing to [Paperless-ngx](https://docs.paperless-ngx.com/). Review and edit each recommendation before applying it. A SQLite-backed history preserves previous decisions and file details so that reanalysis does not erase the past.

> **Project status:** Early development (app metadata: `0.2.2`). This project has been developed and tested in a personal self-hosted environment; it is not presented as a turnkey or production-hardened release. Review recommendations and back up your Nextcloud data and organizer database before using it on important files.

## Highlights

- **Two-stage local AI analysis:** Connect to an Ollama server you control. The identity stage sees the file name/content but no existing folder or tag names, preventing destination metadata from contaminating category/filename decisions. A separate destination-only stage may choose a matching existing folder or propose a new folder path; it cannot change the frozen file identity.
- **Human approval:** Preview suggestions before changes are made. Edit a filename or folder, choose suggested tags with checkboxes, and apply individual actions or all applicable actions.
- **Paperless-ngx routing:** Recommend eligible documents for a configurable Paperless consume/inbox folder. For a Paperless recommendation, choose to send it to Paperless or keep it in Nextcloud; Nextcloud-only recommendations use **Apply all**.
- **Routing policy:** Configure **Always keep in Nextcloud** and **Prefer Paperless** categories in Settings. Always-Keep takes precedence, and these lists are administrator policy rather than hidden hardcoded document-type rules.
- **Four separate workspace views:** Unprocessed, Review, History, and Failed, with a selected-file detail pane below the active list. Only one queue appears at a time.
- **Preserved history:** Keep previous applied, rejected, ignored, and unavailable-file records, including recorded paths, suggested changes, and decision details. Reanalyzing a previously rejected file creates a new suggestion for Review while retaining its earlier decision in History.
- **Missing-file handling:** If a file is no longer found in Nextcloud, remove its actionable suggestion from Review but preserve its last-known details and an unavailability note in History. Temporary Nextcloud/API failures are not treated as proof of deletion.
- **OCR fallback:** Try normal PDF text extraction first; for PDFs without readable text, use local OCR when enabled. OCR-derived suggestions still require manual review. Files that cannot yield usable text go to Failed for investigation or retry.
- **File-type controls:** Choose supported file types by readable name under **Settings → File Types**; the app maps those choices to the appropriate extensions/MIME types. Spreadsheet `.xlsx` content is extracted for analysis rather than sent as raw binary.

## How it works

```text
Nextcloud Files
      |
      v
Discover supported files in configured scan folders
      |
      v
Extract text (PDF / Office / text / spreadsheet; OCR fallback for PDFs)
      |
      v
Ollama identifies the file, filename/tags, category, and Paperless eligibility
      |
      v
Resolve destination separately: use an existing folder or propose a new path
      |
      v
Review in the AI Organizer workspace
      |
      +---- Keep in Nextcloud ---> Apply selected changes
      |
      +---- Paperless candidate -> Send to the configured consume folder
      |
      +---- Reject / Ignore -----> Preserve decision in History
      |
      +---- Failure -------------> Failed: inspect, retry, or ignore
```

Analysis produces **suggestions**, not automatic file moves. Applying a recommendation is a separate user action. A Paperless recommendation moves the file to a configured consume folder; Paperless-ngx handles ingestion from there. The ExApp does not replace Paperless's document-management functionality.

### Destination resolution

Existing Nextcloud folders are **candidates, not a hard limit**. AI Organizer first freezes file identity without showing the classifier any existing folder or tag names. It then resolves the destination separately:

1. An explicit administrator folder template wins and may create a path that does not exist yet, such as `/docker-compose/immich`.
2. A single deterministic category-matching existing folder is reused when available.
3. Otherwise a destination-only Ollama request may choose an existing folder or propose a new absolute path. That request cannot change the category, filename, tags, or Paperless decision.
4. If file identity is too uncertain, the file goes to `/Documents/Unsorted` rather than inventing a destination.

Intentional new-folder suggestions are not treated as errors merely because the folder is absent; the normal Apply path can create the destination. Semantic-only existing-folder matches are capped below the default 95% auto-apply threshold so they remain reviewable.

## Workspace

| View | Purpose |
| --- | --- |
| **Unprocessed** | Find eligible files that have not yet been analyzed. Select a file and click **Analyze**. |
| **Review** | Open saved recommendations without unnecessarily querying the LLM again; edit or apply them, or reject/ignore them. Reanalyze a changed file before applying an out-of-date recommendation. |
| **History** | Browse completed decisions and archived/unavailable records. Inspect original, last-known, and applied paths where recorded. History is read-only; reanalysis is a separate action that creates a new Review record rather than rewriting an old decision. |
| **Failed** | See analysis failures and retry them. An unreadable scanned PDF may be recoverable through OCR; otherwise it remains available for manual attention. |

The sidebar switches the active view. The right side displays that view's file list, and the selected file's recommendation or record details appear below it. History supports status filtering and pagination.

## Requirements

- A supported Nextcloud installation with **AppAPI** configured to run ExApps. The included `info.xml` currently declares Nextcloud versions **33–34**; compatibility outside that range has not been verified here.
- A deployment environment for the ExApp (the project's Docker/AppAPI setup, or Python for local development).
- A reachable Ollama server with a downloaded model, such as `qwen2.5:7b`. Ollama can be configured after the ExApp starts.
- A persistent location for the organizer's SQLite database.
- **Optional:** Paperless-ngx and a consume folder accessible through the configured Nextcloud path.
- PDF OCR support. The published Dockerfile installs OCRmyPDF plus Tesseract, Ghostscript, and qpdf so image-only PDFs can use the bounded OCR fallback.

Ollama can run on another host on your LAN. Use an address reachable **from the ExApp container**, not `localhost` unless Ollama actually runs inside that same network namespace. Your files' extracted text is sent to whichever Ollama endpoint you configure, so use a server you trust.

## Installation and configuration

AI Organizer is designed to be installed as a Nextcloud **ExApp** through AppAPI. The ExApp ID is `ai_organizer`, and the `0.2.2` manifest points AppAPI to `darthdragon/ai-organizer:0.2.2`.

### Recommended: Nextcloud AppAPI / App Store

AppAPI supplies the deployment identity and shared-secret values required by the container, including `APP_ID`, `APP_VERSION`, `APP_SECRET`, `APP_HOST`, `APP_PORT`, `APP_PERSISTENT_STORAGE`, and `NEXTCLOUD_URL`. AI Organizer uses the AppAPI shared secret for authenticated requests back to the same Nextcloud instance; a Nextcloud username and app password are **not** required in a managed ExApp deployment.

The container can start before Ollama is configured. On the first administrator visit, if the Ollama URL or model has not been saved, AI Organizer displays a first-run setup screen. Enter:

```text
Ollama URL       http://192.168.1.2:11434
Ollama model     qwen2.5:7b (or another model already installed in Ollama)
Paperless        optional
Paperless inbox  /inbox by default
```

The first-run screen can test the Ollama URL before saving and discover locally installed models directly in the existing editable **Model** field. The field remains free-form, so an administrator can pick a discovered model or type a new registry model name and ask Ollama to pull it. Pull status is shown in the UI: start/completion messages are green and Ollama failures (including invalid model names/tags) are shown in red. The full **Settings → Local LLM** page provides the same controls. The Ollama URL may be saved before a model is selected; analysis remains disabled until both values are configured.

The first-run screen saves these values to the organizer's SQLite settings. Advanced settings remain available under **AI Organizer → Settings**.

The manifest also declares `OLLAMA_URL`, `OLLAMA_MODEL`, `PAPERLESS_ENABLED`, and `INBOX_PATH` as optional AppAPI deploy options. They can pre-seed an installation, but they are not required for the container to boot.

AI Organizer stores its SQLite database under `APP_PERSISTENT_STORAGE` when AppAPI supplies that path. Review, History, Failed records, saved settings, and the service-user context therefore survive container replacement and upgrades.

> **Current access model:** `0.2.2` is administrator-only. The organizer currently has one global SQLite database and one scheduler, so the manifest intentionally restricts the UI/API and file action to administrators until per-user state separation is implemented.

### Advanced: manual AppAPI / Docker Compose

`compose.yaml` is for administrators who already have a manual AppAPI registration and its `APP_SECRET`. It does not require a mounted `config.yaml`.

Copy `.env.example` to `.env` and set at least:

```env
NEXTCLOUD_URL=http://192.168.1.2:8080
APP_SECRET=YOUR_EXISTING_APPAPI_SECRET

# Optional first-run seeds; leave blank to use the setup screen.
OLLAMA_URL=
OLLAMA_MODEL=
PAPERLESS_ENABLED=false
INBOX_PATH=/inbox
```

`APP_USER` is an optional local/manual override. In the normal AppAPI browser flow AI Organizer learns the authenticated administrator user from the AppAPI request and persists that user ID for scheduled background work.

Start the manual container with:

```bash
docker compose up -d
```

Do not run this manual instance alongside an AppAPI-managed instance of the same ExApp.

### Developer: run from PyCharm or source

There are two supported local authentication modes.

**Standalone Python/PyCharm:** If the Python process talks directly to Nextcloud without AppAPI, it still needs a Nextcloud account and app password because there is no AppAPI shared secret authenticating those WebDAV/OCS requests:

```env
NEXTCLOUD_URL=http://192.168.1.2:8080
NEXTCLOUD_USERNAME=YOUR_NEXTCLOUD_USERNAME
NEXTCLOUD_APP_PASSWORD=YOUR_NEXTCLOUD_APP_PASSWORD
APP_SECRET=
```

**Local/manual ExApp:** If you register the local process through AppAPI and provide `APP_SECRET` (plus `APP_USER` only when there is no proxied browser request yet), `NEXTCLOUD_USERNAME` and `NEXTCLOUD_APP_PASSWORD` are not required.

Ollama values can be left blank for the FastAPI UI and entered in the first-run screen:

```bash
uvicorn exapp.main:app --host 0.0.0.0 --port 23000
```

For the command-line organizer, configure Ollama before performing real analysis.

### Built-in defaults (no `config.yaml` required)

Production/App Store installs no longer depend on `config.yaml`. Non-secret behavior defaults live in `python_organizer_local_llm/config_defaults.py` and are seeded into SQLite on first use. The initial defaults include:

- scan root: `/AI Inbox`
- excluded folders: `/paperless-media`, `/inbox`, `/Photos`, `/AI Ignored`
- allowed extensions: `pdf`, `txt`, `md`, `docx`, `odt`, `rtf`, `log`, `csv`, `json`, `xml`, `xlsx`
- Paperless **Always keep in Nextcloud**: `resume`, `cv`, `curriculum vitae`, `cover letter`, `portfolio`, `source_code`, `project`, `template`
- Paperless **Prefer Paperless**: `receipt`, `invoice`, `statement`, `tax`, `insurance`, `contract`, `warranty`
- Paperless disabled by default, inbox `/inbox`
- OCR enabled with a 10-page initial limit
- automatic analysis/apply disabled

An explicit YAML file can still be supplied with `AI_ORGANIZER_CONFIG=/path/to/override.yaml` for legacy/local development. It is optional and deep-merges over the built-in defaults; it is not part of the App Store deployment contract.

### Environment variables

All direct environment access is centralized in `python_organizer_local_llm/settings.py`.

| Variable | Purpose | Managed ExApp behavior |
| --- | --- | --- |
| `NEXTCLOUD_URL` | Nextcloud base URL. | Supplied by AppAPI. Required manually. |
| `APP_SECRET` | AppAPI shared secret used for ExApp → Nextcloud authentication. | Supplied by AppAPI. |
| `APP_USER` | Optional manual/local user-ID override for AppAPI auth. | Normally learned from authenticated AppAPI requests. |
| `NEXTCLOUD_USERNAME` | Standalone PyCharm/CLI Basic Auth user. | Not needed with `APP_SECRET`. |
| `NEXTCLOUD_APP_PASSWORD` | Standalone PyCharm/CLI Nextcloud app password. | Not needed with `APP_SECRET`. |
| `OLLAMA_URL` | Optional initial Ollama server URL. | May be configured later in the first-run UI. |
| `OLLAMA_MODEL` | Optional initial Ollama model. | May be configured later in the first-run UI. |
| `PAPERLESS_ENABLED` | Optional initial Paperless state. | Defaults to `false`. |
| `INBOX_PATH` | Initial Paperless consume folder. | Defaults to `/inbox`. |
| `APP_ID` | ExApp identifier. | Supplied by AppAPI; default `ai_organizer`. |
| `APP_VERSION` | ExApp version. | Supplied by AppAPI; default `0.2.2`. |
| `APP_PERSISTENT_STORAGE` | Persistent ExApp data path. | Supplied by AppAPI. |
| `AA_VERSION` | AppAPI protocol header version. | Supplied by AppAPI when available; default `4.0.0`. |
| `AI_ORGANIZER_CONFIG` | Optional legacy/local YAML override. | Blank by default. |
| `LOG_LEVEL` | Logging verbosity. | `INFO`. |

`OLLAMA_URL` must be reachable from inside the ExApp container. `localhost` normally refers to the AI Organizer container itself, not an Ollama service on another host.

## Paperless behavior

Paperless integration is **optional**. When enabled, the organizer suggests routing archival document records to your configured consume directory while keeping a complete Nextcloud alternative. Categories not covered by administrator policy use an archival-record versus working-file heuristic.

For a **Paperless recommendation**, the review UI offers a Paperless action and a **Keep in Nextcloud** alternative, alongside applicable apply controls. For a **Nextcloud-only recommendation**, the main combined action is simply **Apply all**; there is no redundant Keep in Nextcloud button. In either case, review the proposed destination before applying it.

The **Always keep in Nextcloud** list takes priority over **Prefer Paperless**. Both lists are editable in **Settings → Paperless** and persisted in SQLite. When Paperless is disabled, its consume-folder and routing-policy controls are removed from the page rather than merely hidden; their saved values are retained and restored if Paperless is enabled again. The built-in values are the initial defaults for a new install. The selected policy is not a guarantee that AI classification will always be correct; manual review remains important.

## PDF OCR and file types

For PDFs, AI Organizer first tries to extract embedded text. If none is readable and OCR is enabled, it runs local OCR, subject to the configured page limit (the initial OCR setting uses **10 pages**). OCR-derived suggestions are held for manual review. If extraction and OCR still cannot provide useful text, the failure appears in **Failed** instead of silently treating the file as analyzed.

Use **Settings → File Types** to choose the file formats to process. The app presents human-readable file-type names and applies the corresponding extension/MIME-type rules to scans and manual analysis. The project's supported-format work includes PDF, modern Word documents (`.docx`), text-based formats, and Excel workbooks (`.xlsx`). Legacy Word `.doc` files should not be assumed to have a working extractor merely because a MIME type can appear in file-action registration.

OCR quality depends on the scan, language data, page count, and installed image-processing tools. The LLM may still suggest an incorrect name, folder, or destination.

## How History protects your work

- **Rejected and ignored:** Decisions remain visible after they leave the actionable queues; rejecting or ignoring does not delete the Nextcloud file.
- **Reanalyzed:** Reanalyzing a previously rejected file creates a new suggestion ID and puts the new recommendation in Review. The old rejection stays in History; it is not overwritten.
- **Applied:** History records the recommendation and available actual action details, such as original/applied paths and tags. Fields missing from older records may be shown as not recorded instead of being invented.
- **Unavailable:** When an authoritative Nextcloud lookup cannot find a file, the pending Review item is archived with a detection time and note. A timeout, permission error, or other API failure is **not** treated as a confirmed deletion.

History is an audit trail of the organizer's records, **not** a backup or a way to restore deleted files. Back up your files and the SQLite database separately.

## Privacy and safety

This project is designed for self-hosted Nextcloud and a local/self-hosted Ollama endpoint. It does **not** require a commercial cloud LLM, but document contents are transmitted to your **configured** Ollama server, and Paperless-bound files are transferred to the **configured** consume folder. You control those hosts and network paths.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| AI Organizer does not appear in Nextcloud | ExApp heartbeat, AppAPI registration, enabled state, registered top-menu/file action, and browser cache. |
| Ollama connection fails | The Ollama URL must be reachable from the ExApp host/container; check firewalls and listen address. |
| `.xlsx` file is not offered for analysis | Enable the spreadsheet file type and check MIME registration, extension filtering, and the actual extractor. |
| Scanned PDF reports no readable text | Enable OCR and verify the OCR-capable image was rebuilt with its dependencies; inspect Failed if extraction still fails. |
| Review fails to verify files | Check Nextcloud/WebDAV connectivity and permissions. Do not archive files merely because the lookup errored. |
| Suggestion seems outdated after a file changed | Re-analyze to create a current suggestion rather than applying one based on the previous file version. |
| A Paperless candidate should stay in Nextcloud | Select **Keep in Nextcloud** and review **Always keep in Nextcloud** / **Prefer Paperless** in Settings. |

## Development and contributions

This project was developed using OpenAI's ChatGPT for code generation and debugging, with requirements, design choices, integration, and testing directed by the maintainer. AI-generated suggestions in the **application** are likewise intended to be reviewed by a human before changes are applied.

Issues and pull requests are welcome. When reporting a problem, include a description of the behavior, the relevant version, and **redacted** logs or configuration. Never include API secrets, app passwords, document contents, or a copy of your live organizer database in public issues.

## License

**GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`).** See the repository's `LICENSE.md` file for the full license text. Add the full license file to the repository before publication if it is not already present. Third-party dependencies and any reused third-party code retain their respective licenses.

This is an independent project and is not an official Nextcloud, Ollama, or Paperless-ngx product.


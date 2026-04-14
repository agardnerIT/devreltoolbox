# devreltoolbox

## Quick start (devcontainer)

1. Clone this repo locally.
2. Create a `.env` file with your `LANGDOCK_API_KEY` (do not commit this file).

```
export LANGDOCK_API_KEY=*****
```

3. In VS Code, open View > Command Palette, then run Rebuild and Reopen in Container.
4. The devcontainer now uses `ubuntu:noble` as the base image and installs the main runtime tools through Dev Container Features.
5. On first container creation, `postCreateCommand` installs Python dependencies from `requirements.txt`.
6. First startup can still take a few minutes because heavier ML dependencies are installed and Whisper may download model assets on first use.
7. After startup, run:

```
python app.py
```

8. Open `http://localhost:8000`

Included in the devcontainer:

- Python 3.12 via the official Python feature
- Node.js via the official Node feature
- `ffmpeg` via a Dev Container Feature
- `yt-dlp` via a Dev Container Feature

Port `8000` is forwarded by the devcontainer configuration.

## YouTube subtitle extraction in devcontainers

Some YouTube videos require an authenticated session and may fail with errors like:

- `Sign in to confirm you're not a bot`
- prompts mentioning `--cookies-from-browser` or `--cookies`

### Supported mode for auth-required videos

For auth-required subtitle extraction, run this project in a **local devcontainer**.

Remote/browser-window devcontainer sessions are not suitable for this flow because
the app cannot directly access cookies from your local browser session.

### Troubleshooting

If subtitle download fails with a bot/sign-in challenge:

1. Run the devcontainer locally (desktop Docker + VS Code Dev Containers).
2. Retry the subtitle flow from the local environment.

Security reminders:

- Never commit cookie files.
- Never paste cookie contents into issues, PRs, logs, or chat.
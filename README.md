# devreltoolbox

![Warning: Entirely vibecoded](https://img.shields.io/badge/Warning-Entirely%20vibecoded-orange?style=for-the-badge)

## Quick start

1. Clone this repository.

```
git clone https://github.com/agardnerit/devreltoolbox
```

2. Create your environment file from the sample:

```bash
cp .env.sample .env
```

3. Edit `.env` and set your `LANGDOCK_API_KEY`.
4. Start the Codespace for this repository.
5. In the Codespace terminal, run:

```bash
python app.py
```

6. Open `http://localhost:8000`

Notes:

- The first startup can take a little longer because Python dependencies are installed and Whisper assets may be downloaded on first use.
- Port `8000` is forwarded by the devcontainer/Codespaces configuration.

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
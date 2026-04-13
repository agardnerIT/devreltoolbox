# devreltoolbox

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
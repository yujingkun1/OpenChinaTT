# Project conventions

- Preserve real accounts, password hashes, sessions, uploaded avatars and match history. Migrations must be additive and tested against a backup before a live release.
- Keep client and server behavior consistent. Personal ratings are settled at 00:00 Asia/Shanghai; doubles never change personal ratings. Users must consent to matches and confirm results.
- Verify changes with `python -m unittest discover -s tests -v` and `node --check public/app.js`. Check changed mobile interactions in a browser.
- Follow the existing readable Chinese UI and mobile navigation. Escape all user-created content; validate authorization and inputs on the server.
- This project's owner has requested that subsequent authorized changes be committed and pushed to the configured GitHub remote after validation, and that changes for the established live website be deployed through its existing private deployment workflow. Keep source, live behavior, and remote repository in sync. Report actual push/deployment results; do not claim synchronization after only local edits.
- Inspect the configured origin and current working tree before publishing. Never force push or discard user changes. If GitHub access is missing, finish independent work and report the exact remaining authorization requirement.
- Keep deployment addresses, SSH details, passwords, SMTP credentials, databases, backups and private runbooks in ignored local files. Public templates use placeholders. Never commit `.env`, `.work`, real credentials or production data.
- Do not publish screenshots containing private email addresses or account credentials. Documentation examples should use fictional data.

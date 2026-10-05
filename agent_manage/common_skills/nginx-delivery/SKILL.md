---
name: nginx-delivery
description: Deploy explicitly public user-facing files, HTML pages, previews, and static assets through nginx. Use whenever a task creates or updates a file that the user must open through a public URL; local paths, IPv6-only URLs, or attachment-only delivery are not complete.
---

# Nginx Delivery

1. Confirm the files are intended to be public. Never publish secrets, credentials, private configuration, source archives, or logs without explicit review.
2. Ensure `PATH` includes `/usr/local/sbin:/usr/sbin:/sbin`. Check nginx through the package manager, service state, or an absolute binary path; do not rely only on `command -v`.
3. If nginx is missing, install it with the host package manager when system installation is authorized; otherwise report the blocker. Do not run it as a foreground service.
4. Name files with lowercase URL-safe English words and hyphens: `<topic>-<content>.<ext>`. Add `-preview` before the extension for previews.
5. Use the public root declared in the workspace runtime rules. Container instances use `/home/node/.openclaw/workspace/public/`; VPS instances use `/var/www/html/`.
6. Deploy only the required files under that public root and update its `index.html` without discarding existing entries. Preserve unrelated files and avoid overwriting an existing deliverable unless updating it is intended.
7. Validate nginx configuration, service state, listening port, the deployed file, and relevant logs. Run network checks separately with bounded timeouts and IPv4, for example `curl -4 --max-time 5`.
8. Return the verified public URL reachable over IPv4 for every delivered file. Prefer HTTPS when configured; otherwise return the actual HTTP URL. Never return a local path, IPv6 URL, or invented URL as the delivery result.

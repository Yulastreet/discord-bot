# Bot Console — Discord bot + Web dashboard

Personal multi-feature Discord bot with an integrated admin dashboard.

- **Bot** (Python · discord.py 2.7) : XP per-guild, auto-reactions, music
  (YouTube via yt-dlp + Deno), duels with custom sabre catalog, and more.
- **Dashboard** (Flask + Jinja) : per-guild + cross-server views to monitor
  and edit everything stored in the SQLite DB. Theme dark/light, parallax,
  Vercel-inspired Restrained palette with a lime acid accent.
- **Architecture** : the bot and the web run as **two separate pm2 processes**
  on the VPS; web pushes commands to the bot via a lightweight DB-backed
  command queue (~1.5s polling).

## License

This project is proprietary. The source code is available for viewing only.
Copying, modifying, distributing, sublicensing, or using this code to create derivative works is not allowed without explicit written permission.
See [LICENSE](LICENSE).

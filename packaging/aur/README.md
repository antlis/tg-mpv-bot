# AUR packaging

`PKGBUILD` for **tg-mpv-bot-git** — installs to `/usr/share/tg-mpv-bot` with a
`/usr/bin/tg-mpv-bot` launcher and a generic systemd *user* unit reading
`~/.config/tg-mpv-bot.env`.

## Publishing / updating on the AUR (maintainer notes)

```bash
# one-time: AUR account + SSH key at https://aur.archlinux.org
git clone ssh://aur@aur.archlinux.org/tg-mpv-bot-git.git aur-tg-mpv-bot-git
cp PKGBUILD aur-tg-mpv-bot-git/ && cp tg-mpv-bot.service aur-tg-mpv-bot-git/
cd aur-tg-mpv-bot-git
makepkg --printsrcinfo > .SRCINFO
git add -A && git commit -m "Update" && git push
```

Users then install with `yay -S tg-mpv-bot-git`, configure
`~/.config/tg-mpv-bot.env`, and `systemctl --user enable --now tg-mpv-bot`.

Note: on a system install the bot uses the pacman-managed `yt-dlp`;
`/mpv_update_ytdlp` is a no-op there by design (it only manages venvs).

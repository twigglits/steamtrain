#!/bin/sh
# Remove slob: units, launcher, package. Steam configs are left as-is;
# run `slob revert` BEFORE uninstalling if you want options restored.
set -eu

systemctl --user disable --now steam-launch-options-bot.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/steam-launch-options-bot.service" \
      "$HOME/.config/systemd/user/steam-launch-options-bot.timer"
systemctl --user daemon-reload

rm -f "$HOME/.local/bin/slob"
rm -rf "$HOME/.local/lib/steam-launch-options-bot"

echo "Uninstalled. State/backups kept in ~/.local/state/steam-launch-options-bot"
echo "and config in ~/.config/steam-launch-options-bot (delete manually if unwanted)."

#!/bin/sh
# Remove steamtrain: units, launcher, package. Steam configs are left as-is;
# run `steamtrain revert` BEFORE uninstalling if you want options restored.
set -eu

systemctl --user disable --now steamtrain.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/steamtrain.service" \
      "$HOME/.config/systemd/user/steamtrain.timer"
systemctl --user daemon-reload 2>/dev/null || true

rm -f "$HOME/.local/bin/steamtrain"
rm -rf "$HOME/.local/lib/steamtrain"

echo "Uninstalled. State/backups kept in ~/.local/state/steamtrain"
echo "and config in ~/.config/steamtrain (delete manually if unwanted)."

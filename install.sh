#!/bin/sh
# Install slob for the current user: package, launcher, systemd user units.
set -eu

REPO_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LIB_DIR="$HOME/.local/lib/steam-launch-options-bot"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$LIB_DIR" "$BIN_DIR" "$UNIT_DIR"

rm -rf "$LIB_DIR/slob"
cp -r "$REPO_DIR/slob" "$LIB_DIR/slob"

cat > "$BIN_DIR/slob" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m slob "\$@"
EOF
chmod +x "$BIN_DIR/slob"

cp "$REPO_DIR/systemd/steam-launch-options-bot.service" \
   "$REPO_DIR/systemd/steam-launch-options-bot.timer" "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now steam-launch-options-bot.timer

echo "Installed. Useful commands:"
echo "  slob scan                                          # see proposals"
echo "  slob apply --dry-run                               # plan without writing"
echo "  systemctl --user list-timers steam-launch-options-bot.timer"
echo "  journalctl --user -u steam-launch-options-bot.service -e"

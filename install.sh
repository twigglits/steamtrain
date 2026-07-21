#!/bin/sh
# Install steamtrain for the current user: package, launcher, systemd user units.
set -eu

REPO_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
LIB_DIR="$HOME/.local/lib/steamtrain"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$LIB_DIR" "$BIN_DIR" "$UNIT_DIR"

rm -rf "$LIB_DIR/steamtrain"
cp -r "$REPO_DIR/steamtrain" "$LIB_DIR/steamtrain"

cat > "$BIN_DIR/steamtrain" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m steamtrain "\$@"
EOF
chmod +x "$BIN_DIR/steamtrain"

cp "$REPO_DIR/systemd/steamtrain.service" \
   "$REPO_DIR/systemd/steamtrain.timer" "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now steamtrain.timer

echo "Installed. Useful commands:"
echo "  steamtrain scan                                          # see proposals"
echo "  steamtrain apply --dry-run                               # plan without writing"
echo "  systemctl --user list-timers steamtrain.timer"
echo "  journalctl --user -u steamtrain.service -e"

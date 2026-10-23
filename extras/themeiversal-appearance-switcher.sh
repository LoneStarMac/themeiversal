#!/bin/bash
# Themeiversal appearance watcher for iTerm2 / Terminal.app (macOS).
# Poll the OS appearance and switch default profiles accordingly.
# Install as a launchd agent (e.g. run every minute via StartInterval)
# or invoke from a Shortcut automation.

set -euo pipefail

DARK_TERM_PROFILE="Themeiversal Ember Dark"
LIGHT_TERM_PROFILE="Themeiversal Ember Light"

APPEARANCE=$(defaults read -g AppleInterfaceStyle 2>/dev/null || echo Light)

if [ "$APPEARANCE" = "Dark" ]; then
  DEFAULT_PROFILE="$DARK_TERM_PROFILE"
else
  DEFAULT_PROFILE="$LIGHT_TERM_PROFILE"
fi

# Terminal.app: set the default profile.
osascript -e "tell application \"Terminal\" to set default settings to settings set \"$DEFAULT_PROFILE\"" || true

# iTerm2: most reliable route is the Python API
# (https://iterm2.com/python-api/) â session.async_set_profile â
# or keep two preference files and swap the Default Bookmark Guid.
echo "Themeiversal: appearance=$APPEARANCE profile=$DEFAULT_PROFILE"
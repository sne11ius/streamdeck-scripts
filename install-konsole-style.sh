#!/usr/bin/env bash
# Install only appearance settings; never contact Konsole, KWin, or systemd.
set -euo pipefail

case ${1:-} in
    ''|--rollback) ;;
    --help|-h)
        printf '%s\n' 'Usage: bash install-konsole-style.sh [--rollback]' \
            'Stages appearance for future Quake launches. Running terminals are not reloaded.'
        exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { printf '%s\n' 'Too many arguments.' >&2; exit 2; }
for tool in kwriteconfig6 kreadconfig6; do command -v "$tool" >/dev/null; done

SCRIPT_DIR=$(dirname "$(realpath "${BASH_SOURCE[0]}")")
config="${XDG_CONFIG_HOME:-$HOME/.config}/konsolerc"
style="${XDG_DATA_HOME:-$HOME/.local/share}/konsole/quake.qss"
backup="${XDG_STATE_HOME:-$HOME/.local/state}/konsole-quake-style"

if [[ ${1:-} == --rollback ]]; then
    [[ -f $backup/ready && -f $backup/konsolerc.before ]] || {
        printf '%s\n' 'No Konsole styling backup to roll back.' >&2
        exit 1
    }
    # Restore only our two keys, not a whole config that may have changed since.
    for key in SplitViewVisibility SplitDragHandleSize; do
        previous=$(kreadconfig6 --file "$backup/konsolerc.before" --group SplitView \
            --key "$key" --default __UNSET__)
        if [[ $previous == __UNSET__ ]]; then
            kwriteconfig6 --file "$config" --group SplitView --key "$key" --delete
        else
            kwriteconfig6 --file "$config" --group SplitView --key "$key" "$previous"
        fi
    done
    if [[ -f $backup/quake.qss.before ]]; then
        install -Dm644 "$backup/quake.qss.before" "$style"
    else
        rm -f -- "$style"
    fi
    rm -f -- "$backup/active" "$backup/ready"
    printf '%s\n' 'Previous split settings restored; future Quake launches no longer add styling flags.' \
        'Running terminals were not reloaded. Existing window/toolbar state was not changed.'
    exit 0
fi

[[ -r $SCRIPT_DIR/konsole-config/quake.qss ]]
umask 077
mkdir -p -- "${config%/*}" "$backup"
if [[ ! -f $backup/ready ]]; then
    if [[ -f $config ]]; then
        install -m600 "$config" "$backup/konsolerc.before"
    else
        install -m600 /dev/null "$backup/konsolerc.before"
    fi
    if [[ -f $style ]]; then
        install -m600 "$style" "$backup/quake.qss.before"
    else
        rm -f -- "$backup/quake.qss.before"
    fi
    # Recovery must be available before any live file changes, including on retry.
    touch "$backup/ready"
fi

install -Dm644 "$SCRIPT_DIR/konsole-config/quake.qss" "$style"
# Deliberately omit --notify: do not resize panes in any running instance.
kwriteconfig6 --file "$config" --group SplitView --key SplitViewVisibility AlwaysHideSplitHeader
kwriteconfig6 --file "$config" --group SplitView --key SplitDragHandleSize SplitDragHandleMedium
touch "$backup/active"

printf '%s\n' 'Installed: hidden pane headers, native 5px handles, translucent Quake dividers.' \
    'The Quake launcher will hide toolbars without replacing menu XML.' \
    'Running terminals were not reloaded. Styling takes effect on a future launch.' \
    "Backup: $backup" \
    "Rollback: bash \"$SCRIPT_DIR/install-konsole-style.sh\" --rollback"

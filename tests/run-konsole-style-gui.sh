#!/usr/bin/env bash
# Runtime-only fixtures, not an installer test. Nothing uses the desktop bus/home.
set -euo pipefail

if [[ ${1:-} == --child ]]; then
    run=${2:?}
    [[ $run == /tmp/opencode/konsole-style-gui.* && -d $run && ! -L $run ]]
    [[ ${HOME:-} == "$run/home" && ${XDG_RUNTIME_DIR:-} == "$run/runtime" ]]
    [[ ${DBUS_SESSION_BUS_ADDRESS:-} == "unix:path=$run/runtime/"* ]]
    [[ -z ${DISPLAY+x} && -z ${QT_QPA_PLATFORMTHEME+x} ]]
    unset XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID SESSION_MANAGER
    # KWin deliberately removes TMPDIR from its session child's environment.
    export TMPDIR="$run/tmp"
    if [[ $KONSOLE_STYLE_GUI_BACKEND == wayland ]]; then
        export WAYLAND_DISPLAY=konsole-style-test
        [[ -S $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ]]
    else
        [[ -z ${WAYLAND_DISPLAY+x} ]]
    fi
    export QT_QPA_PLATFORM=$KONSOLE_STYLE_GUI_BACKEND
    # Only this exec receives LD_PRELOAD, never KWin, D-Bus, or an existing app.
    exec env KONSOLE_STYLE_GUI_PID="$$" LD_PRELOAD="$run/gui.so" \
        konsole --separate --hide-menubar --hide-toolbars \
        --stylesheet "$KONSOLE_STYLE_GUI_STYLESHEET" --profile disposable \
        --workdir "$HOME" >"$run/konsole.log" 2>&1
fi

backend=both
mode=styled
stylesheet=
for arg in "$@"; do
    case $arg in
        --backend=both|--backend=offscreen|--backend=wayland) backend=${arg#*=} ;;
        --baseline) mode=baseline ;;
        --no-stylesheet) mode=no-stylesheet ;;
        --help|-h)
            printf '%s\n' 'Usage: bash tests/run-konsole-style-gui.sh [--backend=both|offscreen|wayland] [--baseline|--no-stylesheet] [stylesheet]' \
                'Default: both backends, repo konsole-config/quake.qss, native split settings, Kvantum KvFlatRed.' \
                '--baseline: upstream split defaults and empty QSS; expect regression failures.' \
                '--no-stylesheet: expected native settings, empty QSS; isolates QSS failures.' \
                'Offscreen checks interaction only. Wayland also requires real backing-store alpha.' \
                'Run both backends: popup grabs need real input serials and are checked offscreen only.' \
                'Artifacts remain in a fresh /tmp/opencode/konsole-style-gui.* directory.'
            exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
        *) [[ -z $stylesheet ]] || exit 2; stylesheet=$arg ;;
    esac
done
script=$(realpath -- "${BASH_SOURCE[0]}")
tests=${script%/*}
stylesheet=${stylesheet:-${tests%/*}/konsole-config/quake.qss}
if [[ $mode == styled && ! -f $stylesheet ]]; then
    printf 'Stylesheet missing: %s (use --baseline for the initial RED run)\n' "$stylesheet" >&2
    exit 2
fi
if [[ $backend == both ]]; then
    args=("$stylesheet")
    if [[ $mode != styled ]]; then args=("--$mode" "${args[@]}"); fi
    status=0
    for platform in offscreen wayland; do
        /bin/bash "$script" "--backend=$platform" "${args[@]}" || status=$?
    done
    exit "$status"
fi
for tool in c++ pkg-config dbus-run-session konsole timeout; do command -v "$tool" >/dev/null; done
if [[ $backend == wayland ]]; then command -v kwin_wayland >/dev/null; fi
[[ -d /tmp/opencode && ! -L /tmp/opencode ]]
umask 077
ulimit -c 0
run=$(mktemp -d /tmp/opencode/konsole-style-gui.XXXXXX)
printf 'ARTIFACTS=%s\nBACKEND=%s MODE=%s\n' "$run" "$backend" "$mode"
mkdir -p "$run"/{home,config/Kvantum,data/konsole,data/color-schemes,state,cache,runtime,config-dirs,data-dirs,tmp}
printf '%s\n' 'isolated-konsole-style-gui-v1' >"$run/guard"
if [[ $mode == styled ]]; then cp -- "$stylesheet" "$run/quake.qss"; else : >"$run/quake.qss"; fi
# Keep relative URLs in the production QSS relative to its original location.
if [[ $mode == styled ]]; then stylesheet=$(realpath -- "$stylesheet"); else stylesheet=$run/quake.qss; fi

cat >"$run/config/konsolerc" <<'EOF'
[Desktop Entry]
DefaultProfile=disposable.profile
[General]
ConfigVersion=1
[TabBar]
TabBarPosition=Bottom
[KonsoleWindow]
RememberWindowSize=false
[UiSettings]
ColorScheme=KvFlatRed
EOF
if [[ $mode != baseline ]]; then
    cat >>"$run/config/konsolerc" <<'EOF'
[SplitView]
SplitViewVisibility=AlwaysHideSplitHeader
SplitDragHandleSize=SplitDragHandleMedium
EOF
fi
cat >"$run/config/Kvantum/kvantum.kvconfig" <<'EOF'
[General]
theme=KvFlatRed
EOF
cp /usr/share/color-schemes/KvFlatRed.colors "$run/config/kdeglobals"
cp /usr/share/color-schemes/KvFlatRed.colors "$run/data/color-schemes/KvFlatRed.colors"
cat >>"$run/config/kdeglobals" <<'EOF'
[KDE]
widgetStyle=kvantum
EOF
cat >"$run/data/konsole/disposable.profile" <<EOF
[General]
Name=disposable
Command=/bin/sh -i
Directory=$run/home
Parent=FALLBACK/
[Appearance]
ColorScheme=disposable
Font=Monospace,11,-1,5,50,0,0,0,0,0
[Scrolling]
HistoryMode=1
HistorySize=1000
EOF
cat >"$run/data/konsole/disposable.colorscheme" <<'EOF'
[General]
Description=Disposable alpha fixture
Opacity=0.5
Blur=false
[Background]
Color=16,24,32
[BackgroundIntense]
Color=16,24,32
[BackgroundFaint]
Color=16,24,32
[Foreground]
Color=240,240,240
[ForegroundIntense]
Color=255,255,255
[ForegroundFaint]
Color=180,180,180
EOF
# No service directories: even accidental activation cannot reach desktop agents.
cat >"$run/session.conf" <<EOF
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=$run/runtime</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
    <allow own="*"/>
  </policy>
</busconfig>
EOF

# No moc or Konsole development headers required.
qt_flags=$(pkg-config --cflags --libs Qt6Widgets Qt6Test)
read -r -a qt_flags <<<"$qt_flags"
# Treat Qt as a system dependency: keep -Werror for the harness, while GCC's
# new diagnostics in third-party headers do not prevent building the tests.
compile_flags=()
for flag in "${qt_flags[@]}"; do
    if [[ $flag == -I* ]]; then
        compile_flags+=(-isystem "${flag#-I}")
    else
        compile_flags+=("$flag")
    fi
done
TMPDIR="$run/tmp" c++ -std=c++17 -fPIC -shared -Wall -Wextra -Werror -O1 -g \
    "$tests/konsole-style-gui.cpp" -o "$run/gui.so" \
    "${compile_flags[@]}"

launch=(/bin/bash "$script" --child "$run")
if [[ $backend == wayland ]]; then
    launch=(kwin_wayland --virtual --width 1280 --height 900 --scale 1
        --socket konsole-style-test --no-lockscreen --no-global-shortcuts
        --exit-with-session "/bin/bash \"$script\" --child \"$run\"")
fi
set +e
# env -i removes KONSOLE_DBUS_*, host displays, activation tokens, shell rc hooks,
# session manager, Qt platform theme, and LD_PRELOAD. The timeout owns only this job.
env -i -C "$run/home" PATH=/usr/bin:/bin LANG=C.UTF-8 HOME="$run/home" \
    XDG_CONFIG_HOME="$run/config" XDG_CONFIG_DIRS="$run/config-dirs" \
    XDG_DATA_HOME="$run/data" XDG_DATA_DIRS="$run/data-dirs" \
    XDG_STATE_HOME="$run/state" XDG_CACHE_HOME="$run/cache" \
    XDG_RUNTIME_DIR="$run/runtime" TMPDIR="$run/tmp" \
    DBUS_SYSTEM_BUS_ADDRESS="unix:path=$run/runtime/no-system-bus" \
    QT_STYLE_OVERRIDE=kvantum QT_SCALE_FACTOR=1 QT_WAYLAND_DISABLE_WINDOWDECORATION=1 \
    KWIN_COMPOSE=Q LIBGL_ALWAYS_SOFTWARE=1 QT_QUICK_BACKEND=software \
    KONSOLE_STYLE_GUI_ROOT="$run" KONSOLE_STYLE_GUI_BACKEND="$backend" \
    KONSOLE_STYLE_GUI_STYLESHEET="$stylesheet" \
    timeout --signal=TERM --kill-after=5s 90s \
    dbus-run-session --config-file "$run/session.conf" -- "${launch[@]}" \
    >"$run/session.log" 2>&1
status=$?
set -e
if [[ -f $run/results.txt ]]; then
    cat "$run/results.txt"
else
    printf 'FAIL harness did not produce results; inspect %s/{session,konsole}.log\n' "$run"
    [[ $status != 0 ]] || status=1
fi
if [[ ! -f $run/complete ]]; then
    printf 'FAIL harness did not finish (process exit %s)\n' "$status"
    [[ $status != 0 ]] || status=1
fi
printf 'EXIT=%s ARTIFACTS=%s\n' "$status" "$run"
exit "$status"

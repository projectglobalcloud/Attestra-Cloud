#!/bin/bash
# Starts the Attestra platform on this Mac.
#
#   Double-click this file in Finder, or in Terminal run:
#       bash START-MAC.command
#
# It finds Python 3.10 or newer and hands over to start.py, which does the
# rest. If no suitable Python is installed, it downloads a private copy into
# this folder (nothing is installed system-wide).

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
    echo
    read -r -p "  Press Return to close this window. " _
    exit 1
}

usable() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3.10 python3.14 python3 \
                     /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                     /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
        path=$(command -v "$candidate" 2>/dev/null) || continue
        # /usr/bin/python3 is a stub that opens an installer dialog when the
        # Apple developer tools are missing; only use it when they are there.
        if [ "$path" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if usable "$path"; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

PY=$(find_python)
if [ -n "$PY" ]; then
    "$PY" start.py "$@" || pause_and_exit
    exit 0
fi

# No suitable Python: fetch uv (a small, self-contained Python manager) into
# .tools/ and let it provide Python 3.12 for this project only.
UV=""
for c in ".tools/uv" ".tools/bin/uv"; do [ -x "$c" ] && UV="$c" && break; done
if [ -z "$UV" ]; then
    echo
    echo "  Python 3.10 or newer was not found on this Mac."
    echo "  Downloading a private copy for this project (one time only)..."
    mkdir -p .tools
    if ! curl -LsSf https://astral.sh/uv/install.sh | \
         env UV_INSTALL_DIR="$PWD/.tools" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null 2>&1; then
        echo
        echo "  The download failed. Check the internet connection, or install"
        echo "  Python from https://www.python.org/downloads/ and run this again."
        pause_and_exit
    fi
    for c in ".tools/uv" ".tools/bin/uv"; do [ -x "$c" ] && UV="$c" && break; done
    [ -z "$UV" ] && { echo "  The Python manager could not be set up."; pause_and_exit; }
fi

"$UV" run --no-project --python 3.12 start.py "$@" || pause_and_exit

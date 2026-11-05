#!/bin/sh
# Run LinExplorer from the source tree.
HERE="$(dirname "$(readlink -f "$0")")"
exec env PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" python3 -m linexplorer "$@"

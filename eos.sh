#!/bin/bash
ABSPATH=$(readlink -f $0)
ABSDIR=$(dirname $ABSPATH)

cd $ABSDIR
/root/.local/bin/uv run python -m akkudoktoreos.server.eos

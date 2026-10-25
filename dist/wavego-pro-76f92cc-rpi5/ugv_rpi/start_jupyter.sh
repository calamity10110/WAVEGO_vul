#!/bin/bash
[ -f ~/.bashrc ] && source ~/.bashrc
cd "$(dirname "$0")" && source ugv-env/bin/activate && exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser

#!/usr/bin/bash
cd "$(dirname "$0")"
DISPLAY=:0.0
export DISPLAY
xset -display $DISPLAY s off
xset -display $DISPLAY s noblank
xrandr -d $DISPLAY -o right

/usr/bin/python3 wiim.py --device=1 --height=400 --window_ratio='16/5' --sleep_between_frames --n_frequency_bins=32 --wiim_ip="192.168.68.118"


#!/bin/sh
# shellscript for cron job - move cwd first
cd `dirname "$0"`
python3 ./__main__.py

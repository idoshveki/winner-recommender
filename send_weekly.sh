#!/bin/bash
cd /Users/idoshveki/projects/winner-recommender
source .venv/bin/activate
python src/recommend/send_weekly.py >> /tmp/winner_weekly.log 2>&1

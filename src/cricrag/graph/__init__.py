"""Relational layer for Pandit: canonical player IDs, then player-to-player edge tables in data/graph.duckdb.

identity.py  one Cricinfo player_id per person across ESPN commentary, Cricsheet and the ipl_fielding CSV
build.py     edge tables (bowled_to, dismissed, fielding_dismissal, dropped, played_for) + parity checks
explore.py   NetworkX analytics over those tables (rankings, nemesis, fielder-bowler pairs, communities)
"""

"""Benchlink staff-adapter — bridge task -> staff bot -> task.result (contract 0.2.0-mvp).

Two pluggable seams:
  Transport     how a task reaches a staff bot   (webhook | fake)
  ResultSource  how the bot's task.result comes back (drive_folder [default] |
                local_folder | http_callback)
"""
__version__ = "0.2.0+mvp.roundtrip"
CONTRACT_VERSION = "0.2.0-mvp"

"""Minimal stand-in for the real ``astrbot`` package.

Tests run without an AstrBot installation: this package only implements
the tiny API surface the plugin actually uses. It is installed into
``sys.modules`` by ``tests/conftest.py`` before any plugin module import.
"""

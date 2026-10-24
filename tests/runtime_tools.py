"""Portable executable discovery for explicitly invoked acceptance harnesses."""
import shutil


def node_executable():
    value = shutil.which('node')
    if value is None:
        raise RuntimeError('Node.js >=22.12 is required; add node to PATH before acceptance testing')
    return value

# conftest.py — ensure services/ is on sys.path so 'shared' is importable
import sys
import os

_services_dir = os.path.join(os.path.dirname(__file__), "..", )
sys.path.insert(0, os.path.abspath(_services_dir))

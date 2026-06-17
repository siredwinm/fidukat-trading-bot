import os
import sys

# Make the repo root importable so `from risk import governor` etc. work under pytest.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

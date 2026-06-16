"""Agrega ejercicio-08-final/ al sys.path para que pytest pueda importar
`app` y `pipeline` sin hacks adicionales en los tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

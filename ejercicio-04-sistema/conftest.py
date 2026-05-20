"""Hace importable el paquete `app` al correr pytest desde la raiz del repo.

La presencia de este conftest agrega `ejercicio-04-sistema/` al sys.path
(modo de import por defecto de pytest), de modo que `from app.main import app`
resuelve sin hacks de sys.path en los tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

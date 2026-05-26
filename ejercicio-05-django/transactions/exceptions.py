"""Handler de excepciones de DRF.

DRF responde 400 a los errores de validacion de un serializer. El E4
(FastAPI/Pydantic) respondia 422 para lo mismo, y la rubrica del E5 pide
"422 en batch invalido". Este handler conserva todo el comportamiento por
defecto de DRF y solo reescribe el status a 422 cuando la causa es una
ValidationError, manteniendo la paridad con el E4.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def validation_to_422_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(exc, ValidationError):
        response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return response

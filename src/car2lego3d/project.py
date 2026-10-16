"""Deployment identity for the open-source reconstruction project."""

PROJECT_NAME = "Car2LEGO_3D"
DEFAULT_BACKEND = "hunyuan-mv"
FRONTEND_PORT = 8768
API_PORT = 8767
LEGACY_WEB_PORT = 8765


def desktop_backend(requested=None):
    if requested and requested != DEFAULT_BACKEND:
        raise ValueError("This project uses Hunyuan3D; use Car2LGEO_3D_LLM for LLM + Blender modelling")
    return DEFAULT_BACKEND

"""Run from any directory; always bind loopback, never implicitly enable reload."""
import sys
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'backend'))

if __name__ == '__main__':
    import uvicorn
    from app.core import get_settings
    from app.main import create_app
    settings = get_settings()
    uvicorn.run(create_app(settings), host='127.0.0.1', port=settings.port, access_log=False)

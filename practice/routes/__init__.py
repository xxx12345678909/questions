"""Sub-route registration center for the practice module."""
from practice.routes.manage import manage_bp
from practice.routes.graph import graph_bp
from practice.routes.recommend import recommend_bp

__all__ = ['manage_bp', 'graph_bp', 'recommend_bp']

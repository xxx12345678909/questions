"""Standalone intelligent review & practice system."""
import os
from flask import Flask
from practice import practice_bp

_project_root = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            static_folder=os.path.join(_project_root, 'static'),
            template_folder=os.path.join(_project_root, 'templates'))
app.register_blueprint(practice_bp, url_prefix='/practice')


@app.route('/')
def root():
    """Redirect root to practice."""
    from flask import redirect
    return redirect('/practice/')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)

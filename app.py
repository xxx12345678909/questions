"""Standalone intelligent review & practice system."""
from flask import Flask
from practice import practice_bp

app = Flask(__name__)
app.register_blueprint(practice_bp, url_prefix='/practice')


@app.route('/')
def root():
    """Redirect root to practice."""
    from flask import redirect
    return redirect('/practice/')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)

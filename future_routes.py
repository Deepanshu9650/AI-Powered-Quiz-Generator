from flask import Blueprint, render_template
from flask_login import login_required

# Create a blueprint named 'new_features'
new_features_bp = Blueprint('new_features', __name__)

# Example of a new route added in a separate file!
@new_features_bp.route('/experimental')
@login_required
def experimental_page():
    return "<h1>This is a new feature running from a separate file!</h1>"

from extensions import db 
class Question(db.Model):
    id = db.Column(db.Integer,primary_key=True)
    text = db.Column(db.Text,nullable=False)
    options = db.Column(db.Text,nullable=False)
    correct_answer = db.Column(db.String(100),nullable=False)
    explanation = db.Column(db.Text,nullable=True)
   
        
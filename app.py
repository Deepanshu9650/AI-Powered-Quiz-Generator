import os
import json
import PyPDF2 
import markdown
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
# Change this key for production use
app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_change_me")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quiz.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- LOGIN SETUP ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- GEMINI SETUP ---
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    print("WARNING: GEMINI_API_KEY not found in environment variables.")

# ------------------ DATABASE MODELS ------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    options = db.Column(db.Text, nullable=False) # Stored as "A||B||C||D"
    correct_answer = db.Column(db.String(300), nullable=False)
    explanation = db.Column(db.Text, nullable=True)

# NEW: Model to store Quiz Results (History)
class QuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    date_taken = db.Column(db.DateTime, default=datetime.utcnow)

# ------------------ HELPER FUNCTIONS ------------------
def extract_text_from_pdf(pdf_file):
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        # Limit to 20 pages to manage context window
        for i, page in enumerate(reader.pages):
            if i >= 20: break 
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return ""

def generate_quiz_questions(topic=None, source_text=None, qcount=5, difficulty="Medium"):
    # JSON Schema for the AI to follow
    json_structure = """
    [
        {
            "question": "Question text here",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": "Option B",
            "explanation": "Brief explanation here"
        }
    ]
    """

    if source_text:
        # PDF Context Mode
        prompt = (
            f"Generate {qcount} multiple-choice questions based STRICTLY on this text:\n"
            f"{source_text[:50000]}\n\n" # Context limit
            f"Difficulty: {difficulty}.\n"
            f"Return the output as a valid JSON array matching this structure:\n{json_structure}"
        )
    else:
        # Topic Mode
        prompt = (
            f"Generate {qcount} multiple-choice questions on the topic: '{topic}'.\n"
            f"Difficulty: {difficulty}.\n"
            f"Return the output as a valid JSON array matching this structure:\n{json_structure}"
        )

    try:
        # Force JSON response type
        response = model.generate_content(
            prompt, 
            generation_config={"response_mime_type": "application/json"}
        )
        return response.text.strip()
    except Exception as e:
        print("Error calling Gemini API:", e)
        return None

# ------------------ AUTH ROUTES ------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))

        # Create new user
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()

        # Log them in immediately
        login_user(new_user)
        flash('Account created successfully!', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Login failed. Check username and password.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ------------------ QUIZ ROUTES ------------------

@app.route('/')
@login_required 
def index():
    # Fetch user's history, newest first
    history = QuizResult.query.filter_by(user_id=current_user.id).order_by(QuizResult.date_taken.desc()).all()
    return render_template('index.html', user=current_user, history=history)

@app.route('/generate_quiz', methods=['POST'])
@login_required
def generate_quiz():
    q_limit = int(request.form.get('question_limit', 5))
    duration = int(request.form.get('duration', 60))
    difficulty = request.form.get('difficulty', 'Medium')
    
    session['duration'] = duration
    quiz_json_text = None

    # Handle Input Source & Save Topic
    if 'pdf_file' in request.files and request.files['pdf_file'].filename != '':
        file = request.files['pdf_file']
        pdf_text = extract_text_from_pdf(file)
        if pdf_text:
            session['last_topic'] = f"PDF: {file.filename}" # Save topic name for history
            quiz_json_text = generate_quiz_questions(source_text=pdf_text, qcount=q_limit, difficulty=difficulty)
    
    elif 'topic' in request.form and request.form['topic'].strip() != '':
        topic = request.form['topic']
        session['last_topic'] = topic # Save topic name for history
        quiz_json_text = generate_quiz_questions(topic=topic, qcount=q_limit, difficulty=difficulty)

    if not quiz_json_text:
        flash('Error: Could not generate quiz. Try a different topic or file.', 'danger')
        return redirect(url_for('index'))

    # Parse JSON and Save to DB
    try:
        questions_data = json.loads(quiz_json_text)
        
        # Clear old questions for this session
        Question.query.delete()
        
        for q in questions_data:
            options_list = q['options']
            correct = q['correct_answer']
            
            # Validation: Ensure answer is in options
            if correct not in options_list:
                correct = options_list[0]

            new_q = Question(
                text=q['question'],
                options="||".join(options_list), 
                correct_answer=correct,
                explanation=q.get('explanation', 'No explanation provided.')
            )
            db.session.add(new_q)
            
        db.session.commit()
        return redirect(url_for('quiz'))

    except json.JSONDecodeError:
        print("JSON Decode Error. Raw text:", quiz_json_text)
        flash('Error parsing AI response. Please try again.', 'danger')
        return redirect(url_for('index'))

@app.route('/quiz')
@login_required
def quiz():
    questions = Question.query.all()
    if not questions:
        return redirect(url_for('index'))
    return render_template('quiz.html', questions=questions, duration=session.get('duration', 60))

@app.route('/submit', methods=['POST'])
@login_required
def submit():
    questions = Question.query.all()
    score = 0
    results = []
    
    for q in questions:
        selected_option = request.form.get(str(q.id))
        is_correct = (selected_option == q.correct_answer)
        
        if is_correct: 
            score += 1
            
        results.append({
            'question': q.text,
            'selected': selected_option if selected_option else "No Answer",
            'correct': q.correct_answer,
            'is_correct': is_correct,
            'explanation': q.explanation,
            'options': q.options.split("||")
        })
    
    # --- SAVE RESULT TO HISTORY ---
    topic_name = session.get('last_topic', 'General Quiz')
    new_result = QuizResult(
        user_id=current_user.id,
        score=score,
        total_questions=len(questions),
        topic=topic_name
    )
    db.session.add(new_result)
    db.session.commit()
    # ------------------------------
    
    return render_template('result.html', score=score, total=len(questions), results=results)

@app.route('/quit')
@login_required
def quit_quiz():
    session.pop('duration', None)
    return redirect(url_for('index'))

@app.template_filter('markdown')
def markdown_filter(text):
    if text is None: return ""
    return markdown.markdown(text, extensions=['fenced_code', 'codehilite'])

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)